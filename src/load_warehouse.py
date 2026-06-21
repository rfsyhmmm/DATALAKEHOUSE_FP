"""
load_warehouse.py — model/ (DW-ready galaxy) -> PostgreSQL data warehouse (INCREMENTAL).

Promotes the finished lakehouse output (the relational-ready galaxy in model/) into a
real PostgreSQL Data Warehouse, one BATCH at a time. The model/ layer carries the
relational contract (schema.json: columns + SQL types + PK/FK); this stage materializes
it and ACCUMULATES across batches:

    1. ensure the database exists                (warehouseDB, created if missing)
    2. ensure schema + tables exist              (created on first batch; never dropped)
    3. open a batch                              (dim_batch row: batch_id, as_of, label, ts)
    4. upsert dims                               (INSERT ON CONFLICT DO NOTHING — stable keys)
    5. upsert facts by NATURAL KEY               (sales_line_id / tweet_id), tag with batch_key
    6. verify per-batch + cumulative counts + a cross-fact query

Surrogate fact PKs are DB-assigned (IDENTITY) so they stay unique across batches. A later
batch with a wider as-of window simply inserts the new rows; existing rows are untouched.

    .venv\\Scripts\\python.exe src\\load_warehouse.py                 # append a batch
    .venv\\Scripts\\python.exe src\\load_warehouse.py --full-refresh  # drop + reload as batch 1
    .venv\\Scripts\\python.exe src\\load_warehouse.py --as-of 2024-12-31 --window-label "full<=2024-12-31"
"""

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))  # src/ for batch_window
import batch_window

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

# stable business key per fact, used for incremental natural-key upsert
NATURAL_KEY = {"fact_sales": "sales_line_id", "fact_sentiment": "tweet_id",
               "fact_inventory": "inv_bk"}

DIM_BATCH_DDL = f'''
CREATE TABLE IF NOT EXISTS "{TARGET_SCHEMA}"."dim_batch" (
    "batch_key"      BIGINT GENERATED ALWAYS AS IDENTITY,
    "batch_id"       BIGINT NOT NULL,
    "as_of_date"     DATE,
    "window_label"   VARCHAR(40),
    "load_timestamp" TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY ("batch_key")
);'''


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
    """CREATE TABLE IF NOT EXISTS. Facts get an IDENTITY PK + a batch_key FK so they
    can accumulate across batches; dims keep their natural surrogate PK."""
    pk = spec["primary_key"]
    is_fact = name.startswith("fact_")
    lines = []
    for col in spec["columns"]:
        c, t = col["name"], col["type"]
        if is_fact and c == pk:
            lines.append(f'    "{c}" BIGINT GENERATED ALWAYS AS IDENTITY')
        else:
            null = "NOT NULL" if (c == pk or c.endswith(KEY_SUFFIX)) else ""
            lines.append(f'    "{c}" {t} {null}'.rstrip())
    if is_fact:
        lines.append('    "batch_key" BIGINT NOT NULL')
    lines.append(f'    PRIMARY KEY ("{pk}")')
    for fk in spec["foreign_keys"]:
        ref_table, ref_col = fk["references"].rstrip(")").split("(")
        lines.append(f'    FOREIGN KEY ("{fk["column"]}") '
                     f'REFERENCES "{TARGET_SCHEMA}"."{ref_table}" ("{ref_col}")')
    if is_fact:
        lines.append(f'    FOREIGN KEY ("batch_key") '
                     f'REFERENCES "{TARGET_SCHEMA}"."dim_batch" ("batch_key")')
    return f'CREATE TABLE IF NOT EXISTS "{TARGET_SCHEMA}"."{name}" (\n' + ",\n".join(lines) + "\n);"


def _df_for(name: str, spec: dict) -> pd.DataFrame:
    """Read model/<name>.parquet, ordered to SPEC, COPY-friendly (t/f bools, Int64)."""
    df = pd.read_parquet(MODEL / f"{name}.parquet")
    cols = [c["name"] for c in spec["columns"]]
    df = df[cols].copy()
    for col in spec["columns"]:
        c, t = col["name"], col["type"]
        if t == "BOOLEAN":
            df[c] = df[c].map({True: "t", False: "f"})
        elif t == "BIGINT":
            df[c] = df[c].astype("Int64")  # avoid "1.0" from <NA>-promoted floats
    return df


def _stage(cur, name: str, spec: dict) -> int:
    """Create a TEMP staging table matching the SPEC columns and COPY the parquet in."""
    coldefs = ",\n".join(f'    "{c["name"]}" {c["type"]}' for c in spec["columns"])
    cur.execute(f'CREATE TEMP TABLE "stg_{name}" (\n{coldefs}\n) ON COMMIT DROP;')
    df = _df_for(name, spec)
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    collist = ", ".join(f'"{c["name"]}"' for c in spec["columns"])
    cur.copy_expert(f'COPY "stg_{name}" ({collist}) FROM STDIN WITH (FORMAT csv, NULL \'\')', buf)
    return len(df)


def upsert_dim(cur, name: str, spec: dict) -> int:
    """Insert dim rows, skipping PKs already present (stable keys -> mostly no-op)."""
    _stage(cur, name, spec)
    cols = ", ".join(f'"{c["name"]}"' for c in spec["columns"])
    pk = spec["primary_key"]
    cur.execute(f'INSERT INTO "{TARGET_SCHEMA}"."{name}" ({cols}) '
                f'SELECT {cols} FROM "stg_{name}" '
                f'ON CONFLICT ("{pk}") DO NOTHING;')
    return cur.rowcount


