# DATALAKEHOUSE\_FP

**Final Project — Semester 4 Data Lakehouse**

Proyek ini membangun pipeline Data Lakehouse end-to-end berbasis arsitektur **Medallion (Bronze → Silver → Gold)** menggunakan tiga sumber data heterogen: data transaksional OLTP dari database AdventureWorks, data media sosial sintetis, dan dokumen invoice PDF. Seluruh data di-ingest melalui zona landing (`pool/`) sebelum masuk ke lapisan medallion.

---

## Arsitektur Sistem

```
   FACTORY                    OUTSIDE WORLD          LAKEHOUSE (hanya kenal pool/)
┌──────────────┐            ┌──────────────┐   ┌─────────┬─────────┬─────────┐
│ dummy_data/  │  move_to   │    pool/     │   │ Bronze  │ Silver  │  Gold   │
│ staging_     │ ─_pool.py─►│ OLTP (CSV)   │──►│ (asli)  │(Parquet)│(Parquet)│──► Power BI
│ extraction   │            │ social (JSON)│   │ CSV/PDF │ struktur│  star   │
│ (generate)   │            │ document(PDF)│   └─────────┴─────────┴─────────┘
└──────────────┘            └──────────────┘
```

**Boundary rule penting:** lapisan medallion (bronze/silver/gold) **hanya membaca dari `pool/`** — tidak pernah menyentuh `dummy_data/`. `dummy_data/` adalah *pabrik* yang men-generate data; `pool/` adalah *dunia luar / source system*.

| Tahap | Zona | Format | Deskripsi |
|-------|------|--------|-----------|
| Factory | `dummy_data/staging_extraction/` | CSV/JSON/PDF | Ekstraksi DB, generate tweet & invoice |
| Pool / Landing | `pool/` | mentah (CSV/JSON/PDF) | Data terpilih dipindah ke sini + `_manifest.json` (lineage) |
| Bronze | `medallion_layer/bronze/` | **format asli** (CSV/PDF) | Raw ingestion dari pool, belum direstrukturisasi |
| Silver | `medallion_layer/silver/` | **Parquet** | Typed, cleaned, deduped, derived columns |
| Gold | `medallion_layer/gold/` | **Parquet** | Star schema siap Power BI |

---

## Sumber Data

### 1. OLTP — AdventureWorks (PostgreSQL)

Database `adventureworks_local` (PostgreSQL 5432) berisi data penjualan sepeda fiktif dari Microsoft AdventureWorks.

**Schema `sales` — 19 tabel:**

| Tabel | Baris | Keterangan |
|-------|------:|------------|
| salesorderheader | 31,465 | Header transaksi; `onlineorderflag` membedakan online vs offline |
| salesorderdetail | 121,317 | Line item per transaksi |
| salesorderheadersalesreason | 27,647 | Alasan penjualan (hanya online) |
| customer | 19,820 | Data pelanggan |
| personcreditcard | 19,118 | Kartu kredit pelanggan |
| creditcard | 19,118 | Master kartu kredit |
| currencyrate | 13,532 | Kurs mata uang historis |
| specialofferproduct | 538 | Produk yang masuk promo |
| salespersonquotahistory | 163 | Kuota sales per periode |
| salestaxrate | 29 | Tarif pajak per wilayah |
| countryregioncurrency | 109 | Mata uang per negara |
| currency | 105 | Master mata uang |
| store | 0 | Data toko (kosong di dataset ini) |
| salesperson | 17 | Data tenaga penjual |
| salesterritoryhistory | 17 | Riwayat territory sales |
| salesterritory | 10 | Territory penjualan |
| salesreason | 10 | Master alasan penjualan |
| specialoffer | 16 | Penawaran spesial |
| shoppingcartitem | 3 | Item keranjang belanja |

**Schema `production`, `person`, `purchasing` — tabel pendukung:**

| Tabel | Schema | Subfolder Output | Baris |
|-------|--------|-----------------|------:|
| product | production | `product_and_sub/` | 504 |
| productsubcategory | production | `product_and_sub/` | 37 |
| productcategory | production | `product_and_sub/` | 4 |
| address | person | `address/` | 19,614 |
| person | person | `person/` | 0 |
| shipmethod | purchasing | `shipmethod/` | 5 |

