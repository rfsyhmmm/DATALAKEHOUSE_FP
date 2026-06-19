#!/usr/bin/env python3
"""
AdventureWorks Invoice PDF Generator (from real exports)
========================================================

Reads exported AdventureWorks CSV tables, selects the SalesOrderHeader rows
whose `onlineorderflag` is FALSE (i.e. reseller / salesperson orders), and
renders one structured invoice page per qualifying order.

REQUIRED inputs
---------------
  --header        Sales.SalesOrderHeader export   (the orders to invoice)
  --product       Production.Product export       (product names / numbers / prices)
  --subcategory   Production.ProductSubcategory    (subcategory + link to category)
  --category      Production.ProductCategory       (category names)

OPTIONAL inputs (each one makes the invoice richer; omit what you don't have)
-----------------------------------------------------------------------------
  --detail        Sales.SalesOrderDetail  -> real line items (product, qty, price)
  --customer      Sales.Customer          -> resolve customerid
  --person        Person.Person           -> individual customer / salesperson names
  --store         Sales.Store             -> store (business) customer names
  --address       Person.Address          -> bill-to / ship-to addresses
  --salesperson   Sales.SalesPerson       -> salesperson link
  --territory     Sales.SalesTerritory    -> territory name
  --shipmethod    Purchasing.ShipMethod   -> shipping method name

If --detail is not provided, the invoice still prints with header totals and a
clear "line items not available" note. If customer/address tables are not
provided, the customer block falls back to the CustomerID / AccountNumber.

File paths are passed flexibly through the DataSources object (CLI, dict, or
direct construction) — see DataSources.from_args / from_dict.

Examples
--------
  python awc_invoices.py \
      --header salesorderheader.csv --product product.csv \
      --subcategory productsubcategory.csv --category productcategory.csv \
      --output-dir invoices

  # richer invoice once you have the detail + customer/address exports:
  python awc_invoices.py --header ... --product ... --subcategory ... --category ... \
      --detail salesorderdetail.csv --customer customer.csv --person person.csv \
      --store store.csv --address address.csv
"""

import argparse
import csv
import os
import sys
from dataclasses import dataclass, fields
from datetime import datetime

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
except ImportError:
    sys.exit("reportlab is required.  Install it with:  pip install reportlab")


# --------------------------------------------------------------------------- #
# Robust CSV reading
# --------------------------------------------------------------------------- #
def _norm(s):
    return (s or "").strip().lower().replace("_", "").replace(" ", "").replace('"', "")


def read_csv(path):
    """Read a CSV into a list of dicts (keys = normalized headers).

    Transparently handles two quirks seen in these exports:
      * a UTF-8 BOM on the first line, and
      * "double-encoded" rows where the ENTIRE record is wrapped in one pair of
        quotes with inner quotes doubled (so a naive parse yields a single
        field). Such rows are detected and re-parsed.
    """
    if not path:
        return []
    if not os.path.exists(path):
        sys.exit(f"File not found: {path}")
    # errors="replace": some exports carry binary columns (e.g. Address.spatiallocation)
    # whose bytes are not valid UTF-8; replacing keeps the rest of the row readable.
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        raw = list(csv.reader(f))
    if not raw:
        return []

    # Detect double-encoding: header parsed to a single field that itself
    # contains commas -> re-parse every row's lone field as CSV.
    if len(raw[0]) == 1 and "," in raw[0][0]:
        raw = [next(csv.reader([r[0]])) for r in raw if r and r[0].strip() != ""]

    header = [_norm(h) for h in raw[0]]
    out = []
    for row in raw[1:]:
        if not row or all(c.strip() == "" for c in row):
            continue
        # pad/truncate to header length
        row = (row + [""] * len(header))[:len(header)]
        out.append(dict(zip(header, row)))
    return out


def col(row, *candidates, default=""):
    """Fetch a value from a row dict by any of several candidate column names."""
    for c in candidates:
        key = _norm(c)
        if key in row and str(row[key]).strip() != "":
            return str(row[key]).strip()
    return default


def to_float(s, default=0.0):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return default


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(d):
    return d.strftime("%Y-%m-%d") if d else "-"


def money(x):
    return f"${x:,.2f}"


# --------------------------------------------------------------------------- #
# Flexible file-path container
# --------------------------------------------------------------------------- #
@dataclass
class DataSources:
    header: str = None
    product: str = None
    subcategory: str = None
    category: str = None
    detail: str = None
    customer: str = None
    person: str = None
    store: str = None
    address: str = None
    salesperson: str = None
    territory: str = None
    shipmethod: str = None
    stateprovince: str = None

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_args(cls, args):
        return cls.from_dict(vars(args))


