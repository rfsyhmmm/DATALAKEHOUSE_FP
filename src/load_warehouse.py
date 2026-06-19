"""
load_warehouse.py — model/ (DW-ready galaxy) -> PostgreSQL data warehouse.

Promotes the finished lakehouse output (the relational-ready galaxy in model/)
into a real PostgreSQL Data Warehouse. The model/ layer already carries the
relational contract (schema.json: columns + SQL types + PK/FK), so this stage
just materializes it:

    1. ensure the target database exists        (warehouseDB, created if missing)
    2. (re)create the target schema             (dw_sales — dropped & rebuilt)
    3. CREATE TABLE for every dim/fact           (PK + FK, schema-qualified)
    4. bulk-load each Parquet via COPY           (dims first, then facts)
    5. verify row counts + a cross-fact query    (DW is queryable)

Idempotent: rerunning drops and reloads dw_sales cleanly.

    .venv\\Scripts\\python.exe src\\load_warehouse.py
"""

import io
import json
import sys
from pathlib import Path

import pandas as pd
import psycopg2

# --- target warehouse ---
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "warehouseDB"
DB_USER = "postgres"
DB_PASSWORD = "postgres"
TARGET_SCHEMA = "dw_sales"
MAINTENANCE_DB = "postgres"   # used only to CREATE DATABASE if warehouseDB is missing

REPO_ROOT   = Path(__file__).resolve().parent.parent
MODEL       = REPO_ROOT / "model"
SCHEMA_JSON = MODEL / "schema.json"
KEY_SUFFIX  = "_key"


def _conn(dbname: str, autocommit: bool = False):
    c = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=dbname,
                         user=DB_USER, password=DB_PASSWORD)
    c.autocommit = autocommit
    return c


def ensure_database():
    """Create warehouseDB if it does not exist (CREATE DATABASE needs autocommit)."""
    try:
        conn = _conn(MAINTENANCE_DB, autocommit=True)
    except psycopg2.OperationalError as e:
        print(f"[ERROR] cannot reach PostgreSQL at {DB_HOST}:{DB_PORT} — {e}",
              file=sys.stderr)
        sys.exit(1)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if cur.fetchone():
            print(f"  [OK]   database '{DB_NAME}' already exists")
        else:
            cur.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"  [NEW]  database '{DB_NAME}' created")
    conn.close()


def load_spec() -> dict:
    if not SCHEMA_JSON.exists():
        print(f"[ERROR] {SCHEMA_JSON} not found — run src/sales_dw/model.py first.",
              file=sys.stderr)
        sys.exit(1)
    return json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))


def create_table_sql(name: str, spec: dict) -> str:
    """Schema-qualified CREATE TABLE with PK + FK (FKs point inside dw_sales)."""
    pk = spec["primary_key"]
    lines = []
    for col in spec["columns"]:
        c, t = col["name"], col["type"]
        null = "NOT NULL" if (c == pk or c.endswith(KEY_SUFFIX)) else ""
        lines.append(f'    "{c}" {t} {null}'.rstrip())
    lines.append(f'    PRIMARY KEY ("{pk}")')
    for fk in spec["foreign_keys"]:
        ref_table, ref_col = fk["references"].rstrip(")").split("(")
        lines.append(f'    FOREIGN KEY ("{fk["column"]}") '
                     f'REFERENCES "{TARGET_SCHEMA}"."{ref_table}" ("{ref_col}")')
    return f'CREATE TABLE "{TARGET_SCHEMA}"."{name}" (\n' + ",\n".join(lines) + "\n);"


def copy_table(cur, name: str, spec: dict) -> int:
    """Bulk-load model/<name>.parquet into dw_sales.<name> via COPY."""
    df = pd.read_parquet(MODEL / f"{name}.parquet")
    cols = [c["name"] for c in spec["columns"]]
    df = df[cols].copy()
    for col in spec["columns"]:
        c, t = col["name"], col["type"]
        if t == "BOOLEAN":
            df[c] = df[c].map({True: "t", False: "f"})  # Postgres COPY-CSV wants t/f
        elif t == "BIGINT":
            # nullable-int: an Unknown member's <NA> can promote the column to
            # float, which writes as "1.0" and breaks a BIGINT COPY. Int64 writes
            # clean integers and "" (-> NULL) for missing.
            df[c] = df[c].astype("Int64")

    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    collist = ", ".join(f'"{c}"' for c in cols)
    cur.copy_expert(
        f'COPY "{TARGET_SCHEMA}"."{name}" ({collist}) '
        f"FROM STDIN WITH (FORMAT csv, NULL '')", buf)
    return len(df)


def main():
    print(f"=== Load warehouse  ({DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}, "
          f"schema={TARGET_SCHEMA}) ===", flush=True)

    ensure_database()
    spec = load_spec()
    tables = list(spec.keys())   # schema.json is emitted dims-first, then facts

    conn = _conn(DB_NAME)
    try:
        with conn.cursor() as cur:
            print(f"\n[SCHEMA] (re)creating {TARGET_SCHEMA}", flush=True)
            cur.execute(f'DROP SCHEMA IF EXISTS "{TARGET_SCHEMA}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{TARGET_SCHEMA}"')

            print("[DDL] creating tables (dims -> facts)", flush=True)
            for name in tables:
                cur.execute(create_table_sql(name, spec[name]))

            print("[LOAD] bulk-loading model/ parquet via COPY", flush=True)
            for name in tables:
                n = copy_table(cur, name, spec[name])
                print(f"  [OK] {TARGET_SCHEMA}.{name:16s} {n:>8,} rows", flush=True)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _verify()
    print("\n=== Warehouse load complete — warehouseDB is now a queryable DW ===",
          flush=True)


def _verify():
    """Re-read row counts from the DB and run one cross-fact sanity query."""
    conn = _conn(DB_NAME)
    spec = load_spec()
    print("\n[VERIFY] in-database row counts", flush=True)
    with conn, conn.cursor() as cur:
        for name in spec:
            cur.execute(f'SELECT COUNT(*) FROM "{TARGET_SCHEMA}"."{name}"')
            print(f"  {TARGET_SCHEMA}.{name:16s} {cur.fetchone()[0]:>8,}", flush=True)

        # IMPORTANT: aggregate each fact to category grain SEPARATELY, then join.
        # Joining the two facts directly on product_key fans out (every sales line
        # x every tweet for that product) and inflates the revenue sum.
        cur.execute(f"""
            WITH rev AS (
                SELECT p.category, SUM(s.line_total) AS revenue
                FROM "{TARGET_SCHEMA}".fact_sales s
                JOIN "{TARGET_SCHEMA}".dim_product p ON p.product_key = s.product_key
                GROUP BY p.category
            ),
            snt AS (
                SELECT p.category,
                       ROUND(AVG(t.sentiment_score), 3) AS avg_sentiment,
                       COUNT(*) AS tweets
                FROM "{TARGET_SCHEMA}".fact_sentiment t
                JOIN "{TARGET_SCHEMA}".dim_product p ON p.product_key = t.product_key
                GROUP BY p.category
            )
            SELECT rev.category, rev.revenue, snt.avg_sentiment, snt.tweets
            FROM rev JOIN snt ON snt.category = rev.category
            ORDER BY rev.revenue DESC
        """)
        print("\n[VERIFY] cross-fact query (revenue x avg sentiment by category)",
              flush=True)
        for cat, rev, sent, tweets in cur.fetchall():
            print(f"  {cat:14s} revenue={float(rev):>16,.2f}  "
                  f"avg_sentiment={sent}  tweets={tweets:,}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
