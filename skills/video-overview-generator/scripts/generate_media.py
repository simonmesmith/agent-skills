#!/usr/bin/env python3
"""Generate row-level OpenAI speech and image assets with hash-based caching."""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import sys
from typing import Any, Dict

from storyboard_lib import (
    load_storyboard,
    media_hash,
    read_manifest,
    save_storyboard,
    selected_rows,
    write_manifest,
)


def require_openai():
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the OpenAI Python package first: python -m pip install openai") from exc
    return OpenAI()


def generate_audio(client: Any, row: Dict[str, Any], out_path: Path, dry_run: bool) -> None:
    if dry_run:
        out_path.write_bytes(b"")
        return
    response = client.audio.speech.create(
        model=row["audio_model"],
        voice=row["voice"],
        input=row["narration_text"],
        response_format="mp3",
    )
    out_path.write_bytes(response.read())


def generate_image(client: Any, row: Dict[str, Any], out_path: Path, dry_run: bool) -> None:
    if dry_run:
        out_path.write_bytes(b"")
        return
    prompt_parts = [row["image_prompt"]]
    if row.get("image_style"):
        prompt_parts.append(f"Style: {row['image_style']}")
    prompt_parts.append("Format: 16:9 landscape still image. No watermark. No readable text unless explicitly requested.")
    response = client.images.generate(
        model=row["image_model"],
        prompt="\n".join(prompt_parts),
        size=row["image_size"],
        quality=row["image_quality"],
    )
    b64 = response.data[0].b64_json
    if not b64:
        raise RuntimeError("image response did not include base64 image data")
    out_path.write_bytes(base64.b64decode(b64))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("storyboard")
    parser.add_argument("--media-dir", default="media")
    parser.add_argument("--ids", help="Comma-separated row IDs to process")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--audio-only", action="store_true")
    parser.add_argument("--image-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Create empty placeholder files without API calls")
    args = parser.parse_args()

    data = load_storyboard(args.storyboard)
    media_dir = Path(args.media_dir)
    audio_dir = media_dir / "audio"
    image_dir = media_dir / "images"
    audio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = media_dir / "manifest.json"
    manifest = read_manifest(manifest_path)
    manifest.setdefault("rows", {})
    client = None if args.dry_run else require_openai()

    for _, row in selected_rows(data, args.ids):
        row_manifest = manifest["rows"].setdefault(row["id"], {})
        audio_hash = media_hash(row, "audio")
        image_hash = media_hash(row, "image")
        audio_path = audio_dir / f"{row['id']}-{audio_hash}.mp3"
        image_path = image_dir / f"{row['id']}-{image_hash}.png"

        if not args.image_only:
            if args.force or row_manifest.get("audio_hash") != audio_hash or not audio_path.exists():
                print(f"Generating audio: {row['id']}")
                generate_audio(client, row, audio_path, args.dry_run)
            row["audio_path"] = str(audio_path)
            row_manifest["audio_hash"] = audio_hash
            row_manifest["audio_path"] = str(audio_path)

        if not args.audio_only:
            if args.force or row_manifest.get("image_hash") != image_hash or not image_path.exists():
                print(f"Generating image: {row['id']}")
                generate_image(client, row, image_path, args.dry_run)
            row["image_path"] = str(image_path)
            row_manifest["image_hash"] = image_hash
            row_manifest["image_path"] = str(image_path)

        row["content_hash"] = {"audio": audio_hash, "image": image_hash}
        row["status"] = "media_generated" if not args.dry_run else "dry_run"

    save_storyboard(data, args.storyboard)
    write_manifest(manifest_path, manifest)
    print(f"Updated {args.storyboard} and {manifest_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
