# Outline Laporan Perancangan Data Lakehouse

> Kerangka penulisan laporan akademik formal untuk proyek **DATALAKEHOUSE_FP**
> (Final Project Semester 4 — Data Lakehouse). Tiap sub-bab dilengkapi bullet
> deskripsi isi sebagai panduan pengisian.

---

## Bagian Depan (Front Matter)

- **Halaman Judul** — judul proyek, identitas penyusun (nama, NIM), program studi, institusi, tahun.
- **Lembar Pengesahan** — tanda tangan dosen pembimbing/penguji.
- **Abstrak** — ringkasan masalah, metode (Medallion + galaxy schema), hasil, dan kata kunci.
- **Kata Pengantar**
- **Daftar Isi**
- **Daftar Gambar** — diagram arsitektur, DAG pipeline, galaxy schema, dashboard.
- **Daftar Tabel** — tabel sumber data, daftar dim/fact, audit kualitas data.

---

## BAB 1 — PENDAHULUAN

### 1.1 Latar Belakang
- Kebutuhan organisasi mengintegrasikan tiga sumber data heterogen: transaksional OLTP (AdventureWorks), media sosial (tweet berlabel sentimen), dan dokumen invoice PDF.
- Keterbatasan data warehouse tradisional vs fleksibilitas data lake, dan bagaimana **data lakehouse** menjembatani keduanya.
- Motivasi analitik terpadu: revenue, sentimen pelanggan, dan inventory dalam satu model.

### 1.2 Rumusan Masalah
- Bagaimana merancang lakehouse yang menyatukan data terstruktur dan tak terstruktur dalam satu pipeline.
- Bagaimana memodelkan **galaxy schema lintas-fact** dengan dimensi konform.
- Bagaimana mendukung **incremental load** (akumulasi antar-batch) tanpa menimpa data lama.

### 1.3 Tujuan
- Membangun pipeline medallion **Bronze → Silver → Gold** dari tiga sumber heterogen.
- Memodelkan **galaxy schema** dengan 3 fact (`fact_sales`, `fact_sentiment`, `fact_inventory`) berbagi dimensi konform.
- Memuat hasil ke **Data Warehouse PostgreSQL** (`warehouseDB.dw_sales`) secara incremental.
- Menyediakan model siap analitik di **Power BI**.

### 1.4 Batasan Masalah
- Sumber data: AdventureWorks (PostgreSQL), tweet sintetis berlabel, invoice PDF offline.
- Lingkungan: PostgreSQL lokal single-node, pemrosesan berbasis Python (pandas/pyarrow).
- Trade-off PDF: sebagian line item ter-*truncate* (header/total tetap akurat).
- Tidak mencakup deployment cloud, streaming real-time, atau model ML produksi.

### 1.5 Manfaat
- Analitik lintas-fact: revenue × sentimen × inventory per produk/kategori.
- Dasar pengambilan keputusan (champions/at-risk, pain point, inventory turnover, margin).

### 1.6 Sistematika Penulisan
- Gambaran ringkas isi tiap bab (Bab 1–5).

---

## BAB 2 — LANDASAN TEORI

### 2.1 Data Lakehouse
- Definisi dan karakteristik; posisi terhadap **data lake** dan **data warehouse**.

### 2.2 Arsitektur Medallion
- Konsep zona **Bronze (raw)**, **Silver (cleaned/typed)**, **Gold (business-ready)**.
- Peran zona *landing/pool* sebelum medallion.

### 2.3 Pemodelan Dimensional
- Star schema, **galaxy schema / fact constellation**.
- **Dimensi konform**, surrogate key, degenerate key, anggota *Unknown* (key = -1).
- Konsep grain, fact, dan measure.

### 2.4 ETL/ELT dan Incremental Loading
- Konsep batch, *watermark*/cutoff `as-of`, *date window*.
- **Upsert by natural key**, akumulasi antar-batch, pengantar SCD (ringkas).

### 2.5 Format dan Teknologi
- **Parquet** (kolumnar), **PostgreSQL**, Python (`pandas`, `pyarrow`, `pdfplumber`), **Power BI**.

### 2.6 Konsep Sumber Data
- Basis data OLTP (AdventureWorks).
- Dasar *sentiment analysis* (positive/neutral/negative, aspek).
- Ekstraksi data dari dokumen PDF tak terstruktur.

### 2.7 Penelitian / Referensi Terkait *(opsional)*
- Studi atau implementasi lakehouse sejenis sebagai pembanding.

---

## BAB 3 — PERANCANGAN SISTEM

### 3.1 Gambaran Umum dan Arsitektur Sistem
- Diagram alur **Factory → Pool → Medallion → Data Warehouse**.
- **Boundary rule**: lapisan medallion hanya membaca dari `pool/`, tidak menyentuh `dummy_data/`.

### 3.2 Analisis Sumber Data
- OLTP AdventureWorks — **split by channel** (`onlineorderflag`: online → CSV, offline → PDF).
- Tweet sintetis berlabel sentimen (blok `_meta`: sentiment, aspect, product).
- Invoice PDF (1 halaman = 1 invoice) dari offline sales order.
- Snapshot inventory (`productinventory` × `location`).

