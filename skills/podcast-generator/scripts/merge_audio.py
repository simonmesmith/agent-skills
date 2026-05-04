#!/usr/bin/env python3
"""Merge generated podcast audio files listed in audio_manifest.csv."""

from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concat-file", type=Path)
    parser.add_argument("--reencode", action="store_true", help="Re-encode instead of stream-copying.")
    args = parser.parse_args()

    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: int(row.get("order") or "0"))

    files = [Path(row["file"]) for row in rows if row.get("file") and row.get("status") != "dry-run"]
    missing = [path for path in files if not path.exists()]
    if missing:
        raise SystemExit("Missing audio files:\n" + "\n".join(str(path) for path in missing))
    if not files:
        raise SystemExit("No generated audio files found in manifest.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    concat_file = args.concat_file or args.out.with_suffix(".concat.txt")
    concat_file.write_text("".join(f"file '{path.resolve()}'\n" for path in files), encoding="utf-8")

    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file)]
    if args.reencode:
        command += ["-ar", "44100", "-ac", "2", "-b:a", "192k"]
    else:
        command += ["-c", "copy"]
    command.append(str(args.out))

    subprocess.run(command, check=True)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
