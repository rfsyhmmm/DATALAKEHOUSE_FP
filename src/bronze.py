"""
bronze.py — shared raw-ingestion stage.  pool/ -> medallion_layer/bronze/

Drains the WHOLE pool/ landing zone into the bronze layer, grouped BY FORMAT
(not by source):

    pool/**/*.csv   ->  bronze/csv/
    pool/**/*.pdf   ->  bronze/pdf/
    pool/**/*.json  ->  bronze/json/

Semantics:
  * MOVE (not copy) — once bronze pulls a file, it DISAPPEARS from pool.
    pool/ is a transient inbox; medallion only reads from pool/.
  * Original format preserved (raw) — no type-casting / restructuring here.
  * CSV files are renamed to a canonical name (trailing _YYYYMMDDHHMM stripped),
    e.g. salesorderheader_202606191332.csv -> salesorderheader.csv.
    JSON/PDF keep their original names.

pool/_manifest.json is left in place (it is the lineage log, not a data file).
"""

import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POOL      = REPO_ROOT / "pool"
BRONZE    = REPO_ROOT / "medallion_layer" / "bronze"

# extension -> bronze subfolder
FORMAT_DIRS = {".csv": "csv", ".pdf": "pdf", ".json": "json"}

# trailing _YYYYMMDDHHMM (12 digits) before the extension
TS_SUFFIX = re.compile(r"_\d{12}$")


def canonical_stem(stem: str) -> str:
    return TS_SUFFIX.sub("", stem)


def run() -> dict:
    if not POOL.exists():
        print(f"  [ERROR] pool/ not found at {POOL}", file=sys.stderr)
        sys.exit(1)

    counts = {"csv": 0, "pdf": 0, "json": 0}
    print("[BRONZE] draining pool/ -> bronze/ (by format, MOVE)")

    # iterate every data file in pool (skip the manifest log)
    files = [p for p in POOL.rglob("*")
             if p.is_file() and p.name != "_manifest.json"]
    if not files:
        print("  [WARN] pool/ is empty — nothing to ingest. "
              "Run src/move_to_pool.py first.")
        return counts

    for src in sorted(files):
        ext = src.suffix.lower()
        sub = FORMAT_DIRS.get(ext)
        if sub is None:
            print(f"  [SKIP] unsupported format: {src.relative_to(POOL)}")
            continue
        out_dir = BRONZE / sub
        out_dir.mkdir(parents=True, exist_ok=True)
        # canonical name for CSV (strip timestamp); keep original for json/pdf
        name = f"{canonical_stem(src.stem)}{ext}" if ext == ".csv" else src.name
        dst = out_dir / name
        if dst.exists():
            dst.unlink()  # overwrite stale bronze copy
        shutil.move(str(src), str(dst))
        counts[sub] += 1

    for fmt, n in counts.items():
        print(f"  [OK] bronze/{fmt:4s} {n:>3d} file(s)")
    return counts


if __name__ == "__main__":
    run()
