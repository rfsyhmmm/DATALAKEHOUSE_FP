import os
import sys
from datetime import datetime

import pandas as pd
import psycopg2

# --- Connection config ---
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "adventureworks_local"
DB_USER = "postgres"
DB_PASSWORD = "postgres"
TARGET_SCHEMA = "sales"

BASE_OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_schema_tables(conn, schema: str) -> list[str]:
    query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    with conn.cursor() as cur:
        cur.execute(query, (schema,))
        return [row[0] for row in cur.fetchall()]


def extract_table(conn, schema: str, table: str, output_dir: str, timestamp: str) -> int:
    table_dir = os.path.join(output_dir, table)
    os.makedirs(table_dir, exist_ok=True)

    filename = f"{table}_{timestamp}.csv"
    filepath = os.path.join(table_dir, filename)

    df = pd.read_sql(f'SELECT * FROM "{schema}"."{table}"', conn)
    df.to_csv(filepath, index=False, encoding="utf-8")

    return len(df), filepath


def main():
    timestamp = datetime.now().strftime("%Y%m%d%H%M")

    print(f"Connecting to {DB_NAME} on {DB_HOST}:{DB_PORT}...")
    try:
        conn = get_connection()
    except psycopg2.OperationalError as e:
        print(f"[ERROR] Could not connect to database: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Discovering tables in schema '{TARGET_SCHEMA}'...")
    tables = get_schema_tables(conn, TARGET_SCHEMA)
    if not tables:
        print(f"[WARNING] No tables found in schema '{TARGET_SCHEMA}'.")
        conn.close()
        return

    print(f"Found {len(tables)} table(s): {', '.join(tables)}\n")

    for table in tables:
        try:
            row_count, filepath = extract_table(conn, TARGET_SCHEMA, table, BASE_OUTPUT_DIR, timestamp)
            print(f"  [OK] {TARGET_SCHEMA}.{table:40s} -> {row_count:>7,} rows -> {os.path.relpath(filepath)}")
        except Exception as e:
            print(f"  [FAIL] {TARGET_SCHEMA}.{table}: {e}", file=sys.stderr)

    conn.close()
    print(f"\nDone. Files written to: {BASE_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
