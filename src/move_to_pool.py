"""
move_to_pool.py — Factory -> Outside World (pool/)

Copies SELECTED dummy_data into the pool/ landing zone, preserving the raw
source format (OLTP=CSV, social_media=JSON, document=PDF). The pool/ folder
represents the "outside world" / source systems. The medallion lakehouse
(bronze -> silver -> gold) reads ONLY from pool/ and never from dummy_data/.

Every copied file is recorded in pool/_manifest.json for lineage/audit.

Usage:
    python src/move_to_pool.py                       # all sources
    python src/move_to_pool.py --source oltp
    python src/move_to_pool.py --source oltp --source document
"""

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
REPO_ROOT   = Path(__file__).resolve().parent.parent
DUMMY_DATA  = REPO_ROOT / "dummy_data"
STAGING     = DUMMY_DATA / "staging_extraction"
TWEET_OUT   = DUMMY_DATA / "tweetgenerate" / "output"
INVOICE_OUT = DUMMY_DATA / "generate_invoice" / "output_invoices"

POOL          = REPO_ROOT / "pool"
POOL_OLTP     = POOL / "OLTP"
POOL_SOCIAL   = POOL / "social_media"
POOL_DOCUMENT = POOL / "document"
MANIFEST_PATH = POOL / "_manifest.json"

# OLTP registry: (staging_subfolder, table_prefix, source_schema)
# Latest timestamped file per table is selected and copied to pool/OLTP/<subfolder>/.
OLTP_REGISTRY = [
    ("salesorderheader",            "salesorderheader",            "sales"),
    ("salesorderdetail",            "salesorderdetail",            "sales"),
    ("salesorderheadersalesreason", "salesorderheadersalesreason", "sales"),
    ("customer",                    "customer",                    "sales"),
    ("creditcard",                  "creditcard",                  "sales"),
    ("personcreditcard",            "personcreditcard",            "sales"),
    ("currency",                    "currency",                    "sales"),
    ("currencyrate",                "currencyrate",                "sales"),
    ("countryregioncurrency",       "countryregioncurrency",       "sales"),
    ("salesperson",                 "salesperson",                 "sales"),
    ("salespersonquotahistory",     "salespersonquotahistory",     "sales"),
    ("salesreason",                 "salesreason",                 "sales"),
    ("salestaxrate",                "salestaxrate",                "sales"),
    ("salesterritory",              "salesterritory",              "sales"),
    ("salesterritoryhistory",       "salesterritoryhistory",       "sales"),
    ("shoppingcartitem",            "shoppingcartitem",            "sales"),
    ("specialoffer",                "specialoffer",                "sales"),
    ("specialofferproduct",         "specialofferproduct",         "sales"),
    ("store",                       "store",                       "sales"),
    ("product_and_sub",             "product",                     "production"),
    ("product_and_sub",             "productsubcategory",          "production"),
    ("product_and_sub",             "productcategory",             "production"),
    ("address",                     "address",                     "person"),
    ("person",                      "person",                      "person"),
    ("shipmethod",                  "shipmethod",                  "purchasing"),
    ("inventory",                   "productinventory",            "production"),
    ("inventory",                   "location",                    "production"),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def latest_in(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern)) if folder.exists() else []
    return files[-1] if files else None


def count_csv_rows(path: Path) -> int:
    """Fast data-row count (excludes header). No full decode needed."""
    with path.open("rb") as f:
        n = sum(1 for _ in f)
    return max(n - 1, 0)


def load_manifest() -> list:
    if not MANIFEST_PATH.exists():
        return []
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("[WARN] _manifest.json unreadable — starting a fresh manifest.")
        return []


def dedup_manifest(entries: list) -> list:
    """Keep only the latest entry per destination file_name.

    Re-runs accumulate skipped entries for files already in pool/.
    Deduping on file_name (the stable destination identity) prevents the
    manifest from growing unboundedly across runs while preserving the
    most-recent lineage record for each file.
    """
    seen: dict[str, int] = {}
    for i, e in enumerate(entries):
        seen[e["file_name"]] = i
    return [entries[i] for i in sorted(seen.values())]


def save_manifest(entries: list) -> None:
    POOL.mkdir(parents=True, exist_ok=True)
    deduped = dedup_manifest(entries)
    MANIFEST_PATH.write_text(json.dumps(deduped, indent=2), encoding="utf-8")
    return deduped


