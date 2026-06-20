# DATALAKEHOUSE\_FP

**Final Project — Semester 4 Data Lakehouse**

Proyek ini membangun pipeline Data Lakehouse end-to-end berbasis arsitektur **Medallion (Bronze → Silver → Gold)** dari tiga sumber data heterogen — data transaksional OLTP (AdventureWorks), data media sosial sintetis (tweet berlabel sentimen), dan dokumen invoice PDF — lalu memodelkannya menjadi **galaxy schema (fact constellation)** dengan dua fact yang berbagi dimensi (`fact_sales` + `fact_sentiment`), dan akhirnya **dimuat ke Data Warehouse PostgreSQL** (`warehouseDB`, schema `dw_sales`). Seluruh data masuk lewat zona landing (`pool/`) sebelum medallion.

---

## Arsitektur Sistem

```
   FACTORY                 OUTSIDE WORLD        LAKEHOUSE (hanya kenal pool/)             DATA WAREHOUSE
┌──────────────┐  move_to  ┌────────────┐  ┌────────┬─────────┬──────────────┬────────┐  ┌──────────────┐
│ dummy_data/  │ ─_pool.py►│   pool/    │─►│ Bronze │ Silver  │ Gold (galaxy)│ model/ │─►│ PostgreSQL   │
│ - OLTP CSV   │   COPY    │ OLTP (CSV) │  │ (asli) │(Parquet)│ fact_sales + │  DW-   │  │ warehouseDB  │
│ - tweet JSON │           │ social JSON│  │ by     │ typed,  │ fact_sentiment│ ready │  │ schema       │
│ - invoice PDF│           │ doc  PDF   │  │ format │ cleaned │ + shared dims│ +DDL   │  │ dw_sales     │
└──────────────┘           └────────────┘  └────────┴─────────┴──────────────┴────────┘  └──────────────┘
                                                                       │
                                                                       └──► Power BI (import Parquet langsung)
```

**Boundary rule penting:** lapisan medallion (bronze/silver/gold) **hanya membaca dari `pool/`** — tidak pernah menyentuh `dummy_data/`. `dummy_data/` adalah *pabrik* yang men-generate data; `pool/` adalah *dunia luar / source system*.

| Tahap | Zona | Format | Deskripsi |
|-------|------|--------|-----------|
| Factory | `dummy_data/` | CSV/JSON/PDF | Ekstraksi DB, generate tweet & invoice |
| Pool / Landing | `pool/` | mentah (CSV/JSON/PDF) | Data terpilih dipindah ke sini + `_manifest.json` (lineage) |
| Bronze | `medallion_layer/bronze/` | **format asli** (CSV/PDF/JSON) | Raw ingestion by FORMAT, belum direstrukturisasi |
| Silver | `medallion_layer/silver/` | **Parquet** | Typed, cleaned, deduped, derived & analyzed |
| Gold | `medallion_layer/gold/` | **Parquet** | **Galaxy schema** (2 fact + dim konform) |
| Model | `model/` | **Parquet + DDL** | dim/fact final relational-ready, siap ship ke DW |
| Data Warehouse | `warehouseDB.dw_sales` | **Tabel PostgreSQL** | Hasil load `model/` (PK + FK), siap dikueri |

---

## Sumber Data

### 1. OLTP — AdventureWorks (PostgreSQL)

Database `adventureworks_local` (PostgreSQL 5432). Schema `sales` (19 tabel) + tabel pendukung `production` (product/subcategory/category), `person` (address/person), `purchasing` (shipmethod).

**Split berdasarkan channel penjualan (`onlineorderflag`):**

| Tabel | Online (`true`) | Offline (`false`) |
|-------|---------------:|------------------:|
| salesorderheader | 27,659 | 3,806 |
| salesorderdetail | 60,398 | 60,919 |
| salesorderheadersalesreason | 27,647 | 0 |

> Channel ini menentukan dari mana data sales diambil di silver: **online → CSV**, **offline → PDF invoice** (lihat *Branch A*).

### 2. Social Media — Synthetic Tweet (berlabel sentimen)

Tweet sintetis EN/ID terkait produk AdventureWorks, **sudah membawa ground-truth label** di blok `_meta`.

