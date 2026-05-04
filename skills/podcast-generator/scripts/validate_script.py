#!/usr/bin/env python3
"""Validate podcast dialogue script tables."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from table_io import id_key, read_rows

REQUIRED = {"id", "host", "text"}
VALID_STATUSES = {"draft", "client-edited", "approved", "generated", "regenerate", "skip", ""}
ID_RE = re.compile(r"^\d{1,6}(?:\.\d{1,6})*$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("--allow-draft", action="store_true", help="Do not fail when draft rows are present.")
    args = parser.parse_args()

    rows, headers = read_rows(args.script)
    errors: list[str] = []
    warnings: list[str] = []

    missing = REQUIRED.difference(headers)
    if missing:
        errors.append(f"Missing required columns: {', '.join(sorted(missing))}")

    seen: set[str] = set()
    prior_key: tuple[int, ...] | None = None
    hosts: set[str] = set()

    for index, row in enumerate(rows, start=2):
        row_id = (row.get("id") or "").strip()
        host = (row.get("host") or "").strip()
        text = (row.get("text") or "").strip()
        status = (row.get("status") or "").strip()

        if not row_id:
            errors.append(f"Row {index}: missing id")
        elif not ID_RE.match(row_id):
            errors.append(f"Row {index}: invalid id '{row_id}'")
        elif row_id in seen:
            errors.append(f"Row {index}: duplicate id '{row_id}'")
        else:
            seen.add(row_id)
            key = id_key(row_id)
            if prior_key is not None and key < prior_key:
                warnings.append(f"Row {index}: id '{row_id}' sorts before previous row")
            prior_key = key

        if not host:
            errors.append(f"Row {index}: missing host")
        else:
            hosts.add(host)

        if not text and status != "skip" and not (args.allow_draft and status == "draft"):
            errors.append(f"Row {index}: missing text")

        if status not in VALID_STATUSES:
            errors.append(f"Row {index}: invalid status '{status}'")
        if status == "draft" and not args.allow_draft:
            warnings.append(f"Row {index}: draft row is not ready for audio generation")

    print(f"Rows: {len(rows)}")
    print(f"Hosts: {', '.join(sorted(hosts)) if hosts else 'none'}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
