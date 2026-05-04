#!/usr/bin/env python3
"""Create clean production IDs while preserving source IDs."""

from __future__ import annotations

import argparse
from pathlib import Path

from table_io import id_key, read_rows, write_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=3)
    args = parser.parse_args()

    rows, headers = read_rows(args.script)
    if not {"id", "host", "text"}.issubset(headers):
        raise SystemExit("Input must include id, host, and text columns.")

    output_headers = list(headers)
    for column in ("production_id", "source_id"):
        if column not in output_headers:
            output_headers.insert(0 if column == "production_id" else 1, column)

    sorted_rows = sorted(rows, key=lambda row: id_key(row["id"]))
    for number, row in enumerate(sorted_rows, start=1):
        row["source_id"] = row.get("source_id") or row["id"]
        row["production_id"] = f"{number:0{args.width}d}"

    write_csv(args.out, sorted_rows, output_headers)

    print(f"Wrote {len(sorted_rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
