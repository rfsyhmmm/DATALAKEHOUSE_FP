#!/usr/bin/env python3
"""
generate_tweets.py
==================
Generator data dummy tweet (JSON) bertema AdventureWorks untuk proyek Data Lake.

Fitur:
- Jumlah tweet bisa dicustom (default 2000).
- Dibagi ke beberapa hari (default 2) -> cocok untuk skenario "2x periodik run".
- Daftar produk bisa di-inject dari CSV hasil export database
  (mis. Production.Product / DimProduct AdventureWorks). Kalau tidak ada,
  dipakai daftar produk bawaan.
- Sentimen positif / negatif + sebagian kecil netral-ambigu
  (untuk mensimulasikan data kotor yang perlu dibuang saat cleaning).
- Bahasa Inggris, Indonesia, atau campuran.
- Output 1 file per hari (default) atau 1 file gabungan; format JSON array atau NDJSON.

Hanya pakai standard library Python (tanpa install apa pun).

Contoh:
    python3 generate_tweets.py --count 2000 --days 2 --start-date 2026-06-14
    python3 generate_tweets.py --count 5000 --days 3 --products dim_product.csv --lang en
    python3 generate_tweets.py --dump-products products_sample.csv   # export template CSV produk
"""

import argparse
import csv
import json
import random
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

# --------------------------------------------------------------------------- #
# 1. DAFTAR PRODUK BAWAAN (fallback kalau tidak ada CSV)
#    Subset representatif AdventureWorks: name, category, subcategory
# --------------------------------------------------------------------------- #
DEFAULT_PRODUCTS = [
    ("Mountain-100 Silver",      "Bikes",       "Mountain Bikes"),
    ("Mountain-200 Black",       "Bikes",       "Mountain Bikes"),
    ("Mountain-500 Black",       "Bikes",       "Mountain Bikes"),
    ("Road-150 Red",             "Bikes",       "Road Bikes"),
    ("Road-250 Black",           "Bikes",       "Road Bikes"),
    ("Road-550-W Yellow",        "Bikes",       "Road Bikes"),
    ("Touring-1000 Blue",        "Bikes",       "Touring Bikes"),
    ("Touring-2000 Blue",        "Bikes",       "Touring Bikes"),
    ("HL Mountain Frame Silver", "Components",   "Mountain Frames"),
    ("HL Road Frame Black",      "Components",   "Road Frames"),
    ("LL Touring Frame Yellow",  "Components",   "Touring Frames"),
    ("HL Road Wheel",            "Components",   "Wheels"),
    ("ML Mountain Front Wheel",  "Components",   "Wheels"),
    ("HL Crankset",              "Components",   "Cranksets"),
    ("Front Brakes",             "Components",   "Brakes"),
    ("Rear Derailleur",          "Components",   "Derailleurs"),
    ("LL Mountain Handlebars",   "Components",   "Handlebars"),
    ("HL Road Pedal",            "Components",   "Pedals"),
    ("Chain",                    "Components",   "Chains"),
    ("Sport-100 Helmet Red",     "Accessories",  "Helmets"),
    ("Water Bottle - 30 oz",     "Accessories",  "Bottles and Cages"),
    ("Mountain Bottle Cage",     "Accessories",  "Bottles and Cages"),
    ("Hydration Pack - 70 oz",   "Accessories",  "Hydration Packs"),
    ("Touring-Panniers Large",   "Accessories",  "Panniers"),
    ("All-Purpose Bike Stand",   "Accessories",  "Bike Stands"),
    ("Hitch Rack - 4-Bike",      "Accessories",  "Bike Racks"),
    ("Headlights - Dual-Beam",   "Accessories",  "Lights"),
    ("Patch Kit/8 Patches",      "Accessories",  "Tires and Tubes"),
    ("Long-Sleeve Logo Jersey",  "Clothing",     "Jerseys"),
    ("Short-Sleeve Classic Jersey", "Clothing",  "Jerseys"),
    ("AWC Logo Cap",             "Clothing",     "Caps"),
    ("Full-Finger Gloves",       "Clothing",     "Gloves"),
    ("Half-Finger Gloves",       "Clothing",     "Gloves"),
    ("Mountain Bike Socks",      "Clothing",     "Socks"),
    ("Classic Vest",             "Clothing",     "Vests"),
    ("Women's Mountain Shorts",  "Clothing",     "Shorts"),
    ("Racing Socks",             "Clothing",     "Socks"),
    ("Bike Wash - Dissolver",    "Accessories",  "Cleaners"),
]

