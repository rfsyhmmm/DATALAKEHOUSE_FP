"""
Split sales order tables into online_store_csv / offline_store_csv
based on the onlineorderflag column in salesorderheader.

Tables split:
  - salesorderheader        (primary split key)
  - salesorderdetail        (filtered by matching salesorderid)
  - salesorderheadersalesreason (filtered by matching salesorderid)

Output folders (inside staging_extraction/):
  online_store_csv/<table>/
  offline_store_csv/<table>/
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

STAGING = Path(__file__).resolve().parent

ONLINE_DIR  = STAGING / "online_store_csv"
OFFLINE_DIR = STAGING / "offline_store_csv"

# Tables that carry salesorderid as a foreign key (split by matching IDs)
RELATED_TABLES = [
    "salesorderdetail",
    "salesorderheadersalesreason",
]


def latest_in(subdir: str, pattern: str) -> Path | None:
    folder = STAGING / subdir
    files = sorted(folder.glob(pattern)) if folder.exists() else []
    return files[-1] if files else None


def save(df: pd.DataFrame, base_dir: Path, table: str, timestamp: str) -> Path:
    out_dir = base_dir / table
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "online" if "online" in base_dir.name else "offline"
    path = out_dir / f"{table}_{suffix}_{timestamp}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def main():
    timestamp = datetime.now().strftime("%Y%m%d%H%M")

    # --- salesorderheader ---
    header_path = latest_in("salesorderheader", "salesorderheader_*.csv")
    if header_path is None:
        print("[ERROR] salesorderheader CSV not found. Run extract_sales.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {header_path.name} ...")
    header = pd.read_csv(header_path, dtype=str)

    flag_col = "onlineorderflag"
    if flag_col not in header.columns:
        print(f"[ERROR] Column '{flag_col}' not found in {header_path.name}.", file=sys.stderr)
        sys.exit(1)

    # Normalise: "True"/"False" strings (pandas exports booleans as strings)
    header[flag_col] = header[flag_col].str.strip().str.lower()
    online_header  = header[header[flag_col] == "true"].copy()
    offline_header = header[header[flag_col] == "false"].copy()

    print(f"  salesorderheader  -> online: {len(online_header):,}  |  offline: {len(offline_header):,}")

    online_ids  = set(online_header["salesorderid"].astype(str))
    offline_ids = set(offline_header["salesorderid"].astype(str))

    p = save(online_header,  ONLINE_DIR,  "salesorderheader", timestamp)
    print(f"  [OK] {p.relative_to(STAGING.parent.parent)}")
    p = save(offline_header, OFFLINE_DIR, "salesorderheader", timestamp)
    print(f"  [OK] {p.relative_to(STAGING.parent.parent)}")

    # --- related tables ---
    for table in RELATED_TABLES:
        src_path = latest_in(table, f"{table}_*.csv")
        if src_path is None:
            print(f"[WARN] {table} CSV not found — skipping.")
            continue

        print(f"Reading {src_path.name} ...")
        df = pd.read_csv(src_path, dtype=str)

        online_rows  = df[df["salesorderid"].astype(str).isin(online_ids)]
        offline_rows = df[df["salesorderid"].astype(str).isin(offline_ids)]

        print(f"  {table}  -> online: {len(online_rows):,}  |  offline: {len(offline_rows):,}")

        p = save(online_rows,  ONLINE_DIR,  table, timestamp)
        print(f"  [OK] {p.relative_to(STAGING.parent.parent)}")
        p = save(offline_rows, OFFLINE_DIR, table, timestamp)
        print(f"  [OK] {p.relative_to(STAGING.parent.parent)}")

    print("\nDone.")
    print(f"  Online  -> {ONLINE_DIR.relative_to(STAGING.parent.parent)}")
    print(f"  Offline -> {OFFLINE_DIR.relative_to(STAGING.parent.parent)}")


if __name__ == "__main__":
    main()
