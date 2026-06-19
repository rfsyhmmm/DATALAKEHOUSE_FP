"""
build_document_dw.py — Document branch: silver only (PDF -> structured Parquet).

Assumes the shared bronze stage (src/bronze.py) has already drained pool/ into
medallion_layer/bronze/pdf/. For the full run use src/build_lakehouse.py.

    .venv\\Scripts\\python.exe src\\document_dw\\build_document_dw.py
"""

import silver


def main():
    print("=== Document DW build (bronze/pdf -> silver) ===\n")
    silver.run()
    print("\n=== Document DW build complete ===")


if __name__ == "__main__":
    main()