- **Volume:** 2,000 tweet · **Periode:** 2026-06-01 s/d 2026-06-19 · **Format:** `tweets_YYYY-MM-DD.json` (1 file/hari)
- **Field utama:** `id_str, created_at, text, lang, user{screen_name, followers_count, verified}, retweet_count, favorite_count, source`
- **`_meta`:** `sentiment` (positive/neutral/negative), `aspect` (kualitas/pengiriman/harga/layanan/durabilitas/umum), `product`, `category`, `subcategory`, `product_bias`, `spike`

### 3. Document — Invoice PDF

PDF invoice yang di-generate dari **offline sales order** (`onlineorderflag = false`, 3,806 order).

- **Output:** 1 file PDF (`invoices_onlineorderflag_false.pdf`), 1 halaman = 1 invoice
- **Konten per invoice:** nomor order, tanggal, line item, subtotal, pajak, freight, total, territory, sales rep
- Order sangat panjang dipotong (`… additional line items truncated …`) → line item *best-effort*, header/total selalu lengkap.

---

## Alur Pipeline (DAG)

Urutan stage mengkodekan dependensi data. **Offline sales diambil dari PDF**, jadi *document silver wajib jalan sebelum sales silver*; gold (galaxy) butuh **kedua** silver fact.

```
move_to_pool.py        dummy_data/ ──COPY──► pool/  (+ _manifest.json)
└─ build_lakehouse.py
   ├─ bronze.py              pool/ ──MOVE(drain)──► bronze/{csv,pdf,json}
   ├─ document_dw/silver.py  bronze/pdf  ──► silver/document/invoice_{header,line}.parquet
   ├─ sales_dw/silver.py     bronze/csv + silver/document ──► silver/sales/ (base + sales.parquet)
   ├─ social_dw/silver.py    bronze/json ──► silver/social/sentiment.parquet
   ├─ sales_dw/gold.py       silver/sales + silver/social ──► gold/  (galaxy: 6 dim + 2 fact)
   └─ sales_dw/model.py      gold/ ──► model/ (parquet + schema.json + create_tables.sql)

load_warehouse.py      model/ ──► warehouseDB.dw_sales  (CREATE TABLE + COPY, PK/FK)
```

### Aturan aliran data

- **`dummy_data/` → `pool/` = COPY** — factory tetap menyimpan arsipnya.
- **`pool/` → `bronze/` = MOVE (drain)** — file hilang dari pool begitu ditarik bronze (`_manifest.json` tetap sebagai log lineage).
- **Bronze dikelompokkan by FORMAT** (`bronze/csv|pdf|json`), bukan by source.
- Karena bronze menguras pool, jalankan `move_to_pool.py` lagi sebelum build berikutnya.

---

## Gold — Galaxy Schema (Fact Constellation)

Dua fact pada grain berbeda berbagi **dimensi konform**. Jembatan lintas-fact utama adalah **`dim_product`** (+ hierarki kategori). Semua dim yang dirujuk fact memiliki anggota **Unknown (`key = -1`)** agar tidak pernah orphan.

> **Prinsip fact tabel:** fact **hanya berisi surrogate key + measure**, tidak ada teks deskriptif. Satu-satunya pengecualian adalah *degenerate key* (`sales_order_id`, `tweet_id`) — identitas natural pada grain. Semua atribut teks dipindah ke dimensi.

```
   dim_customer  dim_channel  dim_source              dim_author  dim_tweet_context
          \          |          /                          \           /
           \         |         /                            \         /
   dim_date ─────  fact_sales  ─── dim_product ─── fact_sentiment  ───── dim_date
                                   (shared)                \         /
                                                        dim_aspect  dim_sentiment
```

