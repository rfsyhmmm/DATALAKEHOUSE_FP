"""
reset_pool.py — empty the pool/ landing zone (the transient "outside world" inbox).

Removes everything under pool/ (OLTP/, social_media/, document/ and the lineage
log _manifest.json), leaving the pool/ root in place. Does NOT touch dummy_data/
or medallion_layer/.

    python src/reset_pool.py            # prompt, then delete
    python src/reset_pool.py --yes      # delete without prompting
    python src/reset_pool.py --dry-run  # show what would be deleted, delete nothing
"""

import argparse
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POOL = REPO_ROOT / "pool"

TITLE = "RESET pool/  (landing zone: OLTP, social_media, document, _manifest.json)"


def targets() -> list[Path]:
    return sorted(POOL.iterdir()) if POOL.exists() else []


def reset(dry_run: bool = False) -> int:
    items = [t for t in targets() if t.exists()]
    if not items:
        print("  nothing to reset — pool/ already empty.")
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
