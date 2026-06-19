"""
reset_medallion.py — wipe the medallion lakehouse output (medallion_layer/ + model/).

Empties the bronze/silver/gold layers AND the relational model/ layer, leaving the
top-level folders in place (the build scripts recreate sub-folders as needed).
Does NOT touch pool/ or dummy_data/.

    python src/reset_medallion.py            # prompt, then delete
    python src/reset_medallion.py --yes      # delete without prompting
    python src/reset_medallion.py --dry-run  # show what would be deleted, delete nothing
"""

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MEDALLION = REPO_ROOT / "medallion_layer"
MODEL     = REPO_ROOT / "model"

TITLE = "RESET medallion_layer/ + model/  (bronze, silver, gold, relational model)"


def targets() -> list[Path]:
    """Direct children of medallion_layer/ and model/ (keeps the two roots)."""
    out = []
    for root in (MEDALLION, MODEL):
        if root.exists():
            out.extend(sorted(root.iterdir()))
    return out


def reset(dry_run: bool = False) -> int:
    items = [t for t in targets() if t.exists()]
    if not items:
        print("  nothing to reset — already clean.")
        return 0
    for t in items:
        kind = "dir " if t.is_dir() else "file"
        tag = "[dry] would remove" if dry_run else "removed"
        print(f"  {tag} {kind} {t.relative_to(REPO_ROOT)}")
        if not dry_run:
            shutil.rmtree(t) if t.is_dir() else t.unlink()
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