BRANDS = ["AdventureWorks", "@AdventureWorks", "AdventureWorks Cycles", "AWC", "@AWCycles"]

# --------------------------------------------------------------------------- #
# 2. TEMPLATE TWEET (placeholder: {p}=produk, {b}=brand, {e}=emoji)
# --------------------------------------------------------------------------- #
EN_POS = [
    "Absolutely loving my new {p} from {b}! Best purchase this year {e}",
    "Just got the {p} and it's a game changer. {b} nailed it {e}",
    "Can't recommend the {p} enough. Smooth ride every single time {e}",
    "The {p} quality is unreal. Worth every penny {e}",
    "Shoutout to {b} the {p} totally exceeded my expectations {e}",
    "Three months on the {p} and still impressed. Solid build {e}",
    "Took the {p} on a brutal trail today and it didn't even flinch {e}",
    "Support at {b} fixed my {p} issue in minutes. Legends {e}",
    "If you're on the fence about the {p}, just buy it. Zero regrets {e}",
    "My {p} arrived early and packaging was perfect. Happy customer {e}",
    "Upgraded to the {p} and my rides have never been better {e}",
    "{b} {p} is pure value. Highly recommend to any cyclist {e}",
    "50km on the {p} today, zero complaints. Build quality on point {e}",
    "Honestly the {p} is the best gear I've bought from {b} {e}",
    "Friends keep asking about my {p}. Thanks {b} for the quality {e}",
    "The {p} is lightweight, durable, and looks amazing {e}",
    "Five stars for the {p}. {b} keeps delivering {e}",
    "Loving how reliable the {p} has been so far. No regrets {e}",
]
EN_NEG = [
    "Really disappointed with the {p}. Broke after two weeks {e}",
    "The {p} from {b} is overpriced for what you actually get {e}",
    "Still waiting on a refund for my faulty {p}. Not happy {b} {e}",
    "Worst experience with the {p}. Quality has gone downhill {e}",
    "My {p} arrived damaged and support has been useless {e}",
    "Expected way more from the {p}. Cheap materials, poor finish {e}",
    "The {p} keeps malfunctioning. Regret buying from {b} {e}",
    "Avoid the {p}. It failed me on the very first ride {e}",
    "{b} shipped the wrong {p} twice now. So frustrating {e}",
    "The {p} squeaks and rattles after a week. Not worth it {e}",
    "Customer service for my {p} issue was a nightmare {e}",
    "Paid premium for the {p} and got mediocre quality. Lesson learned {e}",
    "The {p} stopped working out of nowhere. So annoyed {e}",
    "Honestly the {p} is a letdown. Expected better from {b} {e}",
    "Return process for the {p} is a mess. Never again {b} {e}",
    "The {p} looks nice but performs terribly {e}",
    "Two stars for the {p}. Durability is a joke {e}",
    "My {p} fell apart on day three. {b} do better {e}",
]
EN_NEU = [
    "Anyone else using the {p}? Thinking about getting one {e}",
    "Comparing the {p} with a few others before I decide {e}",
    "Saw the {p} at a store today. Looks interesting {e}",
    "Is the {p} from {b} any good? Need some opinions {e}",
    "Just unboxed the {p}. Will report back after a few rides {e}",
    "The {p} is fine I guess, nothing special either way {e}",
]
ID_POS = [
    "Suka banget sama {p} dari {b}! Kualitasnya mantap {e}",
    "Akhirnya beli {p} dan nggak nyesel sama sekali {e}",
    "Recommended sih {p} ini, awet dan nyaman dipakai {e}",
    "Pelayanan {b} cepet banget pas {p} aku ada masalah, salut {e}",
    "{p} worth it parah, harga sebanding sama kualitasnya {e}",
    "Udah sebulan pakai {p}, masih awet dan oke {e}",
    "Pengiriman {p} cepet, packing rapi. Puas pol {e}",
    "Naik level banget gowes ku semenjak pakai {p} {e}",
    "{b} emang nggak pernah ngecewain, {p} nya juara {e}",
    "Bangga punya {p}, temen-temen pada nanya beli dimana {e}",
]
ID_NEG = [
    "Kecewa banget sama {p}, baru dua minggu udah rusak {e}",
    "{p} dari {b} kemahalan buat kualitas segini {e}",
    "Masih nunggu refund {p} yang cacat. Nggak beres {b} {e}",
    "{p} ku dateng dalam kondisi rusak, CS nya lama banget {e}",
    "Nyesel beli {p}, bahannya murahan {e}",
    "Hindari {p} deh, gagal pas pemakaian pertama {e}",
    "{b} salah kirim {p} sampe dua kali, capek {e}",
    "Baru seminggu {p} udah bunyi-bunyi aneh. Zonk {e}",
    "Bayar mahal {p} tapi kualitas biasa aja. Kapok {e}",
    "{p} tiba-tiba mati total. Kesel {b} {e}",
]
ID_NEU = [
    "Ada yang pakai {p}? Lagi mau beli nih {e}",
    "Lagi banding-bandingin {p} sama merek lain {e}",
    "Tadi liat {p} di toko, lumayan menarik {e}",
    "{p} dari {b} bagus nggak ya? Butuh review dong {e}",
]