def copy_file(src: Path, dst: Path) -> bool:
    """Copy src -> dst. Returns True if copied, False if skipped (already there)."""
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def manifest_entry(run_id, source_type, source_system, source_schema,
                   table_name, src: Path, dst: Path, row_count, status) -> dict:
    return {
        "run_id": run_id,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type,
        "source_system": source_system,
        "source_schema": source_schema,
        "table_name": table_name,
        "file_name": dst.name,
        "file_format": dst.suffix.lstrip(".").lower(),
        "source_path": str(src.relative_to(REPO_ROOT)),
        "destination_path": str(dst.relative_to(REPO_ROOT)),
        "file_size_bytes": dst.stat().st_size if dst.exists() else None,
        "row_count": row_count,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# Source processors
# --------------------------------------------------------------------------- #
def process_oltp(run_id, manifest, results):
    print("\n[OLTP]  staging_extraction -> pool/OLTP/")
    for subfolder, prefix, schema in OLTP_REGISTRY:
        src = latest_in(STAGING / subfolder, f"{prefix}_*.csv")
        if src is None:
            print(f"  [WARN] no file for {subfolder}/{prefix}_*.csv — skipping")
            continue
        dst = POOL_OLTP / src.name   # FLAT: no per-table subfolder inside pool/OLTP/
        copied = copy_file(src, dst)
        rows = count_csv_rows(dst)
        status = "copied" if copied else "skipped"
        manifest.append(manifest_entry(run_id, "OLTP", "PostgreSQL", schema,
                                        prefix, src, dst, rows, status))
        results["oltp"][status] += 1
        print(f"  [{status.upper():7s}] {prefix:30s} {rows:>8,} rows -> {dst.relative_to(POOL)}",
              flush=True)


def process_social_media(run_id, manifest, results):
    print("\n[SOCIAL]  tweetgenerate/output -> pool/social_media/")
    files = sorted(TWEET_OUT.glob("tweets_*.json")) if TWEET_OUT.exists() else []
    if not files:
        print("  [WARN] no tweet JSON files found — skipping")
        return
    for src in files:
        dst = POOL_SOCIAL / src.name
        copied = copy_file(src, dst)
        status = "copied" if copied else "skipped"
        manifest.append(manifest_entry(run_id, "social_media", "synthetic_generator",
                                        None, None, src, dst, None, status))
        results["social_media"][status] += 1
    print(f"  {len(files)} file(s) processed "
          f"(copied={results['social_media']['copied']}, skipped={results['social_media']['skipped']})")


def process_document(run_id, manifest, results):
    print("\n[DOCUMENT]  generate_invoice/output_invoices -> pool/document/")
    files = sorted(INVOICE_OUT.glob("*.pdf")) if INVOICE_OUT.exists() else []
    if not files:
        print("  [WARN] no invoice PDF found — skipping")
        return
    for src in files:
        dst = POOL_DOCUMENT / src.name
        copied = copy_file(src, dst)
        status = "copied" if copied else "skipped"
        manifest.append(manifest_entry(run_id, "document", "awc_invoices",
                                        None, None, src, dst, None, status))
        results["document"][status] += 1
        print(f"  [{status.upper():7s}] {dst.name} -> {dst.relative_to(POOL)}")


# --------------------------------------------------------------------------- #
# CLI / main
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Move selected dummy_data into the pool/ landing zone.")
    p.add_argument("--source", action="append", choices=["oltp", "social_media", "document"],
                   help="Limit to specific source(s). Repeatable. Default: all.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    sources = args.source or ["oltp", "social_media", "document"]

    run_id = str(uuid.uuid4())
    manifest = load_manifest()
    results = {s: {"copied": 0, "skipped": 0} for s in ["oltp", "social_media", "document"]}

    print(f"=== move_to_pool  (run_id={run_id}) ===")
    print(f"Sources: {', '.join(sources)}")

    if "oltp" in sources:
        process_oltp(run_id, manifest, results)
    if "social_media" in sources:
        process_social_media(run_id, manifest, results)
    if "document" in sources:
        process_document(run_id, manifest, results)

    deduped = save_manifest(manifest)

    print("\n=== Summary ===")
    for s in sources:
        print(f"  {s:14s} copied={results[s]['copied']:4d}  skipped={results[s]['skipped']:4d}")
    print(f"Manifest -> {MANIFEST_PATH.relative_to(REPO_ROOT)}  ({len(deduped)} entries)")


if __name__ == "__main__":
    main()
