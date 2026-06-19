import os
import sys
from datetime import datetime

import pandas as pd
import psycopg2

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "adventureworks_local"
DB_USER = "postgres"
DB_PASSWORD = "postgres"

BASE_OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Maps (schema, table) -> output subfolder name
EXTRACTS = [
    ("production", "product",            "product_and_sub"),
    ("production", "productsubcategory", "product_and_sub"),
    ("production", "productcategory",    "product_and_sub"),
    ("person",     "address",            "address"),
    ("person",     "person",             "person"),
    ("purchasing", "shipmethod",         "shipmethod"),
]


def get_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


def extract_table(conn, schema, table, subfolder, timestamp):
    out_dir = os.path.join(BASE_OUTPUT_DIR, subfolder)
    os.makedirs(out_dir, exist_ok=True)
    filepath = os.path.join(out_dir, f"{table}_{timestamp}.csv")
    df = pd.read_sql(f'SELECT * FROM "{schema}"."{table}"', conn)
    df.to_csv(filepath, index=False, encoding="utf-8")
    return len(df), filepath


def main():
    timestamp = datetime.now().strftime("%Y%m%d%H%M")

    print(f"Connecting to {DB_NAME} on {DB_HOST}:{DB_PORT}...")
    try:
        conn = get_connection()
    except psycopg2.OperationalError as e:
        print(f"[ERROR] Could not connect: {e}", file=sys.stderr)
        sys.exit(1)

    for schema, table, subfolder in EXTRACTS:
        try:
            row_count, filepath = extract_table(conn, schema, table, subfolder, timestamp)
            label = f"{schema}.{table}"
            print(f"  [OK] {label:45s} -> {row_count:>7,} rows -> {os.path.relpath(filepath)}")
        except Exception as e:
            print(f"  [FAIL] {schema}.{table}: {e}", file=sys.stderr)

    conn.close()
    print(f"\nDone. Files written to: {BASE_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
