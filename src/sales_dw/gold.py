"""
sales_dw / gold.py — silver (Parquet) -> gold/ (Parquet GALAXY schema)

Builds the Sales + Sentiment **fact constellation** (galaxy schema):

  conformed (shared) dims : dim_date, dim_product
  sales-private dims      : dim_customer, dim_channel
  sentiment-private dims  : dim_aspect, dim_sentiment
  facts                   : fact_sales (sales line grain), fact_sentiment (tweet grain)

The two facts are bridged primarily through the conformed **dim_product** (and its
category / subcategory hierarchy). NOTE: AdventureWorks sales dates are historical
(~2011-2014) while the synthetic tweets are 2026-06, so dim_date does NOT overlap
across the two facts — time-aligned "sentiment-then-sales" trending is not meaningful
on this dummy data; product/category is the real cross-fact bridge.

Integer surrogate keys are used so Power BI auto-detects dim -> fact relationships.
Every dimension a fact maps into carries an Unknown member (key = -1) so unmatched
rows never orphan (and the model/ FK validation passes cleanly).
"""

from pathlib import Path

import pandas as pd

REPO_ROOT     = Path(__file__).resolve().parent.parent.parent
SILVER_SALES  = REPO_ROOT / "medallion_layer" / "silver" / "sales"
SILVER_SOCIAL = REPO_ROOT / "medallion_layer" / "silver" / "social"
GOLD          = REPO_ROOT / "medallion_layer" / "gold"

UNKNOWN_KEY = -1


def _read_sales(name: str) -> pd.DataFrame:
    return pd.read_parquet(SILVER_SALES / f"{name}.parquet")


def _write(df: pd.DataFrame, name: str) -> int:
    GOLD.mkdir(parents=True, exist_ok=True)
    df.to_parquet(GOLD / f"{name}.parquet", index=False)
    return len(df)


def _date_key(dt: pd.Series) -> pd.Series:
    """YYYYMMDD int key; NaT -> UNKNOWN_KEY."""
    d = pd.to_datetime(dt, errors="coerce").dt.normalize()
    key = (d.dt.year * 10000 + d.dt.month * 100 + d.dt.day)
    return key.fillna(UNKNOWN_KEY).astype("int64")


# --------------------------------------------------------------------------- #
# Conformed (shared) dimensions
# --------------------------------------------------------------------------- #
def build_dim_date(*date_series: pd.Series) -> pd.DataFrame:
    """Conformed calendar from the union of every fact's dates (+ Unknown member)."""
    alld = pd.concat([pd.to_datetime(s, errors="coerce") for s in date_series],
                     ignore_index=True)
    dates = alld.dropna().dt.normalize().drop_duplicates().sort_values()
    dd = pd.DataFrame({"full_date": dates})
    dd["date_key"]   = (dd["full_date"].dt.year * 10000
                        + dd["full_date"].dt.month * 100
                        + dd["full_date"].dt.day).astype("int64")
    dd["day"]        = dd["full_date"].dt.day.astype("int64")
    dd["month"]      = dd["full_date"].dt.month.astype("int64")
    dd["month_name"] = dd["full_date"].dt.strftime("%B")
    dd["quarter"]    = dd["full_date"].dt.quarter.astype("int64")
    dd["year"]       = dd["full_date"].dt.year.astype("int64")
    dd["full_date"]  = dd["full_date"].dt.date
    unknown = {"date_key": UNKNOWN_KEY, "full_date": None, "day": -1, "month": -1,
               "month_name": "Unknown", "quarter": -1, "year": -1}
    dd = pd.concat([pd.DataFrame([unknown]), dd], ignore_index=True)
    return dd[["date_key", "full_date", "day", "month", "month_name", "quarter", "year"]]