**Split berdasarkan channel penjualan (`onlineorderflag`):**

| Tabel | Online (`true`) | Offline (`false`) |
|-------|---------------:|------------------:|
| salesorderheader | 27,659 | 3,806 |
| salesorderdetail | 60,398 | 60,919 |
| salesorderheadersalesreason | 27,647 | 0 |

### 2. Social Media — Synthetic Tweet Data

Tweet sintetis berbahasa campuran (EN/ID) yang disimulasikan sebagai data media sosial terkait produk AdventureWorks.

- **Volume:** 2,000 tweet
- **Periode:** 2026-06-01 s/d 2026-06-19 (19 hari)
- **Granularitas:** 1 file JSON per hari (~105–106 tweet/hari)
- **Format file:** `tweets_YYYY-MM-DD.json`
- **Konten:** user_id, username, timestamp, text (mention produk dari katalog), lang, hashtags

### 3. Document — Invoice PDF

PDF invoice yang di-generate dari data offline sales order (reseller/B2B).

- **Filter:** `onlineorderflag = false` → 3,806 qualifying orders
- **Output:** 1 file PDF (`invoices_onlineorderflag_false.pdf`, ~8 MB)
- **Konten per invoice:** nomor order, tanggal, detail item, subtotal, pajak, alamat pengiriman, nama sales rep, metode pengiriman

---

## Prerequisites

- Python 3.10+
- PostgreSQL berjalan di `localhost:5432`
- Database `adventureworks_local` sudah di-restore dari dataset AdventureWorks
- Kredensial default: user `postgres`, password `postgres` (ubah di script jika berbeda)

---

## Cara Menjalankan

### Cara termudah — `run.bat`

Double-click `run.bat` dari File Explorer, atau jalankan di terminal:

```powershell
.\run.bat
```

Script ini otomatis:
1. Membuat virtual environment `.venv` jika belum ada
2. Menginstall semua dependencies dari `requirements.txt`
3. Menjalankan full pipeline (`dummy_data/run_extractions.py`)

Tidak perlu aktivasi venv manual.

### Menjalankan pipeline secara manual

```powershell
.venv\Scripts\python.exe dummy_data\run_extractions.py
```

---

## Pipeline Detail

`run_extractions.py` mengorkestrasi lima step berurutan:

### Step 1 — Extract schema `sales`

```powershell
.venv\Scripts\python.exe dummy_data\staging_extraction\extract_sales.py
```

Menyambung ke PostgreSQL, auto-discover semua tabel di schema `sales`, lalu mengekspor setiap tabel ke CSV terpisah dengan timestamp.

Output: `dummy_data/staging_extraction/<table>/<table>_YYYYMMDDHHMM.csv`

### Step 1b — Extract tabel pendukung

```powershell
.venv\Scripts\python.exe dummy_data\staging_extraction\extract_production.py
```

Mengekstrak tabel dari schema `production` (product, subcategory, category), `person` (address, person), dan `purchasing` (shipmethod).

Output: `dummy_data/staging_extraction/<subfolder>/<table>_YYYYMMDDHHMM.csv`

### Step 1c — Split order berdasarkan channel

```powershell
.venv\Scripts\python.exe dummy_data\staging_extraction\split_by_channel.py
```

Membaca `salesorderheader` terbaru, memisahkan baris berdasarkan nilai kolom `onlineorderflag`, lalu mem-filter tabel terkait (`salesorderdetail`, `salesorderheadersalesreason`) menggunakan `salesorderid` yang cocok.

Output:
- `dummy_data/staging_extraction/online_store_csv/<table>/<table>_online_YYYYMMDDHHMM.csv`
- `dummy_data/staging_extraction/offline_store_csv/<table>/<table>_offline_YYYYMMDDHHMM.csv`

### Step 2 — Generate tweet sintetis

