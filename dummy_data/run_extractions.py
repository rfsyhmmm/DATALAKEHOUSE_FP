import subprocess
import sys
from pathlib import Path

DUMMY_DATA  = Path(__file__).parent
REPO_ROOT   = DUMMY_DATA.parent
STAGING     = DUMMY_DATA / 'staging_extraction'
TWEET_DIR   = DUMMY_DATA / 'tweetgenerate'
TWEET_OUT   = TWEET_DIR / 'output'
INVOICE_DIR = DUMMY_DATA / 'generate_invoice'

PYTHON = sys.executable


def run(cmd: list) -> None:
    print('$', ' '.join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd])
    if result.returncode != 0:
        raise RuntimeError(f'Command failed with exit code {result.returncode}')


def latest_in(subdir: str, pattern: str):
    folder = STAGING / subdir
    files = sorted(folder.glob(pattern)) if folder.exists() else []
    return files[-1] if files else None


# ---------------------------------------------------------------------------
# 1. Extract AdventureWorks sales schema → CSV
# ---------------------------------------------------------------------------
print('\n=== [1] Extracting sales schema from PostgreSQL ===')
run([PYTHON, STAGING / 'extract_sales.py'])


# ---------------------------------------------------------------------------
# 1b. Extract production / person / purchasing tables → CSV
# ---------------------------------------------------------------------------
print('\n=== [1b] Extracting product, address, person, shipmethod tables ===')
run([PYTHON, STAGING / 'extract_production.py'])


# ---------------------------------------------------------------------------
# 1c. Split salesorderheader (and related tables) by onlineorderflag
# ---------------------------------------------------------------------------
print('\n=== [1c] Splitting orders into online_store_csv / offline_store_csv ===')
run([PYTHON, STAGING / 'split_by_channel.py'])


# ---------------------------------------------------------------------------
# 2. Generate tweets
# ---------------------------------------------------------------------------
print('\n=== [2] Generating tweets ===')

product_csvs = sorted((STAGING / 'product_and_sub').glob('product_[0-9]*.csv')) \
               if (STAGING / 'product_and_sub').exists() else []

tweet_cmd = [
    PYTHON, TWEET_DIR / 'generate_tweets.py',
    '--count', '2000',
    '--start-date', '2026-06-01',
    '--end-date',   '2026-06-19',
    '--lang', 'mixed',
    '--split', 'day',
    '--output', str(TWEET_OUT),
]
if product_csvs:
    tweet_cmd += ['--products', str(product_csvs[-1])]

run(tweet_cmd)


# ---------------------------------------------------------------------------
# 3. Generate invoice PDFs
# ---------------------------------------------------------------------------
print('\n=== [3] Generating invoice PDFs ===')

header_csv      = latest_in('salesorderheader', 'salesorderheader_*.csv')
detail_csv      = latest_in('salesorderdetail', 'salesorderdetail_*.csv')
customer_csv    = latest_in('customer',          'customer_*.csv')
product_csv     = latest_in('product_and_sub',   'product_[0-9]*.csv')
subcat_csv      = latest_in('product_and_sub',   'productsubcategory_*.csv')
category_csv    = latest_in('product_and_sub',   'productcategory_*.csv')
address_csv     = latest_in('address',           'address_*.csv')
shipmethod_csv  = latest_in('shipmethod',        'shipmethod_*.csv')
salesperson_csv = latest_in('salesperson',       'salesperson_*.csv')
territory_csv   = latest_in('salesterritory',    'salesterritory_*.csv')

OUTPUT_DIR = INVOICE_DIR / 'output_invoices'

invoice_cmd = [
    PYTHON, INVOICE_DIR / 'awc_invoices.py',
    '--output-dir', str(OUTPUT_DIR),
]
for flag, path in [
    ('--header',      header_csv),
    ('--detail',      detail_csv),
    ('--customer',    customer_csv),
    ('--product',     product_csv),
    ('--subcategory', subcat_csv),
    ('--category',    category_csv),
    ('--address',     address_csv),
    ('--shipmethod',  shipmethod_csv),
    ('--salesperson', salesperson_csv),
    ('--territory',   territory_csv),
]:
    if path:
        invoice_cmd += [flag, str(path)]

run(invoice_cmd)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print('\n=== Generated files ===')

print('\n[Staging CSVs]')
for folder in sorted(STAGING.iterdir()):
    if folder.is_dir():
        for f in sorted(folder.rglob('*.csv')):
            print(f'  {f.relative_to(REPO_ROOT)}  ({f.stat().st_size / 1024:,.1f} KB)')

print('\n[Tweets]')
for f in sorted(TWEET_OUT.glob('*.json')) if TWEET_OUT.exists() else []:
    print(f'  {f.relative_to(REPO_ROOT)}  ({f.stat().st_size / 1024:,.1f} KB)')

print('\n[Invoices]')
if OUTPUT_DIR.exists():
    for f in sorted(OUTPUT_DIR.glob('*.pdf')):
        print(f'  {f.relative_to(REPO_ROOT)}  ({f.stat().st_size / 1024:,.1f} KB)')