def build_dim_product(product, subcat, cat) -> pd.DataFrame:
    sc = subcat.rename(columns={"name": "subcategory"})
    ct = cat.rename(columns={"name": "category"})
    p = product.merge(sc[["productsubcategoryid", "productcategoryid", "subcategory"]],
                      on="productsubcategoryid", how="left")
    p = p.merge(ct[["productcategoryid", "category"]], on="productcategoryid", how="left")
    p = p.rename(columns={
        "productid": "product_id", "name": "product_name", "productnumber": "product_number",
        "standardcost": "standard_cost", "listprice": "list_price",
    })
    p["category"] = p["category"].fillna("Unknown")
    p["subcategory"] = p["subcategory"].fillna("Unknown")
    p = p.sort_values("product_id").reset_index(drop=True)
    p.insert(0, "product_key", p.index + 1)
    cols = ["product_key", "product_id", "product_name", "product_number", "color", "size",
            "category", "subcategory", "standard_cost", "list_price"]
    unknown = {"product_key": UNKNOWN_KEY, "product_id": pd.NA, "product_name": "Unknown",
               "product_number": "Unknown", "color": None, "size": None,
               "category": "Unknown", "subcategory": "Unknown",
               "standard_cost": 0.0, "list_price": 0.0}
    p = pd.concat([pd.DataFrame([unknown]), p[cols]], ignore_index=True)
    return p[cols]


# --------------------------------------------------------------------------- #
# Sales-private dimensions
# --------------------------------------------------------------------------- #
def build_dim_customer(customer: pd.DataFrame) -> pd.DataFrame:
    c = customer.copy()
    customer_type = pd.Series("Unknown", index=c.index)
    customer_type[c["storeid"].notna()] = "Store"
    customer_type[c["personid"].notna()] = "Individual"
    c["customer_type"] = customer_type
    c = c.rename(columns={"customerid": "customer_id", "territoryid": "territory_id"})
    c = c.sort_values("customer_id").reset_index(drop=True)
    c.insert(0, "customer_key", c.index + 1)
    cols = ["customer_key", "customer_id", "customer_type", "territory_id"]
    unknown = {"customer_key": UNKNOWN_KEY, "customer_id": pd.NA,
               "customer_type": "Unknown", "territory_id": pd.NA}
    c = pd.concat([pd.DataFrame([unknown]), c[cols]], ignore_index=True)
    return c[cols]


def build_dim_channel() -> pd.DataFrame:
    return pd.DataFrame({"channel_key": [1, 2], "channel_name": ["Online", "Offline"]})


# --------------------------------------------------------------------------- #
# Sentiment-private dimensions
# --------------------------------------------------------------------------- #
def build_dim_aspect() -> pd.DataFrame:
    # fixed set matching silver social aspect_en (stable surrogate keys)
    names = ["Quality", "Delivery", "Price", "Service", "Durability", "General"]
    return pd.DataFrame({"aspect_key": range(1, len(names) + 1), "aspect_name": names})


def build_dim_sentiment() -> pd.DataFrame:
    return pd.DataFrame({
        "sentiment_key":   [1, 2, 3],
        "sentiment_label": ["positive", "neutral", "negative"],
        "sentiment_score": [1, 0, -1],
    })


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #
def build_fact_sales(sales, dim_customer, dim_product) -> pd.DataFrame:
    f = sales.copy()
    f["date_key"] = _date_key(f["order_date"])
    f["channel_key"] = f["channel"].map({"Online": 1, "Offline": 2}).astype("Int64")

    cust_map = dim_customer.set_index("customer_id")["customer_key"]
    f["customer_key"] = f["customer_id"].map(cust_map).fillna(UNKNOWN_KEY).astype("int64")

    prod_map = dim_product.set_index("product_id")["product_key"]
    f["product_key"] = f["product_id"].map(prod_map).fillna(UNKNOWN_KEY).astype("int64")

    f = f.rename(columns={"salesorderid": "sales_order_id", "order_qty": "order_qty"})
    f["sales_count"] = 1
    return f[["date_key", "customer_key", "product_key", "channel_key",
              "sales_order_id", "source_type", "order_qty", "unit_price",
              "unit_price_discount", "line_total", "sales_count"]]


