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

# make batch_window importable (used for dataset reference date hint in prompts)
sys.path.insert(0, str(SRC))

# PostgreSQL server shared by the source extract (adventureworks_local) and the
# warehouse (warehouseDB). Preflight checks connect here before a DB action runs.
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD = "localhost", 5432, "postgres", "postgres"

# databases an action must be able to reach before its steps run
DB_SOURCE      = "adventureworks_local"   # source OLTP for dummy.create
DB_MAINTENANCE = "postgres"               # always-present db; proves the server is up


def _dataset_ref_date() -> str:
    """Return the max sales date from silver (dataset anchor) or '' if unavailable."""
    try:
        import batch_window as _bw
        return str(_bw.dataset_as_of())
    except Exception:
        return ""


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

    ("batch.1", "Batch 1 -- full-refresh: drop & recreate DW tables, load up to as-of cutoff",
     True,  [_step("Build lakehouse", SRC / "build_lakehouse.py"),
             _step("Load warehouse (full-refresh)", SRC / "load_warehouse.py", "--full-refresh")],
     [DB_MAINTENANCE]),

    ("batch.2", "Batch 2 -- incremental append: add new rows for an advanced as-of cutoff",
     False, [_step("Build lakehouse", SRC / "build_lakehouse.py"),
             _step("Load warehouse (append)", SRC / "load_warehouse.py")],
     [DB_MAINTENANCE]),

    ("full.rebuild", "Full rebuild: move -> build lakehouse -> load warehouse",
     False, [_step("Move to pool", SRC / "move_to_pool.py"),
             _step("Build lakehouse", SRC / "build_lakehouse.py"),
             _step("Load warehouse", SRC / "load_warehouse.py")], [DB_MAINTENANCE]),
]

BY_KEY = {a[0]: a for a in ACTIONS}

# actions that accept date-window flags (--window/--as-of/--start/--end) on each step
WINDOW_AWARE = {"batch.1", "batch.2"}
WINDOW_PRESETS = ("full", "last7", "last30", "today", "custom")

# logical groups shown as sections in the interactive menu
MENU_SECTIONS = [
    ("DATA SOURCES", ["dummy.create", "dummy.reset"]),
    ("DATALAKE",     ["lake.move", "lake.build", "lake.create",
                      "pool.reset", "medallion.reset", "lake.reset"]),
    ("WAREHOUSE",    ["warehouse.create", "warehouse.reset"]),
    ("BATCH",        ["batch.1", "batch.2", "full.rebuild"]),
]

_W = 72   # menu body width


def prompt_window(as_of_hint: str = "") -> list:
    """Interactively collect window flags for a batch run.

    as_of_hint -- suggested as-of date shown to the user (informational only).
    """
    try:
        w = input(f"  window {WINDOW_PRESETS} [full]: ").strip() or "full"
        flags = ["--window", w]
        ao_prompt = (f"  as-of date YYYY-MM-DD "
                     f"[{as_of_hint} = suggested, blank = max-sales]: " if as_of_hint
                     else "  as-of date YYYY-MM-DD (blank = max sales date): ")
        a = input(ao_prompt).strip()
        if not a and as_of_hint:
            a = as_of_hint          # accept the suggested default on blank entry
        if a:
            flags += ["--as-of", a]
        if w == "custom":
            s = input("  custom start YYYY-MM-DD: ").strip()
            e = input("  custom end   YYYY-MM-DD: ").strip()
            if s: flags += ["--start", s]
            if e: flags += ["--end", e]
        return flags
    except EOFError:
        if as_of_hint:
            return ["--window", "full", "--as-of", as_of_hint]
        return ["--window", "full"]


def print_menu():
    # build a lookup: action_key -> (menu_number, action_tuple)
    num = {a[0]: i for i, a in enumerate(ACTIONS, start=1)}

    def _section_line(title: str) -> str:
        pad = _W - len(title) - 4
        return f"\n  -- {title} " + "-" * pad

    print(f"\n{'=' * (_W + 4)}")
    print(f"  DATALAKEHOUSE  Control Bridge")
    print(f"{'=' * (_W + 4)}")

    for section_title, keys in MENU_SECTIONS:
        print(_section_line(section_title))
        for key in keys:
            if key not in BY_KEY:
                continue
            _, title, destr, _, requires = BY_KEY[key]
            n       = num[key]
            prefix  = " *" if destr else "  "
            db_tag  = "  [DB]" if requires else ""
            # right-align the DB tag within the fixed width
            desc = title
            line = f"{prefix}{n:>2}  {key:<18} {desc}"
            if db_tag:
                pad = max(1, _W - len(line) + 4)
                line = line + " " * pad + db_tag.strip()
            print(line)

    print(f"\n   0  {'exit':<18} Quit")
    print(f"\n  * = destructive (asks for confirmation)   [DB] = needs PostgreSQL")


def _confirm(title: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    try:
        ans = input(f"  DESTRUCTIVE: '{title}'. Type 'yes' to proceed: ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


def run_action(action, auto_yes: bool = False, extra_args=()) -> bool:
    key, title, destructive, steps, requires = action
    if requires and not preflight(requires):
        print("  [ABORT] required database not reachable — start PostgreSQL / fix "
              "the connection and retry.", file=sys.stderr)
        return False
    if destructive and not _confirm(title, auto_yes):
        print("  aborted.")
        return False
    extra = list(extra_args)
    print(f"\n>>> {key}: {title}")
    for label, script, args in steps:
        full_args = args + extra            # window flags appended to each step
        rel = script.relative_to(REPO_ROOT)
        print(f"\n$ {rel} {' '.join(full_args)}".rstrip(), flush=True)
        result = subprocess.run([PYTHON, "-u", str(script), *full_args])
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
            token = input("\n  Enter # or action key (0 = exit, 'list' = refresh menu): ").strip()
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
            print(f"  Unknown: {token!r}  — enter a number or action key. Type 'list' to see options.")
            continue

        # collect window flags for batch-aware actions
        if target[0] in WINDOW_AWARE:
            ref = _dataset_ref_date()   # max sales date, or '' if silver not yet built
            if target[0] == "batch.1":
                hint = "2024-12-31"
                print(f"\n  [batch.1] Full-refresh — drops & recreates all DW tables.")
                print(f"  Suggested as-of = {hint}  (half-year cutoff; full range = {ref or '?'}).")
            elif target[0] == "batch.2":
                hint = ref or "2025-06-29"
                print(f"\n  [batch.2] Incremental append — adds rows beyond the previous as-of.")
                print(f"  Suggested as-of = {hint}  (max sales date = full range).")
            else:
                hint = ref
            extra = prompt_window(as_of_hint=hint)
        else:
            extra = ()

        ok = run_action(target, extra_args=extra)

        # separator + status before redisplaying the menu
        status = "DONE" if ok else "FAILED"
        print(f"\n{'-' * (_W + 4)}  [{status}]")
        try:
            input("  Press Enter to return to menu...")
        except EOFError:
            pass
        print_menu()

    print("\n  bye.")


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

    action_token, extra = argv[0], argv[1:]   # remaining args forwarded to the steps
    target = resolve(action_token)
    if target in ("exit", None):
        if target is None:
            print(f"unknown action: {action_token!r}", file=sys.stderr)
            print_menu()
            sys.exit(2)
        return
    if target == "list":
        print_menu()
        return
    extra = extra if target[0] in WINDOW_AWARE else ()
    ok = run_action(target, auto_yes=auto_yes, extra_args=extra)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
