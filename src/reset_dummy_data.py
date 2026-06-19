"""
reset_dummy_data.py — delete GENERATED dummy data, keep the generator scripts.

dummy_data/ holds both the generator programs (*.py: extract_sales, extract_production,
split_by_channel, generate_tweets, awc_invoices, run_extractions) and their OUTPUT
(*.csv staging extracts, *.json tweets, *.pdf invoices). This reset removes only the
generated output (and __pycache__), so the generators stay intact and can re-create it.

    python src/reset_dummy_data.py            # prompt, then delete
    python src/reset_dummy_data.py --yes      # delete without prompting
    python src/reset_dummy_data.py --dry-run  # show what would be deleted, delete nothing
"""

import argparse
import shutil
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
DUMMY_DATA = REPO_ROOT / "dummy_data"

# generated output is identified purely by extension — *.py is never touched
OUTPUT_GLOBS = ("*.csv", "*.json", "*.pdf")

TITLE = "RESET dummy_data/ generated output (*.csv, *.json, *.pdf, __pycache__)"


def targets() -> list[Path]:
    if not DUMMY_DATA.exists():
        return []
    files = []
    for pat in OUTPUT_GLOBS:
        files.extend(DUMMY_DATA.rglob(pat))
    pycache = [p for p in DUMMY_DATA.rglob("__pycache__") if p.is_dir()]
    return sorted(set(files) | set(pycache))


def reset(dry_run: bool = False) -> int:
    items = [t for t in targets() if t.exists()]
    if not items:
        print("  nothing to reset — no generated output found.")
        return 0
    n_files = n_dirs = 0
    for t in items:
        if t.is_dir():
            n_dirs += 1
            tag = "[dry] would remove" if dry_run else "removed"
            print(f"  {tag} dir  {t.relative_to(REPO_ROOT)}")
            if not dry_run:
                shutil.rmtree(t)
        else:
            n_files += 1
            if not dry_run:
                t.unlink()
    if n_files:
        verb = "would delete" if dry_run else "deleted"
        print(f"  {verb} {n_files} output file(s) (*.csv/*.json/*.pdf)")
    return len(items)


def confirm(skip: bool) -> bool:
    if skip:
        return True
    try:
        return input("Type 'yes' to proceed: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=TITLE)
    ap.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    ap.add_argument("--dry-run", action="store_true", help="list targets only")
    args = ap.parse_args(argv)

    print(f"=== {TITLE} ===")
    if not args.dry_run and not confirm(args.yes):
        print("aborted.")
        return
    n = reset(args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    print(f"Done. {verb} {n} item(s).")


if __name__ == "__main__":
    main()