EMOJI = {
    "positive": ["😍", "🔥", "👍", "✨", "🚴", "💯", "🙌", "😎", ""],
    "negative": ["😡", "👎", "😤", "🙄", "😩", "⚠️", "😣", ""],
    "neutral":  ["🤔", "🧐", "👀", "🤷", ""],
}

HASHTAG_POOL = ["AdventureWorks", "cycling", "bikes", "AWcycles", "rideon", "bikelife", "MTB"]

FIRST_NAMES = ["Andi", "Budi", "Citra", "Dewi", "Alex", "Maria", "Jordan", "Sam", "Rizky",
               "Putri", "Kevin", "Lina", "Tom", " Available", "Nadia", "Chris", "Bayu",
               "Hannah", "Eko", "Sofia", "Dimas", "Olivia", "Galih", "Mia", "Raka"]
LAST_NAMES = ["Pratama", "Smith", "Putra", "Lee", "Wijaya", "Garcia", "Santoso", "Brown",
              "Halim", "Nguyen", "Saputra", "Kim", "Anwar", "Lopez", "Hidayat", "Clark"]


def shorten(name: str) -> str:
    """Ambil nama produk yang lebih enak dibaca untuk teks tweet."""
    return name.split(",")[0].strip()


def product_hashtag(category: str, subcategory: str) -> str:
    src = (subcategory or category or "").replace("-", " ")
    return "".join(w.capitalize() for w in src.split()) if src else ""


# --------------------------------------------------------------------------- #
# 3. LOAD PRODUK DARI CSV (kolom dideteksi otomatis, case-insensitive)
# --------------------------------------------------------------------------- #
def load_products(csv_path):
    if not csv_path:
        return [dict(name=n, category=c, subcategory=s) for n, c, s in DEFAULT_PRODUCTS], "default (bawaan)"

    path = Path(csv_path)
    if not path.exists():
        print(f"[!] CSV '{csv_path}' tidak ditemukan, pakai produk bawaan.", file=sys.stderr)
        return [dict(name=n, category=c, subcategory=s) for n, c, s in DEFAULT_PRODUCTS], "default (bawaan)"

    name_keys = {"name", "productname", "product", "product_name", "namaproduk"}
    cat_keys = {"category", "productcategory", "product_category", "categoryname", "kategori"}
    sub_keys = {"subcategory", "productsubcategory", "product_subcategory",
                "subcategoryname", "subkategori"}

    products = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = {h.lower().strip(): h for h in (reader.fieldnames or [])}

        def pick(keyset):
            for k in keyset:
                if k in headers:
                    return headers[k]
            return None

        h_name, h_cat, h_sub = pick(name_keys), pick(cat_keys), pick(sub_keys)
        if not h_name:
            print("[!] Kolom nama produk tidak terdeteksi di CSV, pakai produk bawaan.", file=sys.stderr)
            return [dict(name=n, category=c, subcategory=s) for n, c, s in DEFAULT_PRODUCTS], "default (bawaan)"

        for row in reader:
            nm = (row.get(h_name) or "").strip()
            if not nm:
                continue
            products.append(dict(
                name=nm,
                category=(row.get(h_cat) or "Unknown").strip() if h_cat else "Unknown",
                subcategory=(row.get(h_sub) or "Unknown").strip() if h_sub else "Unknown",
            ))

    if not products:
        print("[!] CSV kosong/tidak valid, pakai produk bawaan.", file=sys.stderr)
        return [dict(name=n, category=c, subcategory=s) for n, c, s in DEFAULT_PRODUCTS], "default (bawaan)"
    return products, str(path)


