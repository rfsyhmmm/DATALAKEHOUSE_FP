#!/usr/bin/env python3
"""
AdventureWorks Synthetic Invoice PDF Generator
==============================================

Generates batches of PDF files, each containing many one-page sales invoices
built from AdventureWorks-style products. Designed for bulk archival (e.g. a
data lake of invoice documents).

Data sources
------------
- Invoice header / customer / line-item *quantities and prices* are SYNTHETIC
  (randomly generated each run; use --seed for reproducibility).
- The PRODUCT CATALOG can be loaded from an exported AdventureWorks products
  dataset (CSV) plus, optionally, a subcategory dataset to join category names.
  If no products file is supplied, a built-in AdventureWorks sample catalog is
  used so the script runs out of the box.

Each invoice carries only the "reduced" fields meant for archival:
invoice no., dates, customer name + address, line items, and totals. Internal
keys, audit columns, and any payment/card data are intentionally omitted.

Examples
--------
  # Defaults: 30 PDFs x 1000 invoices into ./invoices
  python generate_invoices.py

  # 5 PDFs x 200 invoices, all dated 2024-06-01, custom folder
  python generate_invoices.py --num-pdfs 5 --invoices-per-pdf 200 \
      --date 2024-06-01 --output-dir ./out

  # Date range + your own exported AdventureWorks product export
  python generate_invoices.py --start-date 2024-01-01 --end-date 2024-12-31 \
      --products awc_products.csv --subcategories awc_subcategories.csv
"""

import argparse
import csv
import os
import random
import sys
from datetime import date, datetime, timedelta

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
except ImportError:
    sys.exit("reportlab is required.  Install it with:  pip install reportlab")


# --------------------------------------------------------------------------- #
# Built-in AdventureWorks sample catalog (used when --products is not given).
# Columns mirror a typical Production.Product export joined to subcategory.
# --------------------------------------------------------------------------- #
FALLBACK_PRODUCTS = [
    # name, product_number, list_price, subcategory, category
    ("Mountain-100 Black, 42",        "BK-M82B-42", 3374.99, "Mountain Bikes", "Bikes"),
    ("Mountain-200 Silver, 38",       "BK-M68S-38", 2319.99, "Mountain Bikes", "Bikes"),
    ("Mountain-500 Black, 40",        "BK-M18B-40",  539.99, "Mountain Bikes", "Bikes"),
    ("Road-150 Red, 62",              "BK-R93R-62", 3578.27, "Road Bikes",     "Bikes"),
    ("Road-250 Black, 48",            "BK-R89B-48", 2443.35, "Road Bikes",     "Bikes"),
    ("Road-650 Red, 60",              "BK-R50R-60",  782.99, "Road Bikes",     "Bikes"),
    ("Road-750 Black, 52",            "BK-R19B-52",  539.99, "Road Bikes",     "Bikes"),
    ("Touring-1000 Blue, 54",         "BK-T79U-54", 2384.07, "Touring Bikes",  "Bikes"),
    ("Touring-2000 Blue, 50",         "BK-T44U-50", 1214.85, "Touring Bikes",  "Bikes"),
    ("Touring-3000 Yellow, 58",       "BK-T18Y-58",  742.35, "Touring Bikes",  "Bikes"),
    ("HL Road Frame - Black, 58",     "FR-R92B-58", 1431.50, "Road Frames",    "Components"),
    ("LL Mountain Frame - Silver, 42","FR-M21S-42",  264.05, "Mountain Frames","Components"),
    ("HL Road Front Wheel",           "FW-R820",     330.06, "Wheels",         "Components"),
    ("ML Mountain Rear Wheel",        "RW-M762",     236.32, "Wheels",         "Components"),
    ("Front Derailleur",              "FD-2342",      91.49, "Derailleurs",    "Components"),
    ("Rear Derailleur",               "RD-2308",     121.46, "Derailleurs",    "Components"),
    ("Chain",                         "CH-0234",      20.24, "Chains",         "Components"),
    ("HL Crankset",                   "CS-6583",     404.99, "Cranksets",      "Components"),
    ("HL Headset",                    "HS-3479",     124.73, "Headsets",       "Components"),
    ("Front Brakes",                  "FB-9873",     106.50, "Brakes",         "Components"),
    ("Rear Brakes",                   "RB-9231",     106.50, "Brakes",         "Components"),
    ("ML Road Handlebars",            "HB-M918",      61.92, "Handlebars",     "Components"),
    ("HL Mountain Pedal",             "PD-M562",      80.99, "Pedals",         "Components"),
    ("Long-Sleeve Logo Jersey, L",    "LJ-0192-L",    49.99, "Jerseys",        "Clothing"),
    ("Short-Sleeve Classic Jersey, M","SJ-0194-M",    53.99, "Jerseys",        "Clothing"),
    ("AWC Logo Cap",                  "CA-1098",       8.99, "Caps",           "Clothing"),
    ("Men's Bib-Shorts, M",           "SB-M891-M",    89.99, "Bib-Shorts",     "Clothing"),
    ("Half-Finger Gloves, L",         "GL-H102-L",    24.49, "Gloves",         "Clothing"),
    ("Mountain Bike Socks, M",        "SO-B909-M",      9.50, "Socks",         "Clothing"),
    ("Women's Mountain Shorts, S",    "SH-W890-S",    69.99, "Shorts",         "Clothing"),
    ("Sport-100 Helmet, Red",         "HL-U509-R",    34.99, "Helmets",        "Accessories"),
    ("Water Bottle - 30 oz.",         "WB-H098",       4.99, "Bottles and Cages","Accessories"),
    ("Mountain Bottle Cage",          "BC-M005",       9.99, "Bottles and Cages","Accessories"),
    ("Road Bottle Cage",              "BC-R205",       8.99, "Bottles and Cages","Accessories"),
    ("HL Mountain Tire",              "TI-M602",      35.00, "Tires and Tubes","Accessories"),
    ("ML Road Tire",                  "TI-R982",      24.99, "Tires and Tubes","Accessories"),
    ("Patch Kit/8 Patches",           "PK-7098",       2.29, "Tires and Tubes","Accessories"),
    ("Bike Wash - Dissolver",         "CL-9009",       7.95, "Cleaners",       "Accessories"),
    ("Fender Set - Mountain",         "FE-6654",      21.98, "Fenders",        "Accessories"),
    ("Hydration Pack - 70 oz.",       "HY-1023",      54.99, "Hydration Packs","Accessories"),
    ("All-Purpose Bike Stand",        "ST-1401",     159.00, "Bike Stands",    "Accessories"),
    ("Hitch Rack - 4-Bike",           "RA-H123",     120.00, "Bike Racks",     "Accessories"),
]