def upsert_fact(cur, name: str, spec: dict, batch_key: int) -> int:
    """Insert only fact rows whose natural key is new; tag them with batch_key.
    The surrogate PK is omitted so the DB assigns it (IDENTITY)."""
    _stage(cur, name, spec)
    pk = spec["primary_key"]
    nk = NATURAL_KEY[name]
    cols = [c["name"] for c in spec["columns"] if c["name"] != pk]   # drop IDENTITY PK
    sel = ", ".join(f's."{c}"' for c in cols)
    ins = ", ".join(f'"{c}"' for c in cols)
    cur.execute(
        f'INSERT INTO "{TARGET_SCHEMA}"."{name}" ({ins}, "batch_key") '
        f'SELECT {sel}, {batch_key} FROM "stg_{name}" s '
        f'WHERE NOT EXISTS (SELECT 1 FROM "{TARGET_SCHEMA}"."{name}" f '
        f'                  WHERE f."{nk}" = s."{nk}");')
    return cur.rowcount


def open_batch(cur, as_of, label: str) -> tuple[int, int]:
    """Insert a dim_batch row; return (batch_id, batch_key)."""
    cur.execute(f'SELECT COALESCE(MAX("batch_id"), 0) + 1 FROM "{TARGET_SCHEMA}"."dim_batch"')
    batch_id = cur.fetchone()[0]
    cur.execute(
        f'INSERT INTO "{TARGET_SCHEMA}"."dim_batch" ("batch_id", "as_of_date", "window_label") '
        f'VALUES (%s, %s, %s) RETURNING "batch_key"', (batch_id, as_of, label))
    return batch_id, cur.fetchone()[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Load model/ into warehouseDB (incremental batch).")
    ap.add_argument("--full-refresh", action="store_true",
                    help="drop dw_sales and reload from scratch as batch 1")
    batch_window.add_window_args(ap)
    args = ap.parse_args(argv)

    _start, _end, as_of_date, label = batch_window.window_from_args(args)
    as_of = as_of_date.isoformat()

    print(f"=== Load warehouse  ({DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}, "
          f"schema={TARGET_SCHEMA})  batch as-of={as_of} ===", flush=True)

    ensure_database()
    spec = load_spec()
    dims  = [n for n in spec if n.startswith("dim_")]
    facts = [n for n in spec if n.startswith("fact_")]

    conn = _conn(DB_NAME)
    try:
        with conn.cursor() as cur:
            if args.full_refresh:
                print(f"[SCHEMA] --full-refresh: dropping {TARGET_SCHEMA}", flush=True)
                cur.execute(f'DROP SCHEMA IF EXISTS "{TARGET_SCHEMA}" CASCADE')
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{TARGET_SCHEMA}"')
            cur.execute(DIM_BATCH_DDL)
            for name in dims + facts:               # dims (referenced) before facts
                cur.execute(create_table_sql(name, spec[name]))

            batch_id, batch_key = open_batch(cur, as_of, label)
            print(f"[BATCH] #{batch_id}  label='{label}'  (batch_key={batch_key})", flush=True)

            print("[DIMS] upsert (insert new members only)", flush=True)
            for name in dims:
                n = upsert_dim(cur, name, spec[name])
                print(f"  [OK] {name:18s} +{n:>6,} new rows", flush=True)

            print("[FACTS] upsert by natural key (insert new rows, tag batch)", flush=True)
            for name in facts:
                n = upsert_fact(cur, name, spec[name], batch_key)
                print(f"  [OK] {name:18s} +{n:>8,} new rows  (key={NATURAL_KEY[name]})",
                      flush=True)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _verify()
    print("\n=== Warehouse batch loaded — warehouseDB accumulated ===", flush=True)


def _verify():
    """Re-read row counts from the DB and run one cross-fact sanity query."""
    conn = _conn(DB_NAME)
    spec = load_spec()
    print("\n[VERIFY] batches loaded so far", flush=True)
    with conn, conn.cursor() as cur:
        cur.execute(f'SELECT "batch_id", "window_label", "as_of_date", "load_timestamp" '
                    f'FROM "{TARGET_SCHEMA}"."dim_batch" ORDER BY "batch_id"')
        for bid, lbl, asof, ts in cur.fetchall():
            print(f"  batch #{bid}  {str(lbl):20s} as_of={asof}  loaded={ts:%Y-%m-%d %H:%M}",
                  flush=True)

        print("\n[VERIFY] cumulative row counts (facts by batch)", flush=True)
        for name in spec:
            cur.execute(f'SELECT COUNT(*) FROM "{TARGET_SCHEMA}"."{name}"')
            total = cur.fetchone()[0]
            extra = ""
            if name.startswith("fact_"):
                cur.execute(f'SELECT "batch_key", COUNT(*) FROM "{TARGET_SCHEMA}"."{name}" '
                            f'GROUP BY "batch_key" ORDER BY "batch_key"')
                extra = "  by batch: " + ", ".join(f"b{k}={c:,}" for k, c in cur.fetchall())
            print(f"  {TARGET_SCHEMA}.{name:18s} {total:>8,}{extra}", flush=True)

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
