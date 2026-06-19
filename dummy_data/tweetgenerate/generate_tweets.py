#!/usr/bin/env python3
"""
generate_tweets.py
==================
Generator data dummy tweet (JSON) bertema AdventureWorks untuk proyek Data Lake.

Fitur:
- Jumlah tweet & rentang tanggal bisa dicustom (--count, --days atau --start/--end-date).
- Produk bisa di-inject dari export DB:
    * --product (Production.Product) di-join dgn --subcategory & --category, atau
    * --products (satu CSV yang sudah berisi name/category/subcategory).
  Bahan mentah non-finished-goods otomatis dibuang (override: --include-nonsellable).
- Kalimat dirakit kombinatorial (opener + klausa-aspek + closer) -> variasi banyak.
- Tag ASPEK per tweet (kualitas/pengiriman/harga/layanan/durabilitas/umum) untuk
  analisa sentimen berbasis aspek (ditaruh di _meta.aspect).
- BIAS sentimen per produk: sebagian produk "loved", sebagian "problem".
- SPIKE negatif pada tanggal tertentu (simulasi recall), bisa dibatasi subkategori.
- Bahasa Inggris, Indonesia, atau campuran. Output JSON array / NDJSON, per-hari/gabungan.

Hanya standard library Python (tanpa install).

Contoh:
    python3 generate_tweets.py --product product.csv --subcategory productsubcategory.csv \\
        --category productcategory.csv --count 5000 --start-date 2026-06-01 --end-date 2026-06-07
    python3 generate_tweets.py --product product.csv --subcategory sub.csv --category cat.csv \\
        --count 8000 --start-date 2026-06-01 --end-date 2026-06-10 \\
        --spike-date 2026-06-05 --spike-subcategory "Mountain Bikes" --spike-neg-ratio 0.85
    python3 generate_tweets.py --dump-products template.csv   # export template CSV produk
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
# 2. PEMBANGUN KALIMAT KOMBINATORIAL  (opener + klausa-aspek + closer)
#    Aspek: kualitas, pengiriman, harga, layanan, durabilitas, umum
#    {p}=produk  {b}=brand  {e}=emoji
# --------------------------------------------------------------------------- #
ASPECTS = ["kualitas", "pengiriman", "harga", "layanan", "durabilitas", "umum"]
ASPECT_WEIGHTS = [0.32, 0.18, 0.16, 0.12, 0.14, 0.08]

OPENERS = {
    ("en", "positive"): [
        "Absolutely loving my new {p} from {b}",
        "The {p} from {b} has been fantastic",
        "So glad I picked up the {p}",
        "My {p} from {b} is excellent",
        "Really impressed with the {p}",
        "Three weeks on the {p} and I'm sold",
        "Upgraded to the {p} and no looking back",
        "Shoutout to {b} for the {p}",
    ],
    ("en", "negative"): [
        "Really disappointed with the {p}",
        "The {p} from {b} let me down",
        "Regret buying the {p}",
        "Not happy with my {p} at all",
        "Frustrated with the {p} from {b}",
        "Expected so much more from the {p}",
        "Done with the {p} honestly",
        "Two stars for the {p} from {b}",
    ],
    ("id", "positive"): [
        "Suka banget sama {p} dari {b}",
        "Akhirnya pakai {p} dan puas pol",
        "{p} dari {b} mantap sih",
        "Nggak nyesel beli {p}",
        "Terkesan banget sama {p}",
        "Udah sebulan pakai {p}, oke terus",
        "Naik level gowes ku gara-gara {p}",
        "Salut buat {b} soal {p}",
    ],
    ("id", "negative"): [
        "Kecewa banget sama {p}",
        "{p} dari {b} bikin nyesel",
        "Nyesel beli {p}",
        "Nggak puas sama {p} ku",
        "Capek deh sama {p} dari {b}",
        "Berharap lebih dari {p}",
        "Udahan deh sama {p} ini",
        "Bintang dua buat {p} dari {b}",
    ],
}

ASPECT_CLAUSE = {
    ("en", "positive"): {
        "kualitas":    ["build quality feels premium", "performs flawlessly", "feels solid and well made"],
        "pengiriman":  ["shipping was fast and packaging perfect", "arrived a day early, well packed", "delivery was smooth"],
        "harga":       ["worth every penny", "great value for the price", "fair price for this quality"],
        "layanan":     ["support was quick and friendly", "customer service sorted it instantly", "the team was super helpful"],
        "durabilitas": ["still rock solid after months", "zero wear after heavy use", "holding up great on rough trails"],
        "umum":        ["exceeded my expectations", "honestly a great buy", "couldn't be happier"],
    },
    ("en", "negative"): {
        "kualitas":    ["poor build, cheap materials", "rattles and feels flimsy", "the finish is sloppy"],
        "pengiriman":  ["it arrived late and damaged", "packaging was a mess", "shipping took forever"],
        "harga":       ["overpriced for what you get", "just not worth the money", "way too expensive"],
        "layanan":     ["customer service was useless", "support ignored my emails", "the return process is a nightmare"],
        "durabilitas": ["it broke after two weeks", "fell apart on the first ride", "wore out way too fast"],
        "umum":        ["a total letdown", "just a bad buy", "wish I'd skipped it"],
    },
    ("id", "positive"): {
        "kualitas":    ["kualitasnya premium", "performanya mulus", "terasa kokoh dan rapi"],
        "pengiriman":  ["pengiriman cepat dan packing rapi", "sampai lebih cepat, aman", "kirimnya lancar"],
        "harga":       ["worth it banget", "harga sebanding kualitas", "murah buat kualitas segini"],
        "layanan":     ["CS-nya cepat dan ramah", "admin responsif banget", "pelayanannya membantu"],
        "durabilitas": ["masih awet setelah berbulan-bulan", "tahan banting dipakai berat", "kuat di trek kasar"],
        "umum":        ["melebihi ekspektasi", "beli yang tepat sih", "puas pol"],
    },
    ("id", "negative"): {
        "kualitas":    ["bahannya murahan", "bunyi-bunyi dan ringkih", "finishing-nya asal"],
        "pengiriman":  ["datang telat dan rusak", "packing-nya berantakan", "kirimnya lama banget"],
        "harga":       ["kemahalan buat kualitas segini", "nggak sebanding harganya", "mahal nggak masuk akal"],
        "layanan":     ["CS-nya nggak guna", "komplain didiemin", "proses retur ribet"],
        "durabilitas": ["rusak baru dua minggu", "jebol pas ride pertama", "cepet aus banget"],
        "umum":        ["zonk total", "salah beli", "nyesel ambil ini"],
    },
}

CLOSERS = {
    ("en", "positive"): ["Highly recommend.", "Zero regrets.", "Five stars.", "Will buy again.", "", ""],
    ("en", "negative"): ["Avoid.", "Never again.", "Do better {b}.", "Lesson learned.", "", ""],
    ("id", "positive"): ["Recommended.", "Nggak nyesel.", "Bintang lima.", "Bakal beli lagi.", "", ""],
    ("id", "negative"): ["Hindari.", "Kapok.", "Tolong {b} dibenahi.", "Pelajaran mahal.", "", ""],
}

NEUTRAL = {
    "en": [
        "Anyone using the {p} from {b}? Worth it? {e}",
        "Comparing the {p} with a few others before I buy {e}",
        "Saw the {p} in store today, looks interesting {e}",
        "Thinking about the {p}. Any reviews? {e}",
        "Just unboxed the {p}, will report back {e}",
        "Is the {p} any good for daily rides? {e}",
    ],
    "id": [
        "Ada yang pakai {p} dari {b}? Worth it nggak? {e}",
        "Lagi banding-bandingin {p} sama merek lain {e}",
        "Tadi liat {p} di toko, lumayan menarik {e}",
        "Lagi mikir mau beli {p}. Ada review? {e}",
        "Baru unboxing {p}, nanti aku update {e}",
        "{p} bagus nggak buat harian? {e}",
    ],
}

EMOJI = {
    "positive": ["\U0001F60D", "\U0001F525", "\U0001F44D", "\u2728", "\U0001F6B4", "\U0001F4AF", "\U0001F64C", "\U0001F60E", ""],
    "negative": ["\U0001F621", "\U0001F44E", "\U0001F624", "\U0001F644", "\U0001F629", "\u26A0\uFE0F", "\U0001F623", ""],
    "neutral":  ["\U0001F914", "\U0001F9D0", "\U0001F440", "\U0001F937", ""],
}

HASHTAG_POOL = ["AdventureWorks", "cycling", "bikes", "AWcycles", "rideon", "bikelife", "MTB"]

FIRST_NAMES = ["Andi", "Budi", "Citra", "Dewi", "Alex", "Maria", "Jordan", "Sam", "Rizky",
               "Putri", "Kevin", "Lina", "Tom", "Maya", "Nadia", "Chris", "Bayu",
               "Hannah", "Eko", "Sofia", "Dimas", "Olivia", "Galih", "Mia", "Raka"]
LAST_NAMES = ["Pratama", "Smith", "Putra", "Lee", "Wijaya", "Garcia", "Santoso", "Brown",
              "Halim", "Nguyen", "Saputra", "Kim", "Anwar", "Lopez", "Hidayat", "Clark"]


def shorten(name):
    """Nama produk yang lebih enak dibaca untuk teks tweet."""
    return name.split(",")[0].strip()


def product_hashtag(category, subcategory):
    src = (subcategory or category or "").replace("-", " ")
    return "".join(w.capitalize() for w in src.split()) if src else ""


# --------------------------------------------------------------------------- #
# 3. LOAD PRODUK DARI CSV TUNGGAL (name/category/subcategory sudah ada)
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
    sub_keys = {"subcategory", "productsubcategory", "product_subcategory", "subcategoryname", "subkategori"}

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
            print("[!] Kolom nama produk tidak terdeteksi, pakai produk bawaan.", file=sys.stderr)
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
# 3b. LOAD PRODUK DARI EXPORT DB MENTAH (join product + subcategory + category)
# --------------------------------------------------------------------------- #
def _read_dicts(csv_path):
    if not csv_path:
        return []
    p = Path(csv_path)
    if not p.exists():
        print(f"[!] CSV '{csv_path}' tidak ditemukan.", file=sys.stderr)
        return []
    with p.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        return [{(k or "").lower().strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(f)]


def _get(row, *keys, default=""):
    for k in keys:
        if k in row and row[k] != "":
            return row[k]
    return default


def load_products_joined(product_csv, subcategory_csv=None, category_csv=None,
                         include_nonsellable=False):
    cat = {}
    for r in _read_dicts(category_csv):
        cat[_get(r, "productcategoryid", "id")] = _get(r, "name")
    sub = {}
    for r in _read_dicts(subcategory_csv):
        sid = _get(r, "productsubcategoryid", "id")
        sub[sid] = (_get(r, "name"), cat.get(_get(r, "productcategoryid"), ""))

    rows = _read_dicts(product_csv)
    if not rows:
        print("[!] product CSV kosong/tidak ada, pakai produk bawaan.", file=sys.stderr)
        return [dict(name=n, category=c, subcategory=s) for n, c, s in DEFAULT_PRODUCTS], "default (bawaan)"

    products, skipped = [], 0
    for r in rows:
        name = _get(r, "name", "productname", "product")
        if not name:
            continue
        sid = _get(r, "productsubcategoryid", "subcategoryid")
        sub_name, cat_name = sub.get(sid, ("", ""))
        finished = _get(r, "finishedgoodsflag").lower()
        is_sellable = (finished in ("true", "1", "yes")) or (sid != "" and not subcategory_csv)
        if not include_nonsellable and not (sub_name and is_sellable):
            skipped += 1
            continue
        products.append(dict(name=name,
                             category=cat_name or "Unknown",
                             subcategory=sub_name or "Unknown"))
    if not products:
        print("[!] Tidak ada produk jadi yang lolos filter; coba --include-nonsellable. "
              "Sementara pakai produk bawaan.", file=sys.stderr)
        return [dict(name=n, category=c, subcategory=s) for n, c, s in DEFAULT_PRODUCTS], "default (bawaan)"
    return products, f"join product+subcategory+category ({len(products)} jadi, {skipped} dilewati)"


def assign_product_bias(products):
    """Tandai tiap produk: ~30% 'loved', ~20% 'problem', sisanya 'mixed'."""
    for p in products:
        r = random.random()
        p["bias"] = "loved" if r < 0.30 else ("problem" if r > 0.80 else "mixed")


# --------------------------------------------------------------------------- #
# 4. PEMBANGKIT TWEET
# --------------------------------------------------------------------------- #
HOUR_WEIGHTS = [1, 1, 1, 1, 1, 2, 3, 5, 6, 6, 5, 5,
                6, 6, 5, 5, 6, 7, 8, 9, 8, 6, 4, 2]


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


def build_text(product, sentiment, lang, aspect):
    brand = random.choice(BRANDS)
    emoji = random.choice(EMOJI[sentiment])
    if sentiment == "neutral":
        tmpl = random.choice(NEUTRAL[lang])
    else:
        opener = random.choice(OPENERS[(lang, sentiment)])
        clause = random.choice(ASPECT_CLAUSE[(lang, sentiment)][aspect])
        closer = random.choice(CLOSERS[(lang, sentiment)])
        tmpl = " ".join(part for part in [opener + " \u2014 " + clause, closer, "{e}"] if part)
    text = tmpl.format(p=shorten(product["name"]), b=brand, e=emoji).strip()

    if random.random() < 0.6:
        tags = random.sample(HASHTAG_POOL, k=random.randint(1, 3))
        ph = product_hashtag(product["category"], product["subcategory"])
        if ph and random.random() < 0.5:
            tags.append(ph)
        text += " " + " ".join("#" + t for t in tags)

    if random.random() < 0.08:
        text = f"RT @{random.choice(FIRST_NAMES)[:4].lower()}{random.randint(1, 99)}: " + text
    return " ".join(text.split())


def random_timestamp(day):
    hour = random.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
    return datetime(day.year, day.month, day.day, hour,
                    random.randint(0, 59), random.randint(0, 59))


def split_counts(total, days):
    base = total // days
    counts = [base] * days
    for i in range(total - base * days):
        counts[i] += 1
    return counts


def _choose_sentiment(product, base, bias_strength, spike_hit, spike_neg_ratio):
    pos, neg, neu = base
    if spike_hit:
        neg = spike_neg_ratio
        pos = (1 - spike_neg_ratio) * 0.6
        neu = (1 - spike_neg_ratio) * 0.4
    elif bias_strength > 0:
        b = product.get("bias", "mixed")
        if b == "loved":
            pos *= (1 + bias_strength)
            neg *= (1 - bias_strength)
        elif b == "problem":
            neg *= (1 + bias_strength * 1.5)
            pos *= (1 - bias_strength)
    w = [max(pos, 0.0), max(neg, 0.0), max(neu, 0.0)]
    return random.choices(["positive", "negative", "neutral"], weights=w, k=1)[0]


def generate(products, count, days, start_date, pos_ratio, neu_ratio,
             lang_mode, include_meta, bias_strength=0.0,
             spike_days=None, spike_neg_ratio=0.85, spike_subcats=None):
    base = (pos_ratio, max(0.0, 1.0 - pos_ratio - neu_ratio), neu_ratio)
    spike_days = spike_days or set()
    spike_subcats = spike_subcats or set()

    per_day = split_counts(count, days)
    tweet_id = 1_800_000_000_000_000_000
    by_day = {}

    for d in range(days):
        day = start_date + timedelta(days=d)
        day_iso = day.isoformat()
        is_spike_day = day_iso in spike_days
        rows = []
        for _ in range(per_day[d]):
            tweet_id += random.randint(1, 50)
            product = random.choice(products)
            spike_hit = is_spike_day and (
                not spike_subcats or product["subcategory"].lower() in spike_subcats)

            sentiment = _choose_sentiment(product, base, bias_strength,
                                          spike_hit, spike_neg_ratio)
            if sentiment == "neutral":
                aspect = "umum"
            elif spike_hit:
                aspect = random.choices(["durabilitas", "kualitas", "layanan"],
                                        weights=[0.5, 0.35, 0.15], k=1)[0]
            else:
                aspect = random.choices(ASPECTS, weights=ASPECT_WEIGHTS, k=1)[0]

            lang = (random.choice(["en", "id"]) if lang_mode == "mixed" else lang_mode)
            ts = random_timestamp(day)

            tweet = {
                "id_str": str(tweet_id),
                "created_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "text": build_text(product, sentiment, lang, aspect),
                "lang": "in" if lang == "id" else "en",
                "user": make_user(),
                "retweet_count": int(abs(random.gauss(3, 12))),
                "favorite_count": int(abs(random.gauss(8, 25))),
                "source": random.choice(
                    ["Twitter for iPhone", "Twitter for Android", "Twitter Web App"]),
            }
            if include_meta:
                tweet["_meta"] = {
                    "sentiment": sentiment,
                    "aspect": aspect,
                    "product": product["name"],
                    "category": product["category"],
                    "subcategory": product["subcategory"],
                    "product_bias": product.get("bias", "mixed"),
                    "spike": spike_hit,
                }
            rows.append(tweet)

        rows.sort(key=lambda r: r["created_at"])
        by_day[day_iso] = rows
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
    ap.add_argument("--days", type=int, default=2, help="jumlah hari (default 2; diabaikan jika --end-date diisi)")
    ap.add_argument("--start-date", default=date.today().isoformat(), help="tanggal mulai YYYY-MM-DD")
    ap.add_argument("--end-date", default=None, help="tanggal akhir YYYY-MM-DD (opsional, inklusif; menggantikan --days)")
    ap.add_argument("--products", default=None, help="CSV produk tunggal (name/category/subcategory)")
    ap.add_argument("--product", default=None, help="export Production.Product mentah (di-join)")
    ap.add_argument("--subcategory", default=None, help="export ProductSubcategory (untuk --product)")
    ap.add_argument("--category", default=None, help="export ProductCategory (untuk --product)")
    ap.add_argument("--include-nonsellable", action="store_true", help="ikutkan bahan mentah saat join --product")
    ap.add_argument("--lang", choices=["en", "id", "mixed"], default="mixed", help="bahasa tweet (default mixed)")
    ap.add_argument("--pos-ratio", type=float, default=0.6, help="rasio positif dasar (default 0.6)")
    ap.add_argument("--neu-ratio", type=float, default=0.1, help="rasio netral-ambigu (default 0.1)")
    ap.add_argument("--no-product-bias", action="store_true", help="matikan bias sentimen per produk")
    ap.add_argument("--bias-strength", type=float, default=0.5, help="kekuatan bias per produk 0..1 (default 0.5)")
    ap.add_argument("--spike-date", action="append", default=[], help="tanggal lonjakan negatif YYYY-MM-DD (boleh diulang)")
    ap.add_argument("--spike-neg-ratio", type=float, default=0.85, help="rasio negatif saat spike (default 0.85)")
    ap.add_argument("--spike-subcategory", action="append", default=[], help="batasi spike ke subkategori tertentu (boleh diulang)")
    ap.add_argument("--output", default="output", help="folder output (default ./output)")
    ap.add_argument("--split", choices=["day", "none"], default="day", help="day=1 file/hari, none=gabungan")
    ap.add_argument("--format", choices=["json", "ndjson"], default="json")
    ap.add_argument("--no-meta", action="store_true", help="jangan sertakan ground-truth _meta")
    ap.add_argument("--seed", type=int, default=None, help="seed agar reproducible")
    ap.add_argument("--dump-products", default=None, help="export template CSV produk lalu keluar")
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

    days = args.days
    if args.end_date:
        try:
            end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        except ValueError:
            ap.error("--end-date harus format YYYY-MM-DD")
        if end < start:
            ap.error("--end-date tidak boleh sebelum --start-date")
        days = (end - start).days + 1

    if args.pos_ratio + args.neu_ratio > 1.0:
        ap.error("pos-ratio + neu-ratio tidak boleh > 1.0")

    spike_days = set(args.spike_date or [])
    for sd in spike_days:
        try:
            datetime.strptime(sd, "%Y-%m-%d")
        except ValueError:
            ap.error(f"--spike-date tidak valid: {sd}")
    spike_subcats = set(s.lower() for s in (args.spike_subcategory or []))

    if args.product:
        products, src = load_products_joined(args.product, args.subcategory,
                                             args.category, args.include_nonsellable)
    else:
        products, src = load_products(args.products)
    assign_product_bias(products)
    bias_strength = 0.0 if args.no_product_bias else args.bias_strength
    print(f"[i] Produk dimuat: {len(products)} item dari {src}")
    if spike_days:
        tgt = ", ".join(sorted(args.spike_subcategory)) if spike_subcats else "semua subkategori"
        print(f"[i] Spike negatif pada {sorted(spike_days)} (rasio neg={args.spike_neg_ratio}, target: {tgt})")

    by_day = generate(products, args.count, days, start,
                      args.pos_ratio, args.neu_ratio, args.lang, not args.no_meta,
                      bias_strength=bias_strength, spike_days=spike_days,
                      spike_neg_ratio=args.spike_neg_ratio, spike_subcats=spike_subcats)

    written = write_outputs(by_day, args.output, args.split, args.format)
    total = sum(n for _, n in written)
    print(f"[OK] {total} tweet dibuat, terbagi {days} hari:")
    for path, n in written:
        print(f"     - {path}  ({n} tweet)")


if __name__ == "__main__":
    main()