# Lightweight name/place pools for synthetic customers (no external deps).
FIRST_NAMES = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
               "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
               "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Aaron",
               "Nancy", "Diane", "Carlos", "Mei", "Aditya", "Sofia", "Hiroshi", "Fatima"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
              "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
              "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
              "Lee", "Perez", "Thompson", "Nguyen", "Patel", "Tanaka", "Khan", "Cohen"]
STREETS = ["Maple Ave", "Oak St", "Pine Rd", "Cedar Ln", "Elm St", "Lakeview Dr",
           "Sunset Blvd", "Market St", "Hillcrest Way", "Riverside Dr", "Bridge St",
           "Park Ave", "Washington St", "Highland Ave", "Birchwood Ct", "Meadow Ln"]
CITIES = [("Seattle", "WA", "98101"), ("Portland", "OR", "97201"), ("Denver", "CO", "80202"),
          ("Austin", "TX", "73301"), ("Chicago", "IL", "60601"), ("Boston", "MA", "02108"),
          ("Atlanta", "GA", "30301"), ("Phoenix", "AZ", "85001"), ("Miami", "FL", "33101"),
          ("Bellevue", "WA", "98004"), ("San Jose", "CA", "95101"), ("Reno", "NV", "89501")]
COMPANY_SUFFIX = ["Cycles", "Bike Shop", "Sports", "Outfitters", "Pro Bikes",
                  "Wheelworks", "Trail Co.", "Velo Store", "Gear Exchange"]


# --------------------------------------------------------------------------- #
# Product loading
# --------------------------------------------------------------------------- #
def _norm(s):
    return (s or "").strip().lower().replace("_", "").replace(" ", "")


def _pick_col(fieldnames, *candidates):
    """Return the actual header matching any candidate name (case/space-insensitive)."""
    norm_map = {_norm(f): f for f in fieldnames}
    for cand in candidates:
        if _norm(cand) in norm_map:
            return norm_map[_norm(cand)]
    return None