# --------------------------------------------------------------------------- #
# 4. PEMBANGKIT TWEET
# --------------------------------------------------------------------------- #
HOUR_WEIGHTS = [1, 1, 1, 1, 1, 2, 3, 5, 6, 6, 5, 5,   # 0-11
                6, 6, 5, 5, 6, 7, 8, 9, 8, 6, 4, 2]   # 12-23


def make_user():
    fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
    handle = (fn[:4] + ln[:4]).lower().replace(" ", "") + str(random.randint(1, 9999))
    return {
        "id_str": str(random.randint(10**8, 10**10)),
        "name": f"{fn} {ln}",
        "screen_name": handle,
        "followers_count": int(abs(random.gauss(800, 1500))),
        "verified": random.random() < 0.03,
    }


def pick_templates(lang, sentiment):
    table = {
        ("en", "positive"): EN_POS, ("en", "negative"): EN_NEG, ("en", "neutral"): EN_NEU,
        ("id", "positive"): ID_POS, ("id", "negative"): ID_NEG, ("id", "neutral"): ID_NEU,
    }
    return table[(lang, sentiment)]


def build_text(product, sentiment, lang):
    tmpl = random.choice(pick_templates(lang, sentiment))
    text = tmpl.format(
        p=shorten(product["name"]),
        b=random.choice(BRANDS),
        e=random.choice(EMOJI[sentiment]),
    ).strip()

    # tambah 1-3 hashtag (kadang-kadang), termasuk hashtag turunan produk
    if random.random() < 0.6:
        tags = random.sample(HASHTAG_POOL, k=random.randint(1, 3))
        ph = product_hashtag(product["category"], product["subcategory"])
        if ph and random.random() < 0.5:
            tags.append(ph)
        text += " " + " ".join("#" + t for t in tags)

    # kadang diawali "RT @user:" untuk meniru retweet
    if random.random() < 0.08:
        text = f"RT @{random.choice(FIRST_NAMES)[:4].lower()}{random.randint(1,99)}: " + text

    return " ".join(text.split())  # rapikan spasi ganda


def random_timestamp(day: date):
    hour = random.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
    return datetime(day.year, day.month, day.day, hour,
                    random.randint(0, 59), random.randint(0, 59))


def split_counts(total, days):
    base = total // days
    counts = [base] * days
    for i in range(total - base * days):
        counts[i] += 1
    return counts


def generate(products, count, days, start_date, pos_ratio, neu_ratio,
             lang_mode, include_meta):
    neg_ratio = max(0.0, 1.0 - pos_ratio - neu_ratio)
    sentiments = ["positive", "negative", "neutral"]
    weights = [pos_ratio, neg_ratio, neu_ratio]

    per_day = split_counts(count, days)
    tweet_id = 1_800_000_000_000_000_000
    by_day = {}

    for d in range(days):
        day = start_date + timedelta(days=d)
        rows = []
        for _ in range(per_day[d]):
            tweet_id += random.randint(1, 50)
            sentiment = random.choices(sentiments, weights=weights, k=1)[0]
            product = random.choice(products)
            lang = (random.choice(["en", "id"]) if lang_mode == "mixed" else lang_mode)
            ts = random_timestamp(day)

            tweet = {
                "id_str": str(tweet_id),
                "created_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "text": build_text(product, sentiment, lang),
                "lang": "in" if lang == "id" else "en",
                "user": make_user(),
                "retweet_count": int(abs(random.gauss(3, 12))),
                "favorite_count": int(abs(random.gauss(8, 25))),
                "source": random.choice(
                    ["Twitter for iPhone", "Twitter for Android", "Twitter Web App"]),
            }
            if include_meta:
                tweet["_meta"] = {  # ground-truth (boleh dibuang sebelum analisis nyata)
                    "sentiment": sentiment,
                    "product": product["name"],
                    "category": product["category"],
                    "subcategory": product["subcategory"],
                }
            rows.append(tweet)

        rows.sort(key=lambda r: r["created_at"])
        by_day[day.isoformat()] = rows

    return by_day