```powershell
.venv\Scripts\python.exe dummy_data\tweetgenerate\generate_tweets.py `
    --count 2000 --start-date 2026-06-01 --end-date 2026-06-19 `
    --lang mixed --split day --output dummy_data\tweetgenerate\output `
    --products dummy_data\staging_extraction\product_and_sub\<product_file>.csv
```

Menggunakan katalog produk dari step 1b sebagai referensi nama produk dalam tweet.

Output: `dummy_data/tweetgenerate/output/tweets_YYYY-MM-DD.json`

### Step 3 — Generate invoice PDF

```powershell
.venv\Scripts\python.exe dummy_data\generate_invoice\awc_invoices.py `
    --header  dummy_data\staging_extraction\offline_store_csv\salesorderheader\<file>.csv `
    --detail  dummy_data\staging_extraction\offline_store_csv\salesorderdetail\<file>.csv `
    --product dummy_data\staging_extraction\product_and_sub\<file>.csv `
    --output-dir dummy_data\generate_invoice\output_invoices
```

Menggunakan output **pre-filtered** dari step 1c (`offline_store_csv`) — hanya 3,806 header rows dan 60,919 detail rows yang relevan, bukan seluruh dataset (31,465 / 121,317). File CSV dipilih otomatis oleh `run_extractions.py` (latest timestamp per tabel). Tabel pendukung (customer, address, product, shipmethod, dll.) tetap diambil dari folder ekstraksi lengkap.

Output: `dummy_data/generate_invoice/output_invoices/invoices_onlineorderflag_false.pdf`

---

## Data Lakehouse (Medallion) — `src/`

Setelah `dummy_data/` (factory) menghasilkan data, pipeline lakehouse memprosesnya lewat `pool/` → bronze → silver → gold → model. Semua kode ada di `src/` dan **hanya membaca dari `pool/`** (tidak pernah menyentuh `dummy_data/`).

### Aturan aliran data

- **`dummy_data/` → `pool/` = COPY** — factory tetap menyimpan arsipnya.
- **`pool/` → `bronze/` = MOVE (drain)** — begitu bronze menarik file, file itu **hilang dari pool**. Pool adalah inbox transient. (`_manifest.json` tetap sebagai log lineage.)
- **Bronze dikelompokkan by FORMAT** (`bronze/csv/`, `bronze/pdf/`, `bronze/json/`), bukan by source.

### Urutan menjalankan

```powershell
# 0. Factory → pool (COPY data terpilih + manifest lineage)
.venv\Scripts\python.exe src\move_to_pool.py