def load_products(products_path, subcategories_path=None):
    """Load a product catalog from an exported AdventureWorks CSV.

    Auto-detects common column names. Optionally joins a subcategory export on
    ProductSubcategoryID to recover subcategory / category labels.
    Returns a list of dicts: name, product_number, list_price, subcategory, category.
    """
    if not products_path:
        return [
            {"name": n, "product_number": pn, "list_price": lp,
             "subcategory": sub, "category": cat}
            for (n, pn, lp, sub, cat) in FALLBACK_PRODUCTS
        ]

    # Optional subcategory lookup: id -> (subcategory_name, category_name)
    sub_lookup = {}
    if subcategories_path:
        with open(subcategories_path, newline="", encoding="utf-8-sig") as f:
            r = csv.DictReader(f)
            id_col = _pick_col(r.fieldnames, "ProductSubcategoryID", "SubcategoryID", "id")
            name_col = _pick_col(r.fieldnames, "Name", "SubcategoryName", "ProductSubcategory")
            cat_col = _pick_col(r.fieldnames, "Category", "ProductCategory", "CategoryName")
            for row in r:
                if id_col and name_col:
                    sub_lookup[str(row[id_col]).strip()] = (
                        row.get(name_col, "").strip(),
                        (row.get(cat_col, "").strip() if cat_col else ""),
                    )

    products = []
    with open(products_path, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        fn = r.fieldnames
        name_col = _pick_col(fn, "Name", "ProductName", "Product")
        num_col = _pick_col(fn, "ProductNumber", "ProductNo", "SKU")
        price_col = _pick_col(fn, "ListPrice", "Price", "UnitPrice")
        subid_col = _pick_col(fn, "ProductSubcategoryID", "SubcategoryID")
        sub_col = _pick_col(fn, "Subcategory", "SubcategoryName", "ProductSubcategory")
        cat_col = _pick_col(fn, "Category", "ProductCategory")
        if not name_col:
            sys.exit(f"Could not find a product-name column in {products_path}. "
                     f"Headers seen: {fn}")
        for i, row in enumerate(r, 1):
            try:
                price = float(str(row.get(price_col, "0")).replace(",", "") or 0) if price_col else 0.0
            except ValueError:
                price = 0.0
            # Skip non-sellable / zero-price rows
            if price <= 0:
                continue
            subcategory = (row.get(sub_col, "").strip() if sub_col else "")
            category = (row.get(cat_col, "").strip() if cat_col else "")
            if (not subcategory) and subid_col and str(row.get(subid_col, "")).strip() in sub_lookup:
                subcategory, joined_cat = sub_lookup[str(row[subid_col]).strip()]
                category = category or joined_cat
            products.append({
                "name": row[name_col].strip(),
                "product_number": (row.get(num_col, "").strip() if num_col else f"PN-{i:05d}"),
                "list_price": price,
                "subcategory": subcategory,
                "category": category,
            })
    if not products:
        sys.exit(f"No usable products found in {products_path} (all rows had no/zero price?).")
    return products


# --------------------------------------------------------------------------- #
# Synthetic invoice data
# --------------------------------------------------------------------------- #
def random_customer(rng):
    if rng.random() < 0.4:  # business customer
        name = f"{rng.choice(CITIES)[0]} {rng.choice(COMPANY_SUFFIX)}"
    else:                   # individual
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    city, state, zc = rng.choice(CITIES)
    street = f"{rng.randint(100, 9999)} {rng.choice(STREETS)}"
    return {"name": name, "street": street, "city": city, "state": state, "zip": zc}


def random_date(rng, start_d, end_d, fixed_d):
    if fixed_d:
        return fixed_d
    span = (end_d - start_d).days
    return start_d + timedelta(days=rng.randint(0, max(span, 0)))


def make_invoice(seq, rng, products, start_d, end_d, fixed_d, tax_rate, freight_rate):
    order_dt = random_date(rng, start_d, end_d, fixed_d)
    n_lines = rng.randint(1, 8)
    chosen = rng.sample(products, min(n_lines, len(products)))
    lines = []
    subtotal = 0.0
    for p in chosen:
        qty = rng.randint(1, 10)
        unit = p["list_price"]
        disc = rng.choice([0, 0, 0, 0, 0.05, 0.10, 0.15])  # most lines: no discount
        line_total = round(qty * unit * (1 - disc), 2)
        subtotal += line_total
        lines.append({
            "name": p["name"], "product_number": p["product_number"],
            "qty": qty, "unit_price": unit, "discount": disc, "line_total": line_total,
        })
    subtotal = round(subtotal, 2)
    tax = round(subtotal * tax_rate, 2)
    freight = round(subtotal * freight_rate, 2)
    total = round(subtotal + tax + freight, 2)
    return {
        "invoice_no": f"SO{43659 + seq}",
        "po_number": f"PO{rng.randint(100000, 999999)}",
        "order_date": order_dt,
        "ship_date": order_dt + timedelta(days=7),
        "due_date": order_dt + timedelta(days=12),
        "customer": random_customer(rng),
        "lines": lines,
        "subtotal": subtotal, "tax": tax, "freight": freight, "total": total,
    }


# --------------------------------------------------------------------------- #
# PDF rendering (low-level canvas = fast path for high page counts)
# --------------------------------------------------------------------------- #
def _money(x):
    return f"${x:,.2f}"


def draw_invoice(c, inv, company):
    width, height = letter
    left = 0.75 * inch
    right = width - 0.75 * inch
    y = height - 0.8 * inch

    # --- Seller header ---
    c.setFont("Helvetica-Bold", 18)
    c.drawString(left, y, company["name"])
    c.setFont("Helvetica", 9)
    c.drawString(left, y - 14, company["street"])
    c.drawString(left, y - 26, company["city_line"])
    c.drawString(left, y - 38, company["contact"])

    # --- Invoice title block (right) ---
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(right, y, "INVOICE")
    c.setFont("Helvetica", 9)
    c.drawRightString(right, y - 16, f"Invoice #: {inv['invoice_no']}")
    c.drawRightString(right, y - 28, f"PO #: {inv['po_number']}")
    c.drawRightString(right, y - 40, f"Order date: {inv['order_date']:%Y-%m-%d}")
    c.drawRightString(right, y - 52, f"Due date:   {inv['due_date']:%Y-%m-%d}")

    # --- Bill-to block ---
    y -= 78
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.line(left, y, right, y)
    y -= 18
    cust = inv["customer"]
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, y, "BILL TO")
    c.setFont("Helvetica", 10)
    c.drawString(left, y - 14, cust["name"])
    c.drawString(left, y - 27, cust["street"])
    c.drawString(left, y - 40, f"{cust['city']}, {cust['state']} {cust['zip']}")
    c.setFont("Helvetica", 9)
    c.drawRightString(right, y, f"Ship date: {inv['ship_date']:%Y-%m-%d}")

    # --- Line-item table ---
    y -= 64
    cols = {  # x anchors
        "name": left,
        "pn": left + 2.7 * inch,
        "qty": left + 4.05 * inch,
        "unit": left + 4.75 * inch,
        "disc": left + 5.55 * inch,
        "total": right,
    }
    c.setFillColorRGB(0.12, 0.18, 0.32)
    c.rect(left, y - 4, right - left, 18, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(cols["name"] + 4, y, "Product")
    c.drawString(cols["pn"], y, "Product No.")
    c.drawRightString(cols["qty"], y, "Qty")
    c.drawRightString(cols["unit"], y, "Unit Price")
    c.drawRightString(cols["disc"], y, "Disc")
    c.drawRightString(cols["total"] - 4, y, "Line Total")
    c.setFillColorRGB(0, 0, 0)

    y -= 20
    c.setFont("Helvetica", 9)
    for i, ln in enumerate(inv["lines"]):
        if i % 2 == 1:
            c.setFillColorRGB(0.95, 0.96, 0.98)
            c.rect(left, y - 4, right - left, 15, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)
        name = ln["name"]
        if len(name) > 38:
            name = name[:37] + "\u2026"
        c.drawString(cols["name"] + 4, y, name)
        c.drawString(cols["pn"], y, ln["product_number"])
        c.drawRightString(cols["qty"], y, str(ln["qty"]))
        c.drawRightString(cols["unit"], y, _money(ln["unit_price"]))
        c.drawRightString(cols["disc"], y, f"{int(ln['discount']*100)}%" if ln["discount"] else "-")
        c.drawRightString(cols["total"] - 4, y, _money(ln["line_total"]))
        y -= 15

    # --- Totals box (bottom right) ---
    y -= 10
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.line(cols["unit"] - 0.3 * inch, y, right, y)
    y -= 16
    box_label_x = right - 1.6 * inch
    for label, val, bold in [
        ("Subtotal", inv["subtotal"], False),
        ("Tax", inv["tax"], False),
        ("Freight", inv["freight"], False),
        ("Total Due", inv["total"], True),
    ]:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        c.drawRightString(box_label_x, y, label)
        c.drawRightString(right, y, _money(val))
        y -= 15

    # --- Footer ---
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawCentredString(width / 2, 0.6 * inch,
                        f"{company['name']}  -  Thank you for your business.")
    c.setFillColorRGB(0, 0, 0)


def generate_pdf(path, invoices, company):
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle(os.path.basename(path))
    for inv in invoices:
        draw_invoice(c, inv, company)
        c.showPage()
    c.save()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate batches of AdventureWorks synthetic-invoice PDFs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--num-pdfs", type=int, default=30,
                   help="How many PDF files to generate.")
    p.add_argument("--invoices-per-pdf", type=int, default=1000,
                   help="Invoices (one per page) in each PDF file.")
    p.add_argument("--output-dir", default="invoices",
                   help="Folder where PDF files are written (created if missing).")
    p.add_argument("--date", default=None,
                   help="Fixed invoice date YYYY-MM-DD (overrides the range).")
    p.add_argument("--start-date", default=None,
                   help="Range start YYYY-MM-DD (default: 1 year ago).")
    p.add_argument("--end-date", default=None,
                   help="Range end YYYY-MM-DD (default: today).")
    p.add_argument("--products", default=None,
                   help="CSV export of AdventureWorks products. Omit to use built-in catalog.")
    p.add_argument("--subcategories", default=None,
                   help="Optional CSV export of product subcategories to join on ProductSubcategoryID.")
    p.add_argument("--company-name", default="Adventure Works Cycles",
                   help="Seller name shown on each invoice.")
    p.add_argument("--tax-rate", type=float, default=0.08, help="Tax rate applied to subtotal.")
    p.add_argument("--freight-rate", type=float, default=0.025, help="Freight rate applied to subtotal.")
    p.add_argument("--start-number", type=int, default=0,
                   help="Offset added to the invoice number sequence.")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for reproducible output.")
    p.add_argument("--prefix", default="invoices_batch",
                   help="Filename prefix for each PDF.")
    return p.parse_args(argv)


