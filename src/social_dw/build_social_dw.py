"""
build_social_dw.py — Social branch: silver only (tweets JSON -> structured Parquet).

Assumes the shared bronze stage (src/bronze.py) has already drained pool/ into
medallion_layer/bronze/json/. For the full galaxy build use src/build_lakehouse.py.

    .venv\\Scripts\\python.exe src\\social_dw\\build_social_dw.py
"""

import silver


def main():
    print("=== Social DW build (bronze/json -> silver) ===\n")
    silver.run()
    print("\n=== Social DW build complete ===")


if __name__ == "__main__":
    main()