# --------------------------------------------------------------------------- #
# 5. PENULISAN OUTPUT
# --------------------------------------------------------------------------- #
def write_outputs(by_day, out_dir, split, fmt):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    def dump(rows, path):
        with path.open("w", encoding="utf-8") as f:
            if fmt == "ndjson":
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            else:
                json.dump(rows, f, ensure_ascii=False, indent=2)
        written.append((path, len(rows)))

    ext = "ndjson" if fmt == "ndjson" else "json"
    if split == "day":
        for day, rows in by_day.items():
            dump(rows, out / f"tweets_{day}.{ext}")
    else:
        allrows = [r for rows in by_day.values() for r in rows]
        allrows.sort(key=lambda r: r["created_at"])
        dump(allrows, out / f"tweets_all.{ext}")
    return written


def dump_products_template(path):
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ProductID", "Name", "ProductCategory", "ProductSubcategory"])
        for i, (n, c, s) in enumerate(DEFAULT_PRODUCTS, start=1):
            w.writerow([i, n, c, s])
    print(f"[OK] Template CSV produk ditulis ke: {path}")


# --------------------------------------------------------------------------- #
# 6. CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Generator tweet dummy AdventureWorks (JSON).")
    ap.add_argument("--count", type=int, default=2000, help="jumlah total tweet (default 2000)")
    ap.add_argument("--days", type=int, default=2, help="jumlah hari (default 2)")
    ap.add_argument("--start-date", default=date.today().isoformat(),
                    help="tanggal mulai YYYY-MM-DD (default hari ini)")
    ap.add_argument("--products", default=None, help="path CSV produk hasil export DB (opsional)")
    ap.add_argument("--lang", choices=["en", "id", "mixed"], default="mixed",
                    help="bahasa tweet (default mixed)")
    ap.add_argument("--pos-ratio", type=float, default=0.6, help="rasio positif (default 0.6)")
    ap.add_argument("--neu-ratio", type=float, default=0.1,
                    help="rasio netral-ambigu untuk data kotor (default 0.1)")
    ap.add_argument("--output", default="output", help="folder output (default ./output)")
    ap.add_argument("--split", choices=["day", "none"], default="day",
                    help="day=1 file per hari, none=1 file gabungan")
    ap.add_argument("--format", choices=["json", "ndjson"], default="json")
    ap.add_argument("--no-meta", action="store_true", help="jangan sertakan ground-truth _meta")
    ap.add_argument("--seed", type=int, default=None, help="seed agar hasil reproducible")
    ap.add_argument("--dump-products", default=None,
                    help="hanya export template CSV produk ke path ini lalu keluar")
    args = ap.parse_args()

    if args.dump_products:
        dump_products_template(args.dump_products)
        return

    if args.seed is not None:
        random.seed(args.seed)

    try:
        start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    except ValueError:
        ap.error("--start-date harus format YYYY-MM-DD")

    if args.pos_ratio + args.neu_ratio > 1.0:
        ap.error("pos-ratio + neu-ratio tidak boleh > 1.0")

    products, src = load_products(args.products)
    print(f"[i] Produk dimuat: {len(products)} item dari {src}")

    by_day = generate(products, args.count, args.days, start,
                      args.pos_ratio, args.neu_ratio, args.lang, not args.no_meta)

    written = write_outputs(by_day, args.output, args.split, args.format)
    total = sum(n for _, n in written)
    print(f"[OK] {total} tweet dibuat, terbagi {args.days} hari:")
    for path, n in written:
        print(f"     - {path}  ({n} tweet)")


if __name__ == "__main__":
    main()
