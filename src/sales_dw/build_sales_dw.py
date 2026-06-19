"""
build_sales_dw.py — Sales DW branch: silver -> gold -> model.

Assumes the shared bronze stage (src/bronze.py) has already drained pool/ into
medallion_layer/bronze/csv/. For the full run use src/build_lakehouse.py.

    .venv\\Scripts\\python.exe src\\sales_dw\\build_sales_dw.py
"""

import gold
import model
import silver


def main():
    print("=== Sales DW build (bronze/csv -> silver -> gold -> model) ===\n")
    silver.run()
    print()
    gold.run()
    print()
    model.run()
    print("\n=== Sales DW build complete ===")


if __name__ == "__main__":
    main()
