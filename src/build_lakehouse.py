"""
build_lakehouse.py — main lakehouse orchestrator (galaxy / fact constellation).

Drains pool/ once (shared bronze) then builds the galaxy. Stage ordering encodes
the data dependencies:

    bronze            pool -> bronze/{csv,pdf,json}  (MOVE/drain)
    document silver   bronze/pdf  -> silver/document  (parsed invoices)
    sales silver      bronze/csv + silver/document -> silver/sales (base + sales.parquet)
                        ^ offline orders come from the parsed PDF invoices, so document
                          silver MUST run first.
    social silver     bronze/json -> silver/social/sentiment.parquet
    gold (galaxy)     silver/sales + silver/social -> gold/  (conformed dims + 2 facts)
    model (DDL)       gold/ -> model/  (relational-ready parquet + schema.json + SQL)

Reads ONLY from pool/; never touches dummy_data/. Run AFTER src/move_to_pool.py.

    .venv\\Scripts\\python.exe src\\build_lakehouse.py
"""

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
PYTHON = sys.executable


def run(script: Path):
    print(f"\n$ {script.relative_to(SRC.parent)}", flush=True)
    # -u (unbuffered): each stage streams its progress live even when this
    # orchestrator's own stdout is a pipe/redirect/IDE terminal. Without it the
    # long PDF stage would block-buffer and look frozen for tens of seconds.
    result = subprocess.run([PYTHON, "-u", str(script)])
    if result.returncode != 0:
        raise SystemExit(f"Stage failed: {script.name} (exit {result.returncode})")


def main():
    print("=== Lakehouse galaxy build (pool -> bronze -> silver -> gold -> model) ===")
    run(SRC / "bronze.py")                       # drain pool by format
    run(SRC / "document_dw" / "silver.py")       # PDF -> silver/document (before sales)
    run(SRC / "sales_dw" / "silver.py")          # CSV + invoices -> silver/sales (+ sales.parquet)
    run(SRC / "social_dw" / "silver.py")         # tweets -> silver/social/sentiment.parquet
    run(SRC / "sales_dw" / "gold.py")            # galaxy: conformed dims + fact_sales + fact_sentiment
    run(SRC / "sales_dw" / "model.py")           # gold -> model/ (relational DDL + schema)
    print("\n=== Lakehouse galaxy build complete ===")


if __name__ == "__main__":
    main()
