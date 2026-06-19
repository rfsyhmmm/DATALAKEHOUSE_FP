"""
sales_dw / model.py — gold/ (Parquet) -> model/ (DW-ready GALAXY)

The MODEL layer is the staging area between the lakehouse gold layer and the
external Data Warehouse. It holds the FINALIZED, READY-TO-SHIP galaxy (fact
constellation) as relational-ready Parquet (clean types, enforced surrogate
keys) plus a schema descriptor so it loads cleanly into any relational DW.

Outputs (model/):
  dim_date / dim_product (conformed) ; dim_customer / dim_channel (sales) ;
  dim_aspect / dim_sentiment (social) ; fact_sales ; fact_sentiment  (.parquet)
  schema.json         — per table: columns+types, primary_key, foreign_keys
  create_tables.sql   — relational DDL (CREATE TABLE + PK/FK) for DW load
"""

import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLD  = REPO_ROOT / "medallion_layer" / "gold"
MODEL = REPO_ROOT / "model"

# Relational contract: column order + SQL types, primary key, foreign keys.
# The full galaxy promoted from gold to the DW-ready model.
SPEC = {
    # --- conformed (shared) dimensions ---
    "dim_date": {
        "pk": "date_key", "fks": [],
        "columns": [("date_key", "BIGINT"), ("full_date", "DATE"), ("day", "BIGINT"),
                    ("month", "BIGINT"), ("month_name", "VARCHAR(20)"),
                    ("quarter", "BIGINT"), ("year", "BIGINT")],
    },
    "dim_product": {
        "pk": "product_key", "fks": [],
        "columns": [("product_key", "BIGINT"), ("product_id", "BIGINT"),
                    ("product_name", "VARCHAR(255)"), ("product_number", "VARCHAR(50)"),
                    ("color", "VARCHAR(50)"), ("size", "VARCHAR(50)"),
                    ("category", "VARCHAR(100)"), ("subcategory", "VARCHAR(100)"),
                    ("standard_cost", "NUMERIC(18,4)"), ("list_price", "NUMERIC(18,4)")],
    },
    # --- sales-private dimensions ---
    "dim_customer": {
        "pk": "customer_key", "fks": [],
        "columns": [("customer_key", "BIGINT"), ("customer_id", "BIGINT"),
                    ("customer_type", "VARCHAR(20)"), ("territory_id", "BIGINT")],
    },
    "dim_channel": {
        "pk": "channel_key", "fks": [],
        "columns": [("channel_key", "BIGINT"), ("channel_name", "VARCHAR(20)")],
    },
    # --- sentiment-private dimensions ---
    "dim_aspect": {
        "pk": "aspect_key", "fks": [],
        "columns": [("aspect_key", "BIGINT"), ("aspect_name", "VARCHAR(50)")],
    },
    "dim_sentiment": {
        "pk": "sentiment_key", "fks": [],
        "columns": [("sentiment_key", "BIGINT"), ("sentiment_label", "VARCHAR(20)"),
                    ("sentiment_score", "BIGINT")],
    },
    # --- facts ---
    "fact_sales": {
        "pk": "sales_key",
        "fks": [("date_key", "dim_date", "date_key"),
                ("customer_key", "dim_customer", "customer_key"),
                ("product_key", "dim_product", "product_key"),
                ("channel_key", "dim_channel", "channel_key")],
        "columns": [("sales_key", "BIGINT"), ("date_key", "BIGINT"),
                    ("customer_key", "BIGINT"), ("product_key", "BIGINT"),
                    ("channel_key", "BIGINT"), ("sales_order_id", "BIGINT"),
                    ("source_type", "VARCHAR(20)"),
                    ("order_qty", "BIGINT"), ("unit_price", "NUMERIC(18,4)"),
                    ("unit_price_discount", "NUMERIC(18,4)"), ("line_total", "NUMERIC(18,4)"),
                    ("sales_count", "BIGINT")],
    },
    "fact_sentiment": {
        "pk": "sentiment_fact_key",
        "fks": [("date_key", "dim_date", "date_key"),
                ("product_key", "dim_product", "product_key"),
                ("aspect_key", "dim_aspect", "aspect_key"),
                ("sentiment_key", "dim_sentiment", "sentiment_key")],
        "columns": [("sentiment_fact_key", "BIGINT"), ("date_key", "BIGINT"),
                    ("product_key", "BIGINT"), ("aspect_key", "BIGINT"),
                    ("sentiment_key", "BIGINT"), ("tweet_id", "VARCHAR(32)"),
                    ("screen_name", "VARCHAR(100)"), ("lang", "VARCHAR(10)"),
                    ("source", "VARCHAR(50)"), ("verified", "BOOLEAN"),
                    ("is_spike", "BOOLEAN"), ("followers_count", "BIGINT"),
                    ("favorite_count", "BIGINT"), ("retweet_count", "BIGINT"),
                    ("engagement_total", "BIGINT"), ("sentiment_score", "BIGINT"),
                    ("tweet_count", "BIGINT")],
    },
}