def build_fact_sentiment(sentiment, dim_product, dim_aspect, dim_sentiment) -> pd.DataFrame:
    f = sentiment.copy()
    f["date_key"] = _date_key(f["event_date"])

    # product via name (conformed bridge to sales); unmatched -> Unknown(-1)
    name_map = (dim_product[dim_product["product_key"] != UNKNOWN_KEY]
                .assign(_k=lambda d: d["product_name"].str.strip().str.lower())
                .drop_duplicates("_k").set_index("_k")["product_key"])
    f["product_key"] = (f["product_name"].fillna("").str.strip().str.lower()
                        .map(name_map).fillna(UNKNOWN_KEY).astype("int64"))

    aspect_map = dim_aspect.set_index("aspect_name")["aspect_key"]
    f["aspect_key"] = f["aspect_en"].map(aspect_map).fillna(
        aspect_map.get("General")).astype("int64")

    sent_map = dim_sentiment.set_index("sentiment_label")["sentiment_key"]
    f["sentiment_key"] = f["sentiment"].map(sent_map).fillna(
        sent_map.get("neutral")).astype("int64")

    f["tweet_count"] = 1
    return f[["date_key", "product_key", "aspect_key", "sentiment_key",
              "tweet_id", "screen_name", "lang", "source", "verified", "is_spike",
              "followers_count", "favorite_count", "retweet_count",
              "engagement_total", "sentiment_score", "tweet_count"]]


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run() -> dict:
    print("[GOLD] silver -> gold/ (galaxy: fact_sales + fact_sentiment)", flush=True)
    sales     = _read_sales("sales")
    customer  = _read_sales("customer")
    product   = _read_sales("product")
    subcat    = _read_sales("productsubcategory")
    cat       = _read_sales("productcategory")
    sentiment = pd.read_parquet(SILVER_SOCIAL / "sentiment.parquet")

    dim_date      = build_dim_date(sales["order_date"], sentiment["event_date"])
    dim_product   = build_dim_product(product, subcat, cat)
    dim_customer  = build_dim_customer(customer)
    dim_channel   = build_dim_channel()
    dim_aspect    = build_dim_aspect()
    dim_sentiment = build_dim_sentiment()

    fact_sales     = build_fact_sales(sales, dim_customer, dim_product)
    fact_sentiment = build_fact_sentiment(sentiment, dim_product, dim_aspect, dim_sentiment)

    counts = {
        "dim_date":       _write(dim_date, "dim_date"),
        "dim_product":    _write(dim_product, "dim_product"),
        "dim_customer":   _write(dim_customer, "dim_customer"),
        "dim_channel":    _write(dim_channel, "dim_channel"),
        "dim_aspect":     _write(dim_aspect, "dim_aspect"),
        "dim_sentiment":  _write(dim_sentiment, "dim_sentiment"),
        "fact_sales":     _write(fact_sales, "fact_sales"),
        "fact_sentiment": _write(fact_sentiment, "fact_sentiment"),
    }
    for k, v in counts.items():
        print(f"  [OK] {k:16s} {v:>8,} rows", flush=True)

    # FK integrity report (Unknown members should absorb all unmatched rows)
    date_keys = set(dim_date["date_key"])
    sales_orphans = {
        "date_key":     int((~fact_sales["date_key"].isin(date_keys)).sum()),
        "customer_key": int(fact_sales["customer_key"].isna().sum()),
        "product_key":  int(fact_sales["product_key"].isna().sum()),
        "channel_key":  int(fact_sales["channel_key"].isna().sum()),
    }
    sent_unknown_prod = int((fact_sentiment["product_key"] == UNKNOWN_KEY).sum())
    print(f"  [CHECK] fact_sales orphan FKs: {sales_orphans}", flush=True)
    print(f"  [CHECK] fact_sentiment unmatched product -> Unknown: "
          f"{sent_unknown_prod:,}/{len(fact_sentiment):,}", flush=True)
    return counts


if __name__ == "__main__":
    run()
