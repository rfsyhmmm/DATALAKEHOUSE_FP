"""
inventory_dw / gold.py — silver/inventory (Parquet) -> gold/ (Parquet)

Extends the existing galaxy schema with inventory-specific objects:

  new dimension : dim_location  (warehouse / bin location)
  new fact      : fact_inventory (grain: product x location snapshot)

Runs AFTER sales_dw/gold.py so it can read the already-built gold/dim_product.parquet
and gold/dim_date.parquet for FK resolution. Both conformed dimensions are shared —
this module only READS them, never overwrites them.

Natural key for incremental upsert: inv_key = "{productid}_{locationid}"
(one snapshot per product-location pair per pipeline run, tracked by batch_key).
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # src/ for batch_window
import batch_window

REPO_ROOT        = Path(__file__).resolve().parent.parent.parent
SILVER_INVENTORY = REPO_ROOT / "medallion_layer" / "silver" / "inventory"
GOLD             = REPO_ROOT / "medallion_layer" / "gold"

UNKNOWN_KEY = -1


def _read_inv(name: str) -> pd.DataFrame:
    path = SILVER_INVENTORY / f"{name}.parquet"
    if not path.exists():
        print(f"  [ERROR] silver/inventory table missing: {path}", file=sys.stderr)
        sys.exit(1)
    return pd.read_parquet(path)


def _read_gold(name: str) -> pd.DataFrame:
    path = GOLD / f"{name}.parquet"
    if not path.exists():
        print(f"  [ERROR] gold table missing: {path}\n"
              f"          Run src/sales_dw/gold.py before inventory_dw/gold.py.",
              file=sys.stderr)
        sys.exit(1)
    return pd.read_parquet(path)


def _write(df: pd.DataFrame, name: str) -> int:
    GOLD.mkdir(parents=True, exist_ok=True)
    df.to_parquet(GOLD / f"{name}.parquet", index=False)
    return len(df)


def _date_key(dt: pd.Series) -> pd.Series:
    """YYYYMMDD int key; NaT -> UNKNOWN_KEY."""
    d = pd.to_datetime(dt, errors="coerce").dt.normalize()
    key = (d.dt.year * 10000 + d.dt.month * 100 + d.dt.day)
    return key.fillna(UNKNOWN_KEY).astype("int64")


def build_dim_location(location: pd.DataFrame) -> pd.DataFrame:
    loc = location.copy()
    loc = loc.rename(columns={"name": "location_name", "costrate": "cost_rate"})
    loc = loc.rename(columns={"locationid": "location_id"})
    loc = loc.sort_values("location_id").reset_index(drop=True)
    loc.insert(0, "location_key", loc.index + 1)
    cols = ["location_key", "location_id", "location_name", "cost_rate"]
    unknown = {"location_key": UNKNOWN_KEY, "location_id": pd.NA,
               "location_name": "Unknown", "cost_rate": 0.0}
    loc = pd.concat([pd.DataFrame([unknown]), loc[cols]], ignore_index=True)
    return loc[cols]


def build_fact_inventory(inv: pd.DataFrame, dim_product: pd.DataFrame,
                         dim_location: pd.DataFrame, dim_date: pd.DataFrame) -> pd.DataFrame:
    f = inv.copy()

    # FK: product_key via product_id
    prod_map = (dim_product[dim_product["product_key"] != UNKNOWN_KEY]
                .set_index("product_id")["product_key"])
    f["product_key"] = f["productid"].map(prod_map).fillna(UNKNOWN_KEY).astype("int64")

    # FK: location_key via locationid
    loc_map = (dim_location[dim_location["location_key"] != UNKNOWN_KEY]
               .set_index("location_id")["location_key"])
    f["location_key"] = f["locationid"].map(loc_map).fillna(UNKNOWN_KEY).astype("int64")

    # FK: date_key via modifieddate; fall back to UNKNOWN_KEY if date not in dim_date
    f["date_key"] = _date_key(f["modifieddate"])
    valid_dates = set(dim_date["date_key"])
    f["date_key"] = f["date_key"].where(f["date_key"].isin(valid_dates), UNKNOWN_KEY)

    f["quantity_on_hand"] = f["quantity"].astype("Int64")
    f["inv_key"] = (f["productid"].astype(str) + "_" + f["locationid"].astype(str))

    return f[["date_key", "product_key", "location_key", "quantity_on_hand", "inv_key"]]


def run(start=None, end=None, label="full") -> dict:
    print("[GOLD] silver/inventory -> gold/ (dim_location + fact_inventory)", flush=True)

    inv      = _read_inv("productinventory")
    location = _read_inv("location")

    dim_product  = _read_gold("dim_product")
    dim_date     = _read_gold("dim_date")

    dim_location   = build_dim_location(location)
    fact_inventory = build_fact_inventory(inv, dim_product, dim_location, dim_date)

    counts = {
        "dim_location":   _write(dim_location,   "dim_location"),
        "fact_inventory": _write(fact_inventory,  "fact_inventory"),
    }
    for k, v in counts.items():
        print(f"  [OK] {k:18s} {v:>8,} rows", flush=True)

    # FK integrity check
    unknown_prod = int((fact_inventory["product_key"] == UNKNOWN_KEY).sum())
    unknown_loc  = int((fact_inventory["location_key"] == UNKNOWN_KEY).sum())
    unknown_date = int((fact_inventory["date_key"] == UNKNOWN_KEY).sum())
    print(f"  [CHECK] fact_inventory unmatched -> Unknown: "
          f"product={unknown_prod}  location={unknown_loc}  date={unknown_date}", flush=True)

    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build inventory gold layer.")
    batch_window.add_window_args(ap)
    args = ap.parse_args(argv)
    start, end, _as_of, label = batch_window.window_from_args(args)
    run(start, end, label)


if __name__ == "__main__":
    main()
