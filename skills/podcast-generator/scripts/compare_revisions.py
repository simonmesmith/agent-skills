#!/usr/bin/env python3
"""Compare old and revised podcast script tables."""

from __future__ import annotations

import argparse
from pathlib import Path

from table_io import id_key, read_rows, write_csv


def row_key(row: dict[str, str]) -> str:
    return (row.get("source_id") or row.get("id") or "").strip()


def normalize(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        (row.get("host") or "").strip(),
        (row.get("text") or "").strip(),
        (row.get("status") or "").strip(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_script", type=Path)
    parser.add_argument("revised_script", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    old_data, _ = read_rows(args.old_script)
    new_data, _ = read_rows(args.revised_script)
    old_rows = {row_key(row): row for row in old_data if row_key(row)}
    new_rows = {row_key(row): row for row in new_data if row_key(row)}
    all_keys = sorted(set(old_rows) | set(new_rows), key=id_key)

    report: list[dict[str, str]] = []
    for key in all_keys:
        old = old_rows.get(key)
        new = new_rows.get(key)
        if old and not new:
            action = "removed"
        elif new and not old:
            action = "inserted"
        elif new and (new.get("status") or "").strip() == "skip":
            action = "skipped"
        elif old and new and normalize(old) == normalize(new):
            action = "unchanged"
        else:
            action = "changed"

        report.append(
            {
                "id": key,
                "classification": action,
                "old_host": (old or {}).get("host", ""),
                "new_host": (new or {}).get("host", ""),
                "old_text": (old or {}).get("text", ""),
                "new_text": (new or {}).get("text", ""),
                "recommended_audio_action": "reuse" if action == "unchanged" else "regenerate_or_exclude",
            }
        )

    write_csv(args.out, report, list(report[0].keys()) if report else ["id", "classification"])

    counts: dict[str, int] = {}
    for row in report:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    print(", ".join(f"{key}: {value}" for key, value in sorted(counts.items())) or "No rows")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