| Tabel | Baris | Peran | Keterangan |
|-------|------:|-------|------------|
| `dim_date` | 1,144 | konform | `date_key (YYYYMMDD)`, full_date, day, month, month_name, quarter, year (union tanggal sales + tweet) |
| `dim_product` | 505 | konform | product_key, product_id, product_name, product_number, category, subcategory, cost, price |
| `dim_customer` | 19,821 | sales | customer_key, customer_id, customer_type (Individual/Store/Unknown), territory_id |
| `dim_channel` | 2 | sales | channel_key (1=Online, 2=Offline), channel_name |
| `dim_source` | 3 | sales | source_key, source_type (csv_online / pdf_offline) — provenance ingestion |
| `dim_aspect` | 6 | sentiment | aspect_key, aspect_name (Quality/Delivery/Price/Service/Durability/General) |
| `dim_sentiment` | 3 | sentiment | sentiment_key, sentiment_label, sentiment_score (+1/0/−1) |
| `dim_author` | 2,001 | sentiment | author_key, screen_name, verified — profil penulis tweet |
| `dim_tweet_context` | 7 | sentiment | context_key, lang, source_app — junk dim (bahasa + aplikasi posting) |
| `fact_sales` | 111,656 | fact | Grain: 1 line item. FK: date/customer/product/channel/**source**. Measure: order_qty, unit_price, unit_price_discount, line_total, sales_count. Degenerate key: `sales_order_id` |
| `fact_sentiment` | 2,000 | fact | Grain: 1 tweet. FK: date/product/aspect/sentiment/**author/context**. Measure: followers_count, favorite_count, retweet_count, engagement_total, sentiment_score, tweet_count, flag is_spike. Degenerate key: `tweet_id` |

> **Catatan keterbatasan data:** tanggal sales AdventureWorks bersifat historis (~2011–2014) sedangkan tweet sintetis di 2026-06, sehingga `dim_date` **tidak overlap** antar-fact. Analisa lintas-fact yang sahih menggunakan **product/kategori**, bukan waktu.

---

## Branch Silver — detail

### Branch A — Sales (unified: online CSV ∪ offline PDF) → `silver/sales/sales.parquet`

`sales_dw/silver.py` menghasilkan tabel base typed (header, detail, customer, product, dst.) **dan** satu fact sales tergabung pada grain line item:

- **Online** ← `bronze/csv` (`onlineorderflag = true`) join detail; `product_id` membawa `product_number`/`product_name`.
- **Offline** ← `silver/document/invoice_line` join `invoice_header`; `product_id` di-resolve via `product_number → product.productnumber` (key bersih).
- Kolom konform: `salesorderid, salesorderdetailid, order_date, ship_date, customer_id, product_id, product_number, product_name, order_qty, unit_price, unit_price_discount, line_total, channel, source_type, truncated_flag`.

> Tidak ada double-count (online hanya dari CSV, offline hanya dari PDF). **Trade-off (by design):** offline memakai PDF, jadi 752 invoice yang ter-truncate kehilangan sebagian line item (header/total tetap akurat, ditandai `truncated_flag`). Itu sebabnya `fact_sales` = 111,656 (60,398 online + 51,258 offline), bukan total CSV penuh.

### Branch B — Document (PDF tak terstruktur → terstruktur) → `silver/document/`

`document_dw/silver.py` mem-parse invoice PDF dengan `pdfplumber`:

| Tabel | Baris | Keterangan |
|-------|------:|------------|
| `invoice_header` | 3,806 | 1 baris/invoice: salesorderid, po, tanggal, total, `truncated_flag`, `source_page` (1-based) |
| `invoice_line` | 51,258 | 1 baris/line item (best-effort; 752 invoice ter-truncate) |

> Hasil ini menjadi **sumber sisi offline** untuk `sales.parquet` (Branch A).

### Branch C — Social (tweet → sentimen terstruktur) → `silver/social/sentiment.parquet`

`social_dw/silver.py` mem-flatten tweet + `_meta`, dedupe `tweet_id`, lalu menambah fitur analitik: `sentiment_score` (+1/0/−1), `aspect_en` (Indonesia→Inggris), `engagement_total`, `event_date`, `clean_text` (emoji/mention/URL dibersihkan). Output 1 baris/tweet (2,000).

---

## Data Warehouse — load `model/` → PostgreSQL

`src/load_warehouse.py` mempromosikan output lakehouse (`model/`) menjadi DW relational nyata:

1. **Ensure database** `warehouseDB` (dibuat otomatis jika belum ada).
2. **(Re)create schema** `dw_sales` (drop + rebuild — idempotent).
3. **CREATE TABLE** untuk 6 dim + 2 fact (PK + FK, schema-qualified) dari `model/schema.json`.
4. **Bulk-load** tiap Parquet via `COPY` (dim dulu, lalu fact).
5. **Verify** row count + 1 query lintas-fact (revenue × avg sentiment per kategori).

```powershell
.venv\Scripts\python.exe src\load_warehouse.py
```

> Saat join dua fact, **agregasikan tiap fact ke grain kategori dulu lalu join** — join langsung `fact_sales`×`fact_sentiment` pada `product_key` menyebabkan fan-out dan menggelembungkan revenue. Query verifikasi di skrip sudah memakai pola CTE yang benar.

---

## Control Bridge & Reset

### Bridge — `src/datalakehouse.py` (satu pintu untuk semua service)

```powershell
.venv\Scripts\python.exe src\datalakehouse.py                 # menu interaktif (loop)
.venv\Scripts\python.exe src\datalakehouse.py warehouse.create # jalankan 1 action (key)
.venv\Scripts\python.exe src\datalakehouse.py 9                # ...atau by nomor
.venv\Scripts\python.exe src\datalakehouse.py list             # tampilkan menu lalu keluar
.venv\Scripts\python.exe src\datalakehouse.py lake.reset --yes # auto-confirm action destruktif
```

| Action | Fungsi |
|--------|--------|
| `dummy.create` | Generate dummy data (butuh `adventureworks_local`) |
| `dummy.reset` | Hapus output `*.csv/*.json/*.pdf` (generator `.py` aman) |
| `lake.move` / `lake.build` / `lake.create` | Move ke pool / build lakehouse / keduanya |
| `pool.reset` / `medallion.reset` / `lake.reset` | Reset pool / medallion+model / keduanya |
| `warehouse.create` / `warehouse.reset` | Load `model/` → DW / drop schema `dw_sales` |
| `full.rebuild` | move → build lakehouse → load warehouse |

Bridge melakukan **preflight koneksi DB** (timeout 3 dtk) untuk action yang butuh database dan menggagalkannya cepat dengan pesan jelas; action destruktif minta konfirmasi (interaktif) atau wajib `--yes` (non-interaktif).

### Reset scripts (dapat dipanggil langsung)

Tiap reset mendukung `--dry-run` (lihat target tanpa menghapus), `--yes` (skip prompt), default prompt.

| Script | Yang direset | Yang dijaga |
|--------|--------------|-------------|
| `src/reset_dummy_data.py` | output `*.csv/*.json/*.pdf` + `__pycache__` | semua generator `.py` |
| `src/reset_pool.py` | isi `pool/` (OLTP/social/document + manifest) | folder `pool/` |
| `src/reset_medallion.py` | isi `medallion_layer/` + `model/` | dua folder root |
| `src/reset_warehouse.py` | schema `dw_sales` (`--schema` ubah target) | database `warehouseDB` |

---

## Prerequisites

- Python 3.10+
- PostgreSQL di `localhost:5432`, user `postgres` / password `postgres` (ubah di script jika berbeda)
- Database **sumber** `adventureworks_local` sudah di-restore (untuk `dummy.create`)
- Database **warehouse** `warehouseDB` — **otomatis dibuat** oleh `load_warehouse.py` bila belum ada

---

## Cara Menjalankan

### Termudah — lewat bridge

```powershell
.venv\Scripts\python.exe src\datalakehouse.py
# pilih: 5 (lake.create) -> 9 (warehouse.create)   atau langsung 11 (full.rebuild)
```

### Manual (granular)

```powershell
.venv\Scripts\python.exe src\move_to_pool.py        # factory -> pool (COPY + manifest)
.venv\Scripts\python.exe src\build_lakehouse.py     # pool -> bronze -> silver -> gold -> model
.venv\Scripts\python.exe src\load_warehouse.py      # model -> warehouseDB.dw_sales
```

> Untuk meng-generate ulang dummy data dari awal: `.venv\Scripts\python.exe dummy_data\run_extractions.py` (butuh `adventureworks_local`). `run.bat` juga menjalankan langkah ini.

---

## Analisa Power BI

Surrogate key integer membuat relationship dim→fact auto-detect. Pilih salah satu sumber:
1. **Import Parquet langsung** dari `medallion_layer/gold/` atau `model/` (Get Data → Folder/Parquet) — tanpa DB server.
2. **Connect ke Data Warehouse** `warehouseDB.dw_sales` via konektor PostgreSQL (setelah `load_warehouse.py`).

| Business question | Kolom | Pola |
|-------------------|-------|------|
| Revenue per kategori/produk | `dim_product[category/product_name]` × `fact_sales[line_total]` | `Revenue = SUM(fact_sales[line_total])` |
| Online vs Offline | `dim_channel[channel_name]` × Revenue | bar/donut |
| Revenue × sentimen (lintas-fact) | `dim_product[category]`, agregasi `fact_sales` & `fact_sentiment` terpisah lalu gabung | quadrant champions/at-risk |
| Pain point per aspek | `dim_aspect[aspect_name]` × `fact_sentiment` (negatif) | bar |
| Spike (recall) | `fact_sentiment[is_spike]` × produk/kategori | tabel |

Pola umum: **dim_* = sumbu/slicer/legend**, **fact = measure**. Jangan join dua fact baris-ke-baris pada `product_key` (fan-out) — agregasikan ke grain kategori dulu.

---

## Struktur Proyek

```
DATALAKEHOUSE_FP/
├── run.bat · requirements.txt · pyproject.toml
│
├── dummy_data/                                  # FACTORY — generator + output
│   ├── run_extractions.py                       # master generator (step 1–3)
│   ├── staging_extraction/
│   │   ├── extract_sales.py · extract_production.py · split_by_channel.py
│   │   ├── <tabel>/                             # CSV per tabel (full)
│   │   ├── online_store_csv/  · offline_store_csv/   # hasil split per channel
│   ├── tweetgenerate/  generate_tweets.py · output/tweets_*.json
│   └── generate_invoice/ awc_invoices.py · output_invoices/*.pdf
│
├── src/                                         # LAKEHOUSE + DW + tooling (baca pool/ saja)
│   ├── datalakehouse.py                         # ★ control bridge (menu semua service)
│   ├── move_to_pool.py                          # factory -> pool (COPY) + manifest
│   ├── bronze.py                                # drain pool -> bronze/{csv,pdf,json} (MOVE)
│   ├── build_lakehouse.py                       # orchestrator (bronze->silver->gold->model)
│   ├── load_warehouse.py                        # model/ -> warehouseDB.dw_sales
│   ├── reset_dummy_data.py · reset_pool.py · reset_medallion.py · reset_warehouse.py
│   ├── sales_dw/                                # silver(+sales.parquet) · gold(galaxy) · model
│   │   ├── silver.py · gold.py · model.py · build_sales_dw.py
│   ├── document_dw/                             # PDF -> silver/document
│   │   ├── silver.py · build_document_dw.py
│   └── social_dw/                               # tweet -> silver/social/sentiment
│       ├── silver.py · build_social_dw.py
│
├── pool/                                        # OUTSIDE WORLD (transient, FLAT, terkuras)
│   ├── _manifest.json · OLTP/*.csv · social_media/tweets_*.json · document/*.pdf
│
├── medallion_layer/
│   ├── bronze/{csv,pdf,json}/                   # raw by FORMAT (hasil drain pool)
│   ├── silver/
│   │   ├── sales/    *.parquet + sales.parquet  # base typed + unified sales fact
│   │   ├── document/ invoice_{header,line}.parquet
│   │   └── social/   sentiment.parquet
│   └── gold/                                    # GALAXY (flat): dim_*.parquet + fact_sales/fact_sentiment
│
└── model/                                       # DW-READY (flat, jembatan ke warehouseDB)
    ├── dim_*.parquet · fact_sales.parquet · fact_sentiment.parquet
    ├── schema.json                             # kolom+tipe, PK, FK (galaxy)
    └── create_tables.sql                       # DDL load ke DW (PK + FK)
```

---

## Konvensi Penamaan File

| Tipe | Pola | Contoh |
|------|------|--------|
| OLTP full | `<table>_YYYYMMDDHHMM.csv` | `salesorderheader_202606191313.csv` |
| OLTP split online/offline | `<table>_online|offline_YYYYMMDDHHMM.csv` | `salesorderheader_offline_202606191313.csv` |
| Social media | `tweets_YYYY-MM-DD.json` | `tweets_2026-06-01.json` |
| Document | `invoices_onlineorderflag_false.pdf` | — |
| Bronze CSV | nama kanonik (timestamp di-strip) | `salesorderheader.csv` |

File lama tidak ditimpa saat re-run factory — file baru dibuat dengan timestamp baru; script selalu membaca timestamp terbaru (lexicographic sort).
