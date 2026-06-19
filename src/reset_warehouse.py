"""
reset_warehouse.py — drop the selected schema inside warehouseDB.

Removes the warehouse schema (default: dw_sales) and everything in it via
DROP SCHEMA ... CASCADE. The database itself is kept. Re-run src/load_warehouse.py
to rebuild it from model/.

    python src/reset_warehouse.py                  # prompt, then drop dw_sales
    python src/reset_warehouse.py --yes            # drop without prompting
    python src/reset_warehouse.py --schema dw_x    # target another schema
    python src/reset_warehouse.py --dry-run        # list what would be dropped
"""

import argparse
import sys

import psycopg2

# must match src/load_warehouse.py
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "warehouseDB"
DB_USER = "postgres"
DB_PASSWORD = "postgres"
DEFAULT_SCHEMA = "dw_sales"


def _conn():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASSWORD)


def _tables(cur, schema: str) -> list[str]:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = %s ORDER BY table_name
    """, (schema,))
    return [r[0] for r in cur.fetchall()]


def reset(schema: str, dry_run: bool = False) -> int:
    try:
        conn = _conn()
    except psycopg2.OperationalError as e:
        print(f"[ERROR] cannot reach {DB_NAME} at {DB_HOST}:{DB_PORT} — {e}",
              file=sys.stderr)
        sys.exit(1)
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                        (schema,))
            if not cur.fetchone():
                print(f"  schema '{schema}' does not exist — nothing to reset.")
                conn.close()
                return 0
            tbls = _tables(cur, schema)
            for t in tbls:
                print(f"  {'[dry] would drop' if dry_run else 'dropping'} "
                      f"{schema}.{t}")
            if not dry_run:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                print(f"  dropped schema '{schema}' (CASCADE)")
    conn.close()
    return len(tbls)


def confirm(skip: bool, schema: str) -> bool:
    if skip:
        return True
    try:
        return input(f"Type 'yes' to drop schema '{schema}': ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Drop a warehouse schema in warehouseDB.")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA, help="schema to drop")
    ap.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    ap.add_argument("--dry-run", action="store_true", help="list targets only")
    args = ap.parse_args(argv)

    print(f"=== RESET warehouse schema '{args.schema}' in {DB_NAME} ===")
    if not args.dry_run and not confirm(args.yes, args.schema):
        print("aborted.")
        return
    n = reset(args.schema, args.dry_run)
    verb = "would drop" if args.dry_run else "dropped"
    print(f"Done. {verb} schema '{args.schema}' ({n} table(s)).")


if __name__ == "__main__":
    main()