# DDL emit order: all dims before facts (FK dependency)
TABLE_ORDER = ["dim_date", "dim_product", "dim_customer", "dim_channel",
               "dim_aspect", "dim_sentiment", "fact_sales", "fact_sentiment"]
KEY_SUFFIX = "_key"


def _load_gold(name: str) -> pd.DataFrame:
    path = GOLD / f"{name}.parquet"
    if not path.exists():
        print(f"  [ERROR] gold table missing: {path}", file=sys.stderr)
        sys.exit(1)
    return pd.read_parquet(path)


def _finalize(name: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    pk = SPEC[name]["pk"]
    if pk not in df.columns:  # gold facts ship without their surrogate PK — add it here
        df.insert(0, pk, range(1, len(df) + 1))
    cols = [c for c, _ in SPEC[name]["columns"]]
    df = df[cols]
    # enforce surrogate/foreign keys as non-null int64
    for c in cols:
        if c.endswith(KEY_SUFFIX):
            if df[c].isna().any():
                sys.exit(f"  [FAIL] null key '{c}' in {name} — cannot ship to DW.")
            df[c] = df[c].astype("int64")
    return df


def _validate_fks(tables: dict):
    for name, spec in SPEC.items():
        for col, ref_table, ref_col in spec["fks"]:
            valid = set(tables[ref_table][ref_col])
            missing = int((~tables[name][col].isin(valid)).sum())
            if missing:
                sys.exit(f"  [FAIL] {missing} orphan {name}.{col} -> {ref_table}.{ref_col}")


def write_schema_json():
    schema = {}
    for name, spec in SPEC.items():
        schema[name] = {
            "columns": [{"name": c, "type": t} for c, t in spec["columns"]],
            "primary_key": spec["pk"],
            "foreign_keys": [{"column": c, "references": f"{rt}({rc})"}
                             for c, rt, rc in spec["fks"]],
        }
    (MODEL / "schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")


def write_create_sql():
    lines = ["-- Sales DW relational model (generated by model.py)",
             "-- Load order respects FK dependencies (dims first, fact last).", ""]
    for name in TABLE_ORDER:
        spec = SPEC[name]
        col_defs = []
        for c, t in spec["columns"]:
            null = "NOT NULL" if (c == spec["pk"] or c.endswith(KEY_SUFFIX)) else ""
            col_defs.append(f"    {c:<22} {t} {null}".rstrip())
        col_defs.append(f"    PRIMARY KEY ({spec['pk']})")
        for c, rt, rc in spec["fks"]:
            col_defs.append(f"    FOREIGN KEY ({c}) REFERENCES {rt} ({rc})")
        lines.append(f"CREATE TABLE {name} (")
        lines.append(",\n".join(col_defs))
        lines.append(");\n")
    (MODEL / "create_tables.sql").write_text("\n".join(lines), encoding="utf-8")


def run() -> dict:
    MODEL.mkdir(parents=True, exist_ok=True)
    print("[MODEL] gold/ -> model/ (relational-ready galaxy + schema)")

    tables = {name: _finalize(name, _load_gold(name)) for name in SPEC}
    _validate_fks(tables)

    counts = {}
    for name in TABLE_ORDER:
        tables[name].to_parquet(MODEL / f"{name}.parquet", index=False)
        counts[name] = len(tables[name])
        print(f"  [OK] {name:14s} {len(tables[name]):>8,} rows")

    write_schema_json()
    write_create_sql()
    print("  [OK] schema.json + create_tables.sql written")
    print("  [CHECK] referential integrity OK — model ready to ship to DW")
    return counts


if __name__ == "__main__":
    run()
