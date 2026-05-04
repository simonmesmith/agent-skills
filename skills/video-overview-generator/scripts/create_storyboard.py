#!/usr/bin/env python3
"""Validate, normalize, import, and export storyboard files."""

from __future__ import annotations

import argparse
from pathlib import Path

from storyboard_lib import export_csv, import_csv, load_storyboard, save_storyboard, validate_storyboard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", help="Validate and normalize a storyboard JSON file")
    parser.add_argument("--csv", help="Export normalized storyboard JSON to CSV")
    parser.add_argument("--from-csv", help="Import storyboard rows from CSV")
    parser.add_argument("--json", help="Output JSON path when using --from-csv")
    parser.add_argument("--title", default="Video Overview")
    args = parser.parse_args()

    if args.from_csv:
        out_json = args.json or "storyboard.json"
        data = import_csv(args.from_csv, title=args.title)
        errors = validate_storyboard(data)
        if errors:
            for error in errors:
                print(error)
            return 1
        save_storyboard(data, out_json)
        print(f"Wrote {out_json}")
        return 0

    if not args.validate:
        parser.error("Use --validate storyboard.json or --from-csv storyboard.csv")

    data = load_storyboard(args.validate)
    errors = validate_storyboard(data)
    if errors:
        for error in errors:
            print(error)
        return 1
    save_storyboard(data, args.validate)
    print(f"Validated {args.validate} ({len(data['rows'])} rows).")
    if args.csv:
        export_csv(data, args.csv)
        print(f"Wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