def _parse_date(s, name):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"--{name} must be in YYYY-MM-DD format (got: {s!r})")


def main(argv=None):
    args = parse_args(argv)
    rng = random.Random(args.seed)

    fixed_d = _parse_date(args.date, "date")
    start_d = _parse_date(args.start_date, "start-date") or (date.today() - timedelta(days=365))
    end_d = _parse_date(args.end_date, "end-date") or date.today()
    if start_d > end_d:
        sys.exit("--start-date cannot be after --end-date")

    products = load_products(args.products, args.subcategories)
    company = {
        "name": args.company_name,
        "street": "2701 Bike Way",
        "city_line": "Bothell, WA 98011",
        "contact": "sales@adventure-works.com  |  (425) 555-0100",
    }

    os.makedirs(args.output_dir, exist_ok=True)
    total_invoices = args.num_pdfs * args.invoices_per_pdf
    print(f"Catalog: {len(products)} products"
          f"{' (built-in)' if not args.products else f' (from {args.products})'}")
    print(f"Generating {args.num_pdfs} PDF(s) x {args.invoices_per_pdf} invoices "
          f"= {total_invoices:,} invoices -> {os.path.abspath(args.output_dir)}")

    seq = args.start_number
    width = len(str(args.num_pdfs))
    for b in range(1, args.num_pdfs + 1):
        invoices = [
            make_invoice(seq + i, rng, products, start_d, end_d, fixed_d,
                         args.tax_rate, args.freight_rate)
            for i in range(args.invoices_per_pdf)
        ]
        seq += args.invoices_per_pdf
        fname = f"{args.prefix}_{b:0{width}d}.pdf"
        fpath = os.path.join(args.output_dir, fname)
        generate_pdf(fpath, invoices, company)
        size_mb = os.path.getsize(fpath) / (1024 * 1024)
        print(f"  [{b}/{args.num_pdfs}] {fname}  ({args.invoices_per_pdf} pages, {size_mb:.1f} MB)")

    print("Done.")


if __name__ == "__main__":
    main()
