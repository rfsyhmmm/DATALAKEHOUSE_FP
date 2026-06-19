"""
datalakehouse.py — interactive control bridge for the whole DATALAKEHOUSE_FP project.

A single terminal entrypoint that orchestrates every service in the project:
dummy-data generation, datalake (pool + medallion + model) build/reset, and the
PostgreSQL data-warehouse load/reset. Each action simply shells out to the existing
script(s), so this stays a thin, dependency-free "router".

Usage
-----
    python src/datalakehouse.py                 # interactive menu (loops)
    python src/datalakehouse.py warehouse.create # run one action by key
    python src/datalakehouse.py 9                # run one action by number
    python src/datalakehouse.py list             # print the menu and exit
    python src/datalakehouse.py lake.reset --yes # auto-confirm a destructive action

Destructive actions ask for confirmation (interactively) or require --yes
(non-interactively).
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC       = REPO_ROOT / "src"
DUMMY     = REPO_ROOT / "dummy_data"
PYTHON    = sys.executable

# PostgreSQL server shared by the source extract (adventureworks_local) and the
# warehouse (warehouseDB). Preflight checks connect here before a DB action runs.
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD = "localhost", 5432, "postgres", "postgres"

# databases an action must be able to reach before its steps run
DB_SOURCE      = "adventureworks_local"   # source OLTP for dummy.create
DB_MAINTENANCE = "postgres"               # always-present db; proves the server is up


def _check_db(dbname: str) -> tuple[bool, str]:
    try:
        import psycopg2
    except ImportError:
        return False, "psycopg2 not installed"
    try:
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=dbname,
                                user=DB_USER, password=DB_PASSWORD, connect_timeout=3)
        conn.close()
        return True, "OK"
    except Exception as e:
        msg = (str(e).strip().splitlines() or [type(e).__name__])[0]
        return False, msg


def preflight(requires: list) -> bool:
    if not requires:
        return True
    ok_all = True
    for dbname in requires:
        ok, msg = _check_db(dbname)
        print(f"  [preflight] db '{dbname}' @ {DB_HOST}:{DB_PORT} ... "
              f"{'OK' if ok else 'FAIL: ' + msg}", flush=True)
        ok_all = ok_all and ok
    return ok_all


def _step(label, script: Path, *args):
    return (label, script, list(args))


# --- action registry: (key, title, destructive, steps, requires) ---
#   steps    : list of (label, script, args)
#   requires : databases that must be reachable before the action runs (preflight)
ACTIONS = [
    ("dummy.create", "Create dummy data (extract AdventureWorks -> staging CSV/JSON/PDF)",
     False, [_step("Run extractions", DUMMY / "run_extractions.py")], [DB_SOURCE]),

    ("dummy.reset", "Reset dummy data (delete generated *.csv/*.json/*.pdf, keep generators)",
     True, [_step("Reset dummy_data", SRC / "reset_dummy_data.py", "--yes")], []),

    ("lake.move", "Move dummy_data -> pool/ landing zone",
     False, [_step("Move to pool", SRC / "move_to_pool.py")], []),

    ("lake.build", "Build lakehouse (pool -> bronze -> silver -> gold -> model)",
     False, [_step("Build lakehouse", SRC / "build_lakehouse.py")], []),

    ("lake.create", "Full datalake create (move to pool + build lakehouse)",
     False, [_step("Move to pool", SRC / "move_to_pool.py"),
             _step("Build lakehouse", SRC / "build_lakehouse.py")], []),

    ("pool.reset", "Reset pool/ landing zone only",
     True, [_step("Reset pool", SRC / "reset_pool.py", "--yes")], []),

    ("medallion.reset", "Reset medallion_layer/ + model/ only",
     True, [_step("Reset medallion", SRC / "reset_medallion.py", "--yes")], []),

    ("lake.reset", "Reset whole datalake (pool + medallion + model)",
     True, [_step("Reset pool", SRC / "reset_pool.py", "--yes"),
            _step("Reset medallion", SRC / "reset_medallion.py", "--yes")], []),

    ("warehouse.create", "Load model/ into warehouseDB schema dw_sales",
     False, [_step("Load warehouse", SRC / "load_warehouse.py")], [DB_MAINTENANCE]),

    ("warehouse.reset", "Reset warehouse schema dw_sales in warehouseDB",
     True, [_step("Reset warehouse", SRC / "reset_warehouse.py", "--yes")], [DB_MAINTENANCE]),

    ("full.rebuild", "Full rebuild: move -> build lakehouse -> load warehouse",
     False, [_step("Move to pool", SRC / "move_to_pool.py"),
             _step("Build lakehouse", SRC / "build_lakehouse.py"),
             _step("Load warehouse", SRC / "load_warehouse.py")], [DB_MAINTENANCE]),
]

BY_KEY = {a[0]: a for a in ACTIONS}


def print_menu():
    print("\n=== DATALAKEHOUSE control bridge ===")
    print(f"{'#':>2}  {'action':<18} description")
    print("  " + "-" * 76)
    for i, (key, title, destr, _steps, requires) in enumerate(ACTIONS, start=1):
        flag = "  [destructive]" if destr else ""
        db = f"  (db: {', '.join(requires)})" if requires else ""
        print(f"{i:>2}  {key:<18} {title}{flag}{db}")
    print(f"{'0':>2}  {'exit':<18} quit")


def _confirm(title: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    try:
        ans = input(f"  DESTRUCTIVE: '{title}'. Type 'yes' to proceed: ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


def run_action(action, auto_yes: bool = False) -> bool:
    key, title, destructive, steps, requires = action
    if requires and not preflight(requires):
        print("  [ABORT] required database not reachable — start PostgreSQL / fix "
              "the connection and retry.", file=sys.stderr)
        return False
    if destructive and not _confirm(title, auto_yes):
        print("  aborted.")
        return False
    print(f"\n>>> {key}: {title}")
    for label, script, args in steps:
        rel = script.relative_to(REPO_ROOT)
        print(f"\n$ {rel} {' '.join(args)}".rstrip(), flush=True)
        result = subprocess.run([PYTHON, "-u", str(script), *args])
        if result.returncode != 0:
            print(f"  [FAIL] step '{label}' exited {result.returncode} — stopping.",
                  file=sys.stderr)
            return False
    print(f"\n<<< {key} complete.")
    return True


def resolve(token: str):
    """Map a menu number or action key to an action tuple (or None)."""
    token = token.strip()
    if token.isdigit():
        idx = int(token)
        if idx == 0:
            return "exit"
        if 1 <= idx <= len(ACTIONS):
            return ACTIONS[idx - 1]
        return None
    if token in ("exit", "quit", "q"):
        return "exit"
    if token in ("list", "menu", "help", "h"):
        return "list"
    return BY_KEY.get(token)


def interactive():
    print_menu()
    while True:
        try:
            token = input("\nselect # or action (0=exit): ").strip()
        except EOFError:
            print()
            break
        if not token:
            continue
        target = resolve(token)
        if target == "exit":
            break
        if target == "list":
            print_menu()
            continue
        if target is None:
            print(f"  unknown selection: {token!r} (type 'list' to see options)")
            continue
        run_action(target)
    print("bye.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    auto_yes = False
    for f in ("--yes", "-y"):
        if f in argv:
            argv.remove(f)
            auto_yes = True

    if not argv:
        interactive()
        return

    target = resolve(argv[0])
    if target in ("exit", None):
        if target is None:
            print(f"unknown action: {argv[0]!r}", file=sys.stderr)
            print_menu()
            sys.exit(2)
        return
    if target == "list":
        print_menu()
        return
    ok = run_action(target, auto_yes=auto_yes)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
