#!/usr/bin/env python3
"""Patch one storyboard row and optionally regenerate the affected media."""

from __future__ import annotations

import argparse
import subprocess
import sys

from storyboard_lib import load_storyboard, save_storyboard


PATCHABLE = {
    "narration_text",
    "caption_text",
    "image_prompt",
    "image_style",
    "voice",
    "audio_model",
    "image_model",
    "image_size",
    "image_quality",
    "transition",
    "duration_override",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("storyboard")
    parser.add_argument("row_id")
    parser.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--media-dir", default="media")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = load_storyboard(args.storyboard)
    row = next((item for item in data["rows"] if item["id"] == args.row_id), None)
    if not row:
        raise SystemExit(f"row not found: {args.row_id}")

    for assignment in args.set:
        if "=" not in assignment:
            raise SystemExit(f"expected FIELD=VALUE: {assignment}")
        key, value = assignment.split("=", 1)
        if key not in PATCHABLE:
            raise SystemExit(f"field is not patchable: {key}")
        row[key] = value

    save_storyboard(data, args.storyboard)
    print(f"Patched {args.row_id} in {args.storyboard}")
    if args.skip_generate:
        return 0
    cmd = [
        sys.executable,
        __file__.replace("regen_item.py", "generate_media.py"),
        args.storyboard,
        "--media-dir",
        args.media_dir,
        "--ids",
        args.row_id,
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
