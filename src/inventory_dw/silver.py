"""
inventory_dw / silver.py — bronze/csv -> silver/inventory (Parquet)

Cleans & conforms the two inventory source tables:
  * productinventory  (productid, locationid, quantity, modifieddate)
  * location          (locationid, name, costrate)

Output is columnar Parquet in silver/inventory/.
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BRONZE    = REPO_ROOT / "medallion_layer" / "bronze" / "csv"
SILVER    = REPO_ROOT / "medallion_layer" / "silver" / "inventory"


def _read(name: str) -> pd.DataFrame:
    path = BRONZE / f"{name}.csv"
    if not path.exists():
        print(f"  [ERROR] bronze table missing: {path}", file=sys.stderr)
        sys.exit(1)
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
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


def run() -> dict:
    counts = {}
    print("[SILVER] bronze/csv -> silver/inventory (typed, deduped Parquet)")

    # --- productinventory ---
    inv = _read("productinventory")
    inv = inv[["productid", "locationid", "quantity", "modifieddate"]].copy()
    inv = _to_num(inv, ["productid", "locationid", "quantity"], integer=True)
    inv["modifieddate"] = pd.to_datetime(inv["modifieddate"], errors="coerce")
    inv = inv.drop_duplicates(subset=["productid", "locationid"])
    counts["productinventory"] = _write(inv, "productinventory")

    # --- location ---
    loc = _read("location")
    loc = loc[["locationid", "name", "costrate"]].copy()
    loc = _to_num(loc, ["locationid"], integer=True)
    loc = _to_num(loc, ["costrate"])
    loc = loc.drop_duplicates(subset=["locationid"])
    counts["location"] = _write(loc, "location")

    for k, v in counts.items():
        print(f"  [OK] {k:20s} {v:>8,} rows", flush=True)

    return counts


if __name__ == "__main__":
    run()