# 1. Lakehouse: pool → bronze(drain) → silver → gold → model  (semua branch)
.venv\Scripts\python.exe src\build_lakehouse.py
```

> Karena bronze menguras pool, jalankan `move_to_pool.py` lagi sebelum build berikutnya (mengisi ulang pool dari factory tanpa generate ulang).

### Branch A — Sales Data Warehouse (Scenario 1)

`bronze/csv` → `silver/sales` (Parquet bersih, + `line_total` turunan) → `gold/sales` (star schema) → `model/sales` (siap DW). Star schema:

| Tabel | Baris | Keterangan |
|-------|------:|------------|
| `fact_sales` | 121,317 | Grain: 1 produk per order line. FK: date/customer/product/channel. Measure: `order_qty, unit_price, unit_price_discount, line_total, sales_count` |
| `dim_date` | 1,124 | `date_key (YYYYMMDD), full_date, day, month, month_name, quarter, year` |
| `dim_customer` | 19,820 | `customer_key, customer_id, customer_type (Individual/Store/Unknown), territory_id` |
| `dim_product` | 504 | `product_key, product_id, product_name, category, subcategory, standard_cost, list_price, ...` |
| `dim_channel` | 2 | `channel_key (1=Online, 2=Offline), channel_name` |

> `line_total` dihitung (`order_qty × unit_price × (1 − unit_price_discount)`) karena kolom ini *computed* di AdventureWorks dan tidak ikut terekspor.

### Branch B — Document (PDF tak terstruktur → terstruktur)

`bronze/pdf` → `silver/document` (Parquet). Demonstrasi pemrosesan data tak terstruktur memakai `pdfplumber`:

| Tabel | Baris | Keterangan |
|-------|------:|------------|
| `invoice_header` | 3,806 | 1 baris/invoice: salesorderid, po, tanggal, total, `truncated_flag`, `source_page` |
| `invoice_line` | 51,258 | 1 baris/line item (best-effort) |

> Order yang sangat panjang dipotong di PDF ("… additional line items truncated …") sehingga line item bersifat *best-effort* (752 invoice ter-truncate); header & total selalu lengkap. Validasi: `total_due` PDF cocok 100% dengan CSV sumber (selisih maks $0.005, rounding), dan line count 100% match untuk 3,054 invoice non-truncate.

---

## Gold vs Model vs Data Warehouse — & cara analisa Power BI

Sumber kebingungan umum: **"data warehouse" dan "Parquet" bukan dua pilihan** —
- **Data warehouse** = *model*-nya (star schema: `dim_*` + `fact_sales` dengan relationship). Ini desain logis.
- **Parquet** = *format penyimpanan*-nya (file kolumnar efisien).

Jadi tiga konsep di proyek ini:

| Layer | Isi | Peran |
|-------|-----|-------|
| **gold/** | Semua `dim_*` + `fact_sales` Parquet hasil lakehouse | Data analitik. Tidak semua tabel harus dikirim ke DW. |
| **model/** | dim/fact **terpilih** yang final + `schema.json` + `create_tables.sql` | Star schema relational-ready, **siap di-ship ke Data Warehouse**. |
| **Data Warehouse** | Tabel relational hasil load `model/` (mis. Postgres) | Tujuan akhir (opsional; DDL sudah disediakan). |

**Power BI fleksibel** — pilih salah satu:
1. **Import Parquet langsung** dari `gold/sales/` atau `model/sales/` (Get Data → Folder/Parquet). Tanpa DB server. Surrogate key integer → relationship dim→fact auto-detect.
2. **Connect ke Data Warehouse** (setelah `model/` di-load ke DB) via SQL connector.

### Mapping business question → schema → visual Power BI

| Business question | Kolom dipakai | Visual | DAX |
|-------------------|---------------|--------|-----|
| Tren revenue per bulan/tahun | `dim_date[year, month_name]` × measure | Line chart | `Revenue = SUM(fact_sales[line_total])` |
| Online vs Offline | `dim_channel[channel_name]` × Revenue | Donut / Bar | `Revenue` (di atas) |
| Produk / kategori terlaris | `dim_product[product_name / category]` × Revenue | Bar (Top N) | `Units = SUM(fact_sales[order_qty])` |
| Individual vs Store | `dim_customer[customer_type]` × Revenue | Stacked bar | `Orders = SUM(fact_sales[sales_count])` |

Pola umum: **dim_* = sumbu/slicer/legend**, **fact_sales = measure** (agregasi `line_total`, `order_qty`, `sales_count`).

---

## Dependencies

```
pandas            # transform & IO
psycopg2-binary   # koneksi PostgreSQL (factory)
reportlab         # generate invoice PDF (factory)
pyarrow           # baca/tulis Parquet (silver & gold)
pdfplumber        # ekstraksi teks PDF (document silver)
```

Install manual (tanpa `run.bat`):

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

---

## Struktur Proyek

```
DATALAKEHOUSE_FP/
├── run.bat                                      # Entry point — jalankan ini
├── requirements.txt
├── pyproject.toml
│
├── dummy_data/                                  # Zona data mentah & generator
│   ├── run_extractions.py                       # Master pipeline (step 1–3)
│   │
│   ├── staging_extraction/                      # Output ekstraksi OLTP
│   │   ├── extract_sales.py                    # Step 1  : sales schema → CSV
│   │   ├── extract_production.py               # Step 1b : production/person/purchasing → CSV
│   │   ├── split_by_channel.py                 # Step 1c : pisah online / offline
│   │   │
│   │   ├── salesorderheader/                   # Full header (semua 31,465 baris)
│   │   ├── salesorderdetail/                   # Full detail (semua 121,317 baris)
│   │   ├── salesorderheadersalesreason/        # Sales reason (semua 27,647 baris)
│   │   ├── customer/
│   │   ├── product_and_sub/                    # product + productsubcategory + productcategory
│   │   ├── address/
│   │   ├── person/
│   │   ├── shipmethod/
│   │   ├── salesperson/
│   │   ├── salesterritory/
│   │   ├── <tabel lainnya>/                    # 10 tabel sales lainnya
│   │   │
│   │   ├── online_store_csv/                   # Split hasil step 1c
│   │   │   ├── salesorderheader/               # 27,659 baris
│   │   │   ├── salesorderdetail/               # 60,398 baris
│   │   │   └── salesorderheadersalesreason/    # 27,647 baris
│   │   │
│   │   └── offline_store_csv/                  # Split hasil step 1c
│   │       ├── salesorderheader/               # 3,806 baris
│   │       ├── salesorderdetail/               # 60,919 baris
│   │       └── salesorderheadersalesreason/    # 0 baris
│   │
│   ├── tweetgenerate/
│   │   ├── generate_tweets.py                  # Step 2 : tweet generator
│   │   └── output/                             # tweets_YYYY-MM-DD.json (19 file)
│   │
│   └── generate_invoice/
│       ├── awc_invoices.py                     # Step 3 : invoice PDF generator
│       └── output_invoices/                    # invoices_onlineorderflag_false.pdf
│
├── src/                                         # Pipeline lakehouse (baca dari pool/ saja)
│   ├── move_to_pool.py                          # Factory → pool (COPY) + manifest lineage
│   ├── bronze.py                                # Shared: drain pool → bronze/{csv,pdf,json} (MOVE)
│   ├── build_lakehouse.py                       # Orchestrator utama (bronze→silver→gold→model)
│   ├── sales_dw/                                # Branch A — CSV → star schema → model
│   │   ├── silver.py  gold.py  model.py
│   │   └── build_sales_dw.py                    # silver→gold→model
│   └── document_dw/                             # Branch B — PDF → terstruktur
│       ├── silver.py
│       └── build_document_dw.py                 # silver only
│
├── pool/                                        # OUTSIDE WORLD (transient, FLAT, terkuras)
│   ├── _manifest.json                           # audit lineage tiap file (tetap walau pool kosong)
│   ├── OLTP/*.csv                               # flat, tanpa folder per-tabel
│   ├── social_media/tweets_*.json
│   └── document/invoices_onlineorderflag_false.pdf
│
├── medallion_layer/
│   ├── bronze/                                  # BY FORMAT (hasil drain pool)
│   │   ├── csv/*.csv                            # raw OLTP (nama kanonik, format asli)
│   │   ├── pdf/*.pdf                            # raw PDF
│   │   └── json/*.json                          # raw tweet (stage utk Scenario 2)
│   ├── silver/
│   │   ├── sales/*.parquet                      # typed, deduped, + line_total
│   │   └── document/                            # invoice_header / invoice_line .parquet
│   └── gold/
│       └── sales/                               # dim_* + fact_sales .parquet
│
└── model/                                       # DW-READY (jembatan ke Data Warehouse)
    └── sales/
        ├── dim_*.parquet  fact_sales.parquet    # star schema final (relational-ready)
        ├── schema.json                          # kolom+tipe, PK, FK
        └── create_tables.sql                    # DDL load ke DW
```

---

## Konvensi Penamaan File

| Tipe | Pola | Contoh |
|------|------|--------|
| OLTP full | `<table>_YYYYMMDDHHMM.csv` | `salesorderheader_202606191313.csv` |
| OLTP split online | `<table>_online_YYYYMMDDHHMM.csv` | `salesorderheader_online_202606191313.csv` |
| OLTP split offline | `<table>_offline_YYYYMMDDHHMM.csv` | `salesorderheader_offline_202606191313.csv` |
| Social media | `tweets_YYYY-MM-DD.json` | `tweets_2026-06-01.json` |
| Document | `invoices_onlineorderflag_false.pdf` | — |

Jika pipeline dijalankan ulang, file lama tidak ditimpa — file baru dibuat dengan timestamp baru. Script selalu membaca file dengan timestamp terbaru (lexicographic sort).
