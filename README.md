# DATALAKEHOUSE\_FP

**Final Project — Semester 4 Data Lakehouse**

Proyek ini membangun pipeline Data Lakehouse end-to-end berbasis arsitektur **Medallion (Bronze → Silver → Gold)** menggunakan tiga sumber data heterogen: data transaksional OLTP dari database AdventureWorks, data media sosial sintetis, dan dokumen invoice PDF. Seluruh data di-ingest melalui zona landing (`pool/`) sebelum masuk ke lapisan medallion.

---

## Arsitektur Sistem

```
PostgreSQL (AdventureWorks)  ──┐
Synthetic Tweet Generator     ─┼──► dummy_data/  ──► pool/  ──► Bronze ──► Silver ──► Gold
Invoice PDF Generator         ─┘
```

Data mengalir dalam lima tahap:

| Tahap | Zona | Deskripsi |
|-------|------|-----------|
| Dummy Data Generation | `dummy_data/` | Ekstraksi dari DB, generate tweet & invoice |
| Pool / Landing Zone | `pool/` | Titik masuk tunggal sebelum medallion; berisi manifest audit |
| Bronze | `medallion_layer/bronze/` | Raw ingestion — data disimpan apa adanya |
| Silver | `medallion_layer/silver/` | Cleaning, deduplication, type casting |
| Gold | `medallion_layer/gold/` | Agregasi & data siap analitik / reporting |

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
    --header  dummy_data\staging_extraction\salesorderheader\<file>.csv `
    --detail  dummy_data\staging_extraction\salesorderdetail\<file>.csv `
    --product dummy_data\staging_extraction\product_and_sub\<file>.csv `
    --output-dir dummy_data\generate_invoice\output_invoices
```

Script secara otomatis memfilter baris dengan `onlineorderflag = false`. File CSV dipilih otomatis oleh `run_extractions.py` (latest timestamp per tabel).

Output: `dummy_data/generate_invoice/output_invoices/invoices_onlineorderflag_false.pdf`

---

## Dependencies

```
pandas
psycopg2-binary
reportlab
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
├── pool/                                        # Landing zone (sebelum medallion)
│
└── medallion_layer/
    ├── bronze/                                  # Raw ingestion layer
    ├── silver/                                  # Cleaned & transformed layer
    └── gold/                                    # Aggregated & reporting layer
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
