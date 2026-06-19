"""
sales_dw / silver.py — bronze/sales (CSV) -> silver/sales (Parquet)

Cleans & conforms the raw bronze tables:
  * select relevant columns, lower-case already done in bronze
  * type-cast (dates, integers, floats)
  * deduplicate on natural key
  * derive line_total on salesorderdetail (linetotal is not exported by AdventureWorks)

Output is columnar Parquet — efficient and Power-BI friendly.
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BRONZE = REPO_ROOT / "medallion_layer" / "bronze" / "csv"
SILVER = REPO_ROOT / "medallion_layer" / "silver" / "sales"
DOC_SILVER = REPO_ROOT / "medallion_layer" / "silver" / "document"  # parsed PDF invoices

# Conformed column order for the unified sales fact (online CSV + offline PDF).
SALES_COLUMNS = [
    "salesorderid", "salesorderdetailid", "order_date", "ship_date",
    "customer_id", "product_id", "product_number", "product_name",
    "order_qty", "unit_price", "unit_price_discount", "line_total",
    "channel", "source_type", "truncated_flag",
]


def _read(name: str) -> pd.DataFrame:
    path = BRONZE / f"{name}.csv"
    if not path.exists():
        print(f"  [ERROR] bronze table missing: {path}", file=sys.stderr)
        sys.exit(1)
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]  # bronze keeps raw headers
    return df


def _to_num(df: pd.DataFrame, cols: list, integer=False):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if integer:
            df[c] = df[c].astype("Int64")
    return df


def _write(df: pd.DataFrame, name: str) -> int:
    SILVER.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SILVER / f"{name}.parquet", index=False)
    return len(df)


def _read_doc(name: str) -> pd.DataFrame:
    path = DOC_SILVER / f"{name}.parquet"
    if not path.exists():
        print(f"  [ERROR] document silver missing: {path}\n"
              f"          Run src/document_dw/silver.py before building sales.parquet "
              f"(offline orders come from the parsed PDF invoices).", file=sys.stderr)
        sys.exit(1)
    return pd.read_parquet(path)


def build_sales(header: pd.DataFrame, detail: pd.DataFrame,
                product: pd.DataFrame) -> pd.DataFrame:
    """Unified, line-grain sales fact = ONLINE (CSV) + OFFLINE (parsed PDF invoices).

    Online orders are taken from the CSV header/detail (onlineorderflag == True).
    Offline orders are taken ONLY from the parsed PDF invoices in silver/document
    (per design). The two channels are disjoint by salesorderid, so the union does
    not double-count. Both sides are conformed to SALES_COLUMNS.
    """
    # product_number / product_name lookups (shared product identity across channels)
    pid_to_num  = product.dropna(subset=["productid"]).drop_duplicates("productid") \
                         .set_index("productid")["productnumber"]
    pid_to_name = product.dropna(subset=["productid"]).drop_duplicates("productid") \
                         .set_index("productid")["name"]
    num_to_pid  = product.dropna(subset=["productnumber"]).drop_duplicates("productnumber") \
                         .set_index("productnumber")["productid"]

    # --- ONLINE: CSV header (onlineorderflag) join detail ---
    oh = header.loc[header["onlineorderflag"] == True,
                    ["salesorderid", "orderdate", "shipdate", "customerid"]]
    on = detail.merge(oh, on="salesorderid", how="inner")
    online = pd.DataFrame({
        "salesorderid":        on["salesorderid"].astype("Int64"),
        "salesorderdetailid":  on["salesorderdetailid"].astype("Int64"),
        "order_date":          pd.to_datetime(on["orderdate"], errors="coerce"),
        "ship_date":           pd.to_datetime(on["shipdate"], errors="coerce"),
        "customer_id":         on["customerid"].astype("Int64"),
        "product_id":          on["productid"].astype("Int64"),
        "product_number":      on["productid"].map(pid_to_num),
        "product_name":        on["productid"].map(pid_to_name),
        "order_qty":           on["orderqty"].astype("Int64"),
        "unit_price":          on["unitprice"].astype("float"),
        "unit_price_discount": on["unitpricediscount"].astype("float"),
        "line_total":          on["line_total"].astype("float"),
        "channel":             "Online",
        "source_type":         "csv_online",
        "truncated_flag":      False,
    })

    # --- OFFLINE: parsed PDF invoice lines join invoice header ---
    il = _read_doc("invoice_line")
    ih = _read_doc("invoice_header")[["salesorderid", "order_date", "ship_date",
                                      "customer_id", "truncated_flag"]]
    off = il.merge(ih, on="salesorderid", how="left")
    offline = pd.DataFrame({
        "salesorderid":        off["salesorderid"].astype("Int64"),
        "salesorderdetailid":  pd.array([pd.NA] * len(off), dtype="Int64"),  # PDF has only line_no
        "order_date":          pd.to_datetime(off["order_date"], errors="coerce"),
        "ship_date":           pd.to_datetime(off["ship_date"], errors="coerce"),
        "customer_id":         off["customer_id"].astype("Int64"),
        "product_id":          off["product_number"].map(num_to_pid).astype("Int64"),
        "product_number":      off["product_number"],
        "product_name":        off["product_name"],
        "order_qty":           off["qty"].astype("Int64"),
        "unit_price":          off["unit_price"].astype("float"),
        "unit_price_discount": off["disc_pct"].astype("float"),
        "line_total":          off["line_total"].astype("float"),
        "channel":             "Offline",
        "source_type":         "pdf_offline",
        "truncated_flag":      off["truncated_flag"].fillna(False).astype(bool),
    })

    sales = pd.concat([online[SALES_COLUMNS], offline[SALES_COLUMNS]],
                      ignore_index=True)
    return sales


def run() -> dict:
    counts = {}
    print("[SILVER] bronze/sales -> silver/sales (typed, deduped Parquet)")

    # --- salesorderheader ---
    h = _read("salesorderheader")
    h = h[["salesorderid", "orderdate", "duedate", "shipdate", "onlineorderflag",
           "customerid", "salespersonid", "territoryid",
           "subtotal", "taxamt", "freight", "totaldue"]].copy()
    h = _to_num(h, ["salesorderid", "customerid", "salespersonid", "territoryid"], integer=True)
    h = _to_num(h, ["subtotal", "taxamt", "freight", "totaldue"])
    for d in ["orderdate", "duedate", "shipdate"]:
        h[d] = pd.to_datetime(h[d], errors="coerce")
    h["onlineorderflag"] = h["onlineorderflag"].str.strip().str.lower().isin(["true", "1", "t"])
    h = h.drop_duplicates(subset=["salesorderid"])
    counts["salesorderheader"] = _write(h, "salesorderheader")

    # --- salesorderdetail (+ derived line_total) ---
    d = _read("salesorderdetail")
    d = d[["salesorderid", "salesorderdetailid", "orderqty", "productid",
           "unitprice", "unitpricediscount"]].copy()
    d = _to_num(d, ["salesorderid", "salesorderdetailid", "orderqty", "productid"], integer=True)
    d = _to_num(d, ["unitprice", "unitpricediscount"])
    d["unitpricediscount"] = d["unitpricediscount"].fillna(0.0)
    d["line_total"] = (d["orderqty"].astype("float") * d["unitprice"]
                       * (1.0 - d["unitpricediscount"])).round(4)
    d = d.drop_duplicates(subset=["salesorderdetailid"])
    counts["salesorderdetail"] = _write(d, "salesorderdetail")

    # --- customer ---
    c = _read("customer")
    c = c[["customerid", "personid", "storeid", "territoryid"]].copy()
    c = _to_num(c, ["customerid", "personid", "storeid", "territoryid"], integer=True)
    c = c.drop_duplicates(subset=["customerid"])
    counts["customer"] = _write(c, "customer")

    # --- product ---
    p = _read("product")
    p = p[["productid", "name", "productnumber", "color", "size",
           "standardcost", "listprice", "productsubcategoryid"]].copy()
    p = _to_num(p, ["productid", "productsubcategoryid"], integer=True)
    p = _to_num(p, ["standardcost", "listprice"])
    p = p.drop_duplicates(subset=["productid"])
    counts["product"] = _write(p, "product")

    # --- productsubcategory ---
    sc = _read("productsubcategory")
    sc = sc[["productsubcategoryid", "productcategoryid", "name"]].copy()
    sc = _to_num(sc, ["productsubcategoryid", "productcategoryid"], integer=True)
    sc = sc.drop_duplicates(subset=["productsubcategoryid"])
    counts["productsubcategory"] = _write(sc, "productsubcategory")

    # --- productcategory ---
    cat = _read("productcategory")
    cat = cat[["productcategoryid", "name"]].copy()
    cat = _to_num(cat, ["productcategoryid"], integer=True)
    cat = cat.drop_duplicates(subset=["productcategoryid"])
    counts["productcategory"] = _write(cat, "productcategory")

    # --- salesterritory ---
    t = _read("salesterritory")
    keep = [col for col in ["territoryid", "name", "countryregioncode", "group"] if col in t.columns]
    t = t[keep].copy()
    t = _to_num(t, ["territoryid"], integer=True)
    t = t.drop_duplicates(subset=["territoryid"])
    counts["salesterritory"] = _write(t, "salesterritory")

    # --- unified sales fact (online CSV + offline PDF) ---
    sales = build_sales(h, d, p)
    counts["sales"] = _write(sales, "sales")

    for k, v in counts.items():
        print(f"  [OK] {k:20s} {v:>8,} rows", flush=True)

    n_online  = int((sales["channel"] == "Online").sum())
    n_offline = int((sales["channel"] == "Offline").sum())
    n_trunc   = int(sales["truncated_flag"].sum())
    n_nopid   = int(sales["product_id"].isna().sum())
    print(f"  [INFO] sales.parquet  online={n_online:,}  offline={n_offline:,}  "
          f"(offline truncated lines flagged via header={n_trunc:,}; "
          f"unmatched product_id={n_nopid:,})", flush=True)
    return counts


if __name__ == "__main__":
    run()