# --------------------------------------------------------------------------- #
# Lookups built from the exports
# --------------------------------------------------------------------------- #
class Catalog:
    def __init__(self, src: DataSources):
        self.src = src
        self._build()

    def _build(self):
        # categories: id -> name
        cat = {}
        for r in read_csv(self.src.category):
            cat[col(r, "productcategoryid", "id")] = col(r, "name")
        # subcategories: id -> (name, category_name)
        sub = {}
        for r in read_csv(self.src.subcategory):
            sid = col(r, "productsubcategoryid", "id")
            sub[sid] = (col(r, "name"), cat.get(col(r, "productcategoryid"), ""))
        # products: id -> dict
        self.products = {}
        for r in read_csv(self.src.product):
            pid = col(r, "productid", "id")
            sname, cname = sub.get(col(r, "productsubcategoryid"), ("", ""))
            self.products[pid] = {
                "name": col(r, "name", "productname"),
                "number": col(r, "productnumber", "sku"),
                "listprice": to_float(col(r, "listprice", "price")),
                "subcategory": sname,
                "category": cname,
            }

        # OPTIONAL: order detail grouped by salesorderid
        self.detail_by_order = {}
        for r in read_csv(self.src.detail):
            oid = col(r, "salesorderid", "orderid")
            pid = col(r, "productid")
            prod = self.products.get(pid, {})
            qty = int(to_float(col(r, "orderqty", "quantity"), 0))
            unit = to_float(col(r, "unitprice"), prod.get("listprice", 0.0))
            disc = to_float(col(r, "unitpricediscount", "discount"), 0.0)
            line_total = to_float(col(r, "linetotal"), round(qty * unit * (1 - disc), 4))
            self.detail_by_order.setdefault(oid, []).append({
                "name": prod.get("name", f"Product {pid}"),
                "number": prod.get("number", pid),
                "qty": qty, "unit": unit, "discount": disc, "line_total": line_total,
            })

        # OPTIONAL: customer name resolution
        self.person_name = {}   # businessentityid -> "First Last"
        for r in read_csv(self.src.person):
            bid = col(r, "businessentityid", "personid", "id")
            name = " ".join(p for p in [col(r, "firstname"), col(r, "middlename"),
                                        col(r, "lastname")] if p)
            self.person_name[bid] = name.strip()
        self.store_name = {}    # businessentityid -> store name
        for r in read_csv(self.src.store):
            self.store_name[col(r, "businessentityid", "storeid", "id")] = col(r, "name")
        # customer: customerid -> store/person business entity ids
        self.customer = {}
        for r in read_csv(self.src.customer):
            cid = col(r, "customerid", "id")
            self.customer[cid] = {
                "store": col(r, "storeid"),
                "person": col(r, "personid"),
                "account": col(r, "accountnumber"),
            }
        # addresses: addressid -> formatted lines
        self.address = {}
        for r in read_csv(self.src.address):
            aid = col(r, "addressid", "id")
            self.address[aid] = {
                "line1": col(r, "addressline1"),
                "line2": col(r, "addressline2"),
                "city": col(r, "city"),
                "state": col(r, "stateprovinceid", "stateprovince", "state"),
                "postal": col(r, "postalcode", "zip"),
            }
        # territory: id -> name ; shipmethod: id -> name ; salesperson -> person id
        self.territory = {col(r, "territoryid", "id"): col(r, "name")
                          for r in read_csv(self.src.territory)}
        self.shipmethod = {col(r, "shipmethodid", "id"): col(r, "name")
                           for r in read_csv(self.src.shipmethod)}
        self.salesperson = {col(r, "businessentityid", "salespersonid", "id"):
                            col(r, "businessentityid", "salespersonid")
                            for r in read_csv(self.src.salesperson)}
        # OPTIONAL: stateprovinceid -> "WA" / "Washington"
        self.stateprovince = {}
        for r in read_csv(self.src.stateprovince):
            self.stateprovince[col(r, "stateprovinceid", "id")] = \
                col(r, "stateprovincecode", "code", "name")

    # -- resolution helpers (all degrade gracefully) --
    def customer_label(self, customerid, accountnumber):
        info = self.customer.get(customerid)
        if info:
            if info.get("store") and info["store"] in self.store_name:
                return self.store_name[info["store"]]
            if info.get("person") and info["person"] in self.person_name:
                return self.person_name[info["person"]]
        return f"Customer #{customerid}" + (f"  (Acct {accountnumber})" if accountnumber else "")

    def address_lines(self, addressid):
        a = self.address.get(addressid)
        if not a:
            return None
        lines = [a["line1"]]
        if a["line2"]:
            lines.append(a["line2"])
        state = a["state"]
        if state in self.stateprovince:           # resolved to code/name
            state = self.stateprovince[state]
        elif state.isdigit():                      # unresolved numeric FK -> hide
            state = ""
        city_line = ", ".join(p for p in [a["city"], state] if p)
        if a["postal"]:
            city_line = f"{city_line} {a['postal']}".strip()
        if city_line:
            lines.append(city_line)
        return [l for l in lines if l]

    def territory_name(self, tid):
        return self.territory.get(tid, "")

    def shipmethod_name(self, mid):
        return self.shipmethod.get(mid, "")


