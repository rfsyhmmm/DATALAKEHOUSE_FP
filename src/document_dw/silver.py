"""
document_dw / silver.py — bronze/document (PDF) -> silver/document (Parquet)

Parses the unstructured invoice PDF into structured tables. Each page is one
invoice. Header fields & totals are always complete; line items are best-effort
because long orders are truncated on the PDF ("... additional line items
truncated ...") — such invoices are flagged with truncated_flag=True.

Outputs:
  silver/document/invoice_header.parquet  — 1 row per invoice
  silver/document/invoice_line.parquet    — 1 row per extracted line item
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BRONZE    = REPO_ROOT / "medallion_layer" / "bronze" / "pdf"
SILVER    = REPO_ROOT / "medallion_layer" / "silver" / "document"

# --- header regexes (searched anywhere on the page) ---
RE_INVOICE  = re.compile(r"Invoice #:\s*SO(\d+)")
RE_PO       = re.compile(r"PO #:\s*(\S+)")
RE_ORDER    = re.compile(r"Order date:\s*(\d{4}-\d{2}-\d{2})")
RE_DUE      = re.compile(r"Due date:\s*(\d{4}-\d{2}-\d{2})")
RE_SHIP     = re.compile(r"Ship date:\s*(\d{4}-\d{2}-\d{2})")
RE_SHIPVIA  = re.compile(r"Ship via:\s*(.+)")
RE_TERR     = re.compile(r"Territory:\s*(.+)")
RE_SALESP   = re.compile(r"Salesperson:\s*(.+)")
RE_CUST     = re.compile(r"Customer #(\d+)\s*\(Acct\s*([^)]+)\)")
RE_SUBTOTAL = re.compile(r"Subtotal\s+\$([\d,]+\.\d+)")
RE_TAX      = re.compile(r"\bTax\s+\$([\d,]+\.\d+)")
RE_FREIGHT  = re.compile(r"Freight\s+\$([\d,]+\.\d+)")
RE_TOTAL    = re.compile(r"Total Due\s+\$([\d,]+\.\d+)")

# tail of a line-item row: <pn> <qty> $<unit> <disc> $<total>
RE_LINE = re.compile(
    r"^(?P<name>.+?)\s+(?P<pn>\S+)\s+(?P<qty>\d+)\s+"
    r"\$(?P<unit>[\d,]+\.\d+)\s+(?P<disc>-|\d+%)\s+\$(?P<total>[\d,]+\.\d+)$"
)
TRUNC_MARK = "additional line items truncated"


def _money(s):
    return float(s.replace(",", "")) if s else None


def _date(s):
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def _search(rx, text, group=1):
    m = rx.search(text)
    return m.group(group).strip() if m else None


def parse_page(text: str, page_no: int):
    soid = _search(RE_INVOICE, text)
    if soid is None:
        return None, []
    soid = int(soid)

    header = {
        "salesorderid":    soid,
        "po":              _search(RE_PO, text),
        "order_date":      _date(_search(RE_ORDER, text)),
        "due_date":        _date(_search(RE_DUE, text)),
        "ship_date":       _date(_search(RE_SHIP, text)),
        "ship_via":        _search(RE_SHIPVIA, text),
        "territory":       _search(RE_TERR, text),
        "salesperson":     _search(RE_SALESP, text),
        "subtotal":        _money(_search(RE_SUBTOTAL, text)),
        "tax":             _money(_search(RE_TAX, text)),
        "freight":         _money(_search(RE_FREIGHT, text)),
        "total_due":       _money(_search(RE_TOTAL, text)),
        "source_page":     page_no,
        "truncated_flag":  TRUNC_MARK in text,
    }
    cust = RE_CUST.search(text)
    header["customer_id"]      = int(cust.group(1)) if cust else None
    header["customer_account"] = cust.group(2).strip() if cust else None

    # line items: between the table header row and "Subtotal"
    lines = []
    in_table = False
    line_no = 0
    for raw in text.split("\n"):
        if "Product No." in raw:
            in_table = True
            continue
        if not in_table:
            continue
        if raw.startswith("Subtotal"):
            break
        if TRUNC_MARK in raw:
            continue
        m = RE_LINE.match(raw.strip())
        if not m:
            continue
        line_no += 1
        disc = m.group("disc")
        lines.append({
            "salesorderid":   soid,
            "line_no":        line_no,
            "product_name":   m.group("name").strip(),
            "product_number": m.group("pn"),
            "qty":            int(m.group("qty")),
            "unit_price":     _money(m.group("unit")),
            "disc_pct":       0.0 if disc == "-" else float(disc.rstrip("%")) / 100.0,
            "line_total":     _money(m.group("total")),
        })

    header["line_item_count"] = line_no
    return header, lines


def run() -> dict:
    pdfs = sorted(BRONZE.glob("*.pdf")) if BRONZE.exists() else []
    if not pdfs:
        print(f"  [ERROR] no PDF in {BRONZE}", file=sys.stderr)
        sys.exit(1)

    SILVER.mkdir(parents=True, exist_ok=True)
    headers, all_lines = [], []

    # flush=True on every status line: a multi-thousand-page PDF takes tens of
    # seconds to parse, and Python BLOCK-buffers stdout whenever it is not a TTY
    # (pipes, file redirects, subprocess.run, the VS Code integrated terminal).
    # Without flushing, nothing appears until the buffer fills or the process
    # exits, so the run looks frozen/silent. Flushing keeps progress visible.
    print("[SILVER] bronze/document -> silver/document (parse PDF -> Parquet)",
          flush=True)
    pages_seen = 0
    for pdf_path in pdfs:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                pages_seen += 1
                text = page.extract_text() or ""
                # source_page is 1-based to match the physical PDF page number.
                hdr, lines = parse_page(text, i + 1)
                if hdr is None:
                    continue
                headers.append(hdr)
                all_lines.extend(lines)
                if (i + 1) % 500 == 0:
                    print(f"    parsed {i + 1:,}/{total:,} pages...", flush=True)

    # A non-empty PDF that yields zero parsed invoices means the layout no longer
    # matches RE_INVOICE/RE_LINE (e.g. the generator format changed). Writing an
    # empty Parquet would hide that downstream — fail loudly instead.
    if not headers:
        print(f"  [ERROR] parsed {pages_seen:,} page(s) but extracted 0 invoices "
              f"— PDF layout may not match the parser regexes.", file=sys.stderr)
        sys.exit(1)

    hdr_df = pd.DataFrame(headers)
    line_df = pd.DataFrame(all_lines)
    hdr_df.to_parquet(SILVER / "invoice_header.parquet", index=False)
    line_df.to_parquet(SILVER / "invoice_line.parquet", index=False)

    n_trunc = int(hdr_df["truncated_flag"].sum()) if len(hdr_df) else 0
    print(f"  [OK] invoice_header  {len(hdr_df):>8,} rows  ({n_trunc:,} truncated)",
          flush=True)
    print(f"  [OK] invoice_line    {len(line_df):>8,} rows", flush=True)
    return {"invoice_header": len(hdr_df), "invoice_line": len(line_df)}


if __name__ == "__main__":
    run()