### 3.3 Perancangan Zona Data
- `dummy_data/` (factory/generator), `pool/` (landing + manifest lineage).
- `bronze/` (raw by format), `silver/` (Parquet typed), `gold/` (galaxy), `model/` (DW-ready).
- Data Warehouse `warehouseDB.dw_sales`.

### 3.4 Perancangan Pipeline (DAG)
- Urutan stage dan dependensi data.
- *Document silver* harus jalan sebelum *sales silver*; gold butuh kedua silver fact.
- `inventory_dw/gold.py` setelah `sales_dw/gold.py` (membaca dim konform).

### 3.5 Perancangan Silver per Branch
- **Branch A — Sales**: gabungan online CSV ∪ offline PDF pada grain line item.
- **Branch B — Document**: parsing PDF → `invoice_header` + `invoice_line`.
- **Branch C — Social**: flatten tweet + `_meta`, fitur analitik (sentiment_score, engagement).
- **Branch D — Inventory**: typing `productinventory` + `location`, dedupe natural key.

### 3.6 Perancangan Gold — Galaxy Schema
- 3 fact + 11 dimensi konform; diagram fact constellation.
- Grain, FK, measure, dan degenerate key tiap fact.
- Dimensi konform `dim_date` & `dim_product`; anggota *Unknown* guard.

### 3.7 Perancangan Incremental Batch dan Date Window
- Dimensi dibangun dari data **full** (surrogate key stabil); fact difilter ke window.
- Preset window (`full`/`last7`/`last30`/`today`/`custom`), `dim_batch`, strategi upsert.
- Perbedaan akumulasi **fact transaksi** (`fact_sales`/`fact_sentiment`, natural key unik → bertambah tiap batch) vs **fact snapshot** (`fact_inventory`, natural key product×location berulang → termuat sekali di batch 1).

### 3.8 Perancangan Data Warehouse
- Schema `dw_sales`, DDL (PK/FK), `batch_key` IDENTITY, strategi load incremental.

### 3.9 Konvensi Penamaan dan Aturan Aliran Data
- Penamaan file (OLTP/social/document/bronze).
- **COPY** (`dummy_data` → `pool`) vs **MOVE/drain** (`pool` → `bronze`).

---

## BAB 4 — IMPLEMENTASI DAN HASIL

### 4.1 Lingkungan dan Prasyarat
- Python 3.10+, PostgreSQL `localhost:5432`, database sumber `adventureworks_local`.

### 4.2 Implementasi Factory dan Generator
- `extract_sales.py` / `extract_production.py` / `split_by_channel.py`.
- Generate tweet (`generate_tweets.py`) dan invoice PDF (`awc_invoices.py`).

### 4.3 Implementasi Pipeline Lakehouse
- `move_to_pool.py`, `bronze.py`, silver/gold/model per domain.
- Orkestrator `build_lakehouse.py` dan **control bridge** `datalakehouse.py`.

### 4.4 Implementasi Load Data Warehouse
- `load_warehouse.py` — ensure DB/schema, open batch, upsert dim & fact, verifikasi count.

### 4.5 Hasil Galaxy Schema
- Tabel jumlah baris fact/dim (mis. `fact_sales` 111,656; `fact_sentiment` 9,016; `fact_inventory` 1,069).
- Statistik dan ringkasan model akhir.

### 4.6 Pengujian dan Skenario Batch
- **Batch 1** (full-refresh) vs **Batch 2** (append, cutoff lebih baru) — bukti akumulasi data.
- Catatan: akumulasi terlihat pada `fact_sales`/`fact_sentiment`; `fact_inventory` (snapshot product×location) hanya termuat di batch 1 (natural key `inv_bk` tanpa tanggal).

### 4.7 Catatan Kualitas Data (Audit Kolom)
- Perbaikan `inv_bk` corruption, Unknown category/subcategory, `is_spike` di-drop, perluasan `dim_date`.

### 4.8 Analitik dan Visualisasi Power BI
- Pemetaan *business question* → kolom → measure/visual (revenue, online vs offline, sentimen, margin, stok, turnover, antar-batch).

### 4.9 Pembahasan dan Trade-off
- Trade-off PDF truncation; pencegahan fan-out saat join dua fact (agregasi ke grain kategori dulu).
- Keterbatasan akumulasi `fact_inventory`: grain *periodic snapshot* dengan natural key tanpa tanggal → tidak bertambah antar-batch; opsi perbaikan (sertakan `date_key` pada `inv_bk`).

---

## BAB 5 — PENUTUP

### 5.1 Kesimpulan
- Rangkuman pencapaian tujuan: pipeline medallion, galaxy schema, DW incremental, analitik terpadu.

### 5.2 Saran / Pengembangan Selanjutnya
- Migrasi ke cloud/Spark/Delta Lake, ingestion streaming, model sentimen berbasis ML, penerapan SCD Type 2.

---

## Bagian Belakang (Back Matter)

- **Daftar Pustaka** — referensi teori dan teknologi.
- **Lampiran**
  - Struktur direktori proyek.
  - Contoh `_manifest.json` dan `schema.json` (galaxy).
  - Cuplikan DDL `create_tables.sql`.
  - Screenshot dashboard Power BI.