# --------------------------------------------------------------------------- #
# Build invoice records from header rows with onlineorderflag == false
# --------------------------------------------------------------------------- #
def build_invoices(src: DataSources, catalog: Catalog, online_flag_value="false"):
    invoices = []
    target = online_flag_value.strip().lower()
    for r in read_csv(src.header):
        if col(r, "onlineorderflag").strip().lower() != target:
            continue
        oid = col(r, "salesorderid", "orderid")
        invoices.append({
            "salesorderid": oid,
            "po": col(r, "purchaseordernumber"),
            "account": col(r, "accountnumber"),
            "customerid": col(r, "customerid"),
            "order_date": parse_date(col(r, "orderdate")),
            "due_date": parse_date(col(r, "duedate")),
            "ship_date": parse_date(col(r, "shipdate")),
            "bill_to": catalog.address_lines(col(r, "billtoaddressid")),
            "ship_to": catalog.address_lines(col(r, "shiptoaddressid")),
            "customer_label": catalog.customer_label(col(r, "customerid"), col(r, "accountnumber")),
            "territory": catalog.territory_name(col(r, "territoryid")),
            "shipmethod": catalog.shipmethod_name(col(r, "shipmethodid")),
            "salesperson": catalog.person_name.get(col(r, "salespersonid"), ""),
            "lines": catalog.detail_by_order.get(oid, []),
            "subtotal": to_float(col(r, "subtotal")),
            "tax": to_float(col(r, "taxamt")),
            "freight": to_float(col(r, "freight")),
            "total": to_float(col(r, "totaldue")),
        })
    return invoices


