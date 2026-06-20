"""
batch_window.py — date-window / as-of resolver for incremental batch runs.

A *batch* ingests the slice of the (fully generated) data whose date falls inside a
window. Relative presets (last7/last30/today) anchor on the **dataset reference date**
= the max sales order_date (the data's "now"), NOT wall-clock today — because the sales
data is historical. The default `full` window loads everything up to the as-of cutoff.

Reused by gold.py (to filter the facts) and the orchestrator/bridge (CLI flags).
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT     = Path(__file__).resolve().parent.parent
SALES_PARQUET = REPO_ROOT / "medallion_layer" / "silver" / "sales" / "sales.parquet"

WINDOWS = ("full", "last7", "last30", "today", "custom")


def _parse(d):
    if d is None or isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def sales_bounds() -> tuple[date, date]:
    """(min, max) of order_date in the unified silver sales fact."""
    if not SALES_PARQUET.exists():
        raise FileNotFoundError(
            f"{SALES_PARQUET} not found — build silver/sales first (sales_dw/silver.py).")
    s = pd.to_datetime(pd.read_parquet(SALES_PARQUET, columns=["order_date"])["order_date"])
    return s.min().date(), s.max().date()


def dataset_as_of() -> date:
    """Default anchor = the dataset's latest sales date."""
    return sales_bounds()[1]


def resolve_window(window="full", as_of=None, start=None, end=None):
    """Return (start_date, end_date, as_of_date, label). Presets anchor on as_of."""
    smin, smax = sales_bounds()
    anchor = _parse(as_of) or smax
    if window == "custom":
        s = _parse(start) or smin
        e = _parse(end) or anchor
        return s, e, anchor, f"custom:{s}..{e}"
    if window == "today":
        return anchor, anchor, anchor, f"today:{anchor}"
    if window == "last7":
        return anchor - timedelta(days=6), anchor, anchor, f"last7@{anchor}"
    if window == "last30":
        return anchor - timedelta(days=29), anchor, anchor, f"last30@{anchor}"
    # full
    return smin, anchor, anchor, f"full<= {anchor}"


def add_window_args(p):
    """Attach the standard window flags to an argparse parser."""
    p.add_argument("--window", choices=WINDOWS, default="full",
                   help="date window preset (default: full)")
    p.add_argument("--as-of", default=None,
                   help="anchor/cutoff date YYYY-MM-DD (default: max sales date)")
    p.add_argument("--start", default=None, help="custom window start (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="custom window end (YYYY-MM-DD)")


def window_from_args(args):
    return resolve_window(args.window, args.as_of, args.start, args.end)