# --------------------------------------------------------------------------- #
# PDF rendering
# --------------------------------------------------------------------------- #
def draw_invoice(c, inv, seller):
    width, height = letter
    left = 0.75 * inch
    right = width - 0.75 * inch
    y = height - 0.8 * inch

    c.setFont("Helvetica-Bold", 18)
    c.drawString(left, y, seller["name"])
    c.setFont("Helvetica", 9)
    c.drawString(left, y - 14, seller["street"])
    c.drawString(left, y - 26, seller["city_line"])

    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(right, y, "INVOICE")
    c.setFont("Helvetica", 9)
    c.drawRightString(right, y - 16, f"Invoice #: SO{inv['salesorderid']}")
    if inv["po"]:
        c.drawRightString(right, y - 28, f"PO #: {inv['po']}")
    c.drawRightString(right, y - 40, f"Order date: {fmt_date(inv['order_date'])}")
    c.drawRightString(right, y - 52, f"Due date:   {fmt_date(inv['due_date'])}")

    y -= 78
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.line(left, y, right, y)
    y -= 18

    # Bill-to (left) / Ship-to (right)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, y, "BILL TO")
    c.setFont("Helvetica", 10)
    c.drawString(left, y - 13, inv["customer_label"])
    yy = y - 26
    for ln in (inv["bill_to"] or []):
        c.setFont("Helvetica", 9)
        c.drawString(left, yy, ln)
        yy -= 12

    c.setFont("Helvetica", 9)
    c.drawRightString(right, y, f"Ship date: {fmt_date(inv['ship_date'])}")
    extra = []
    if inv["shipmethod"]:
        extra.append(f"Ship via: {inv['shipmethod']}")
    if inv["territory"]:
        extra.append(f"Territory: {inv['territory']}")
    if inv.get("salesperson"):
        extra.append(f"Salesperson: {inv['salesperson']}")
    ey = y - 13
    for e in extra:
        c.drawRightString(right, ey, e)
        ey -= 12

    y = min(yy, ey) - 18

    # Line-item table
    cols = {
        "name": left, "pn": left + 2.7 * inch, "qty": left + 4.05 * inch,
        "unit": left + 4.75 * inch, "disc": left + 5.55 * inch, "total": right,
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

    if inv["lines"]:
        c.setFont("Helvetica", 9)
        for i, ln in enumerate(inv["lines"]):
            if y < 1.6 * inch:  # avoid running off the page for very long orders
                c.setFont("Helvetica-Oblique", 8)
                c.drawString(cols["name"] + 4, y, "... additional line items truncated ...")
                y -= 15
                break
            if i % 2 == 1:
                c.setFillColorRGB(0.95, 0.96, 0.98)
                c.rect(left, y - 4, right - left, 15, fill=1, stroke=0)
                c.setFillColorRGB(0, 0, 0)
            name = ln["name"]
            if len(name) > 38:
                name = name[:37] + "\u2026"
            c.setFont("Helvetica", 9)
            c.drawString(cols["name"] + 4, y, name)
            c.drawString(cols["pn"], y, ln["number"])
            c.drawRightString(cols["qty"], y, str(ln["qty"]))
            c.drawRightString(cols["unit"], y, money(ln["unit"]))
            c.drawRightString(cols["disc"], y, f"{ln['discount']*100:.0f}%" if ln["discount"] else "-")
            c.drawRightString(cols["total"] - 4, y, money(ln["line_total"]))
            y -= 15
    else:
        c.setFont("Helvetica-Oblique", 9)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        c.drawString(cols["name"] + 4, y,
                     "Line items not available (SalesOrderDetail not supplied) - header totals only.")
        c.setFillColorRGB(0, 0, 0)
        y -= 15

    # Totals
    y -= 10
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.line(cols["unit"] - 0.3 * inch, y, right, y)
    y -= 16
    label_x = right - 1.6 * inch
    for label, val, bold in [
        ("Subtotal", inv["subtotal"], False),
        ("Tax", inv["tax"], False),
        ("Freight", inv["freight"], False),
        ("Total Due", inv["total"], True),
    ]:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        c.drawRightString(label_x, y, label)
        c.drawRightString(right, y, money(val))
        y -= 15

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawCentredString(width / 2, 0.6 * inch, f"{seller['name']}  -  Thank you for your business.")
    c.setFillColorRGB(0, 0, 0)


def write_pdfs(invoices, seller, output_dir, one_file_per_invoice=False,
               combined_name="invoices_onlineorderflag_false.pdf"):
    os.makedirs(output_dir, exist_ok=True)
    if one_file_per_invoice:
        for inv in invoices:
            path = os.path.join(output_dir, f"invoice_SO{inv['salesorderid']}.pdf")
            c = canvas.Canvas(path, pagesize=letter)
            draw_invoice(c, inv, seller)
            c.showPage()
            c.save()
        return output_dir
    path = os.path.join(output_dir, combined_name)
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle(combined_name)
    for inv in invoices:
        draw_invoice(c, inv, seller)
        c.showPage()
    c.save()
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate invoice PDFs from AdventureWorks exports (onlineorderflag=false).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    # required-ish
    p.add_argument("--header", required=True, help="SalesOrderHeader CSV")
    p.add_argument("--product", required=True, help="Product CSV")
    p.add_argument("--subcategory", help="ProductSubcategory CSV")
    p.add_argument("--category", help="ProductCategory CSV")
    # optional enrichment
    p.add_argument("--detail", help="SalesOrderDetail CSV (line items)")
    p.add_argument("--customer", help="Customer CSV")
    p.add_argument("--person", help="Person CSV")
    p.add_argument("--store", help="Store CSV")
    p.add_argument("--address", help="Address CSV")
    p.add_argument("--salesperson", help="SalesPerson CSV")
    p.add_argument("--territory", help="SalesTerritory CSV")
    p.add_argument("--shipmethod", help="ShipMethod CSV")
    p.add_argument("--stateprovince", help="StateProvince CSV (resolve numeric state IDs)")
    # output / behavior
    p.add_argument("--output-dir", default="invoices", help="Where to write PDFs")
    p.add_argument("--one-file-per-invoice", action="store_true",
                   help="Write one PDF per invoice instead of a single combined PDF")
    p.add_argument("--combined-name", default="invoices_onlineorderflag_false.pdf",
                   help="Filename for the combined PDF")
    p.add_argument("--online-flag-value", default="false",
                   help="Value of onlineorderflag to KEEP")
    p.add_argument("--seller-name", default="Adventure Works Cycles")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    src = DataSources.from_args(args)
    catalog = Catalog(src)
    invoices = build_invoices(src, catalog, args.online_flag_value)
    seller = {"name": args.seller_name, "street": "2701 Bike Way",
              "city_line": "Bothell, WA 98011"}

    has_detail = any(inv["lines"] for inv in invoices)
    has_addr = any(inv["bill_to"] for inv in invoices)
    print(f"Qualifying invoices (onlineorderflag={args.online_flag_value}): {len(invoices)}")
    print(f"Line items: {'YES' if has_detail else 'NO (SalesOrderDetail not supplied)'}")
    print(f"Customer addresses: {'YES' if has_addr else 'NO (Address table not supplied)'}")
    if not invoices:
        sys.exit("No qualifying rows found - check --header path and --online-flag-value.")

    out = write_pdfs(invoices, seller, args.output_dir,
                     args.one_file_per_invoice, args.combined_name)
    print(f"Written to: {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
