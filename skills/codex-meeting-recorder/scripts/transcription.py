#!/usr/bin/env python3
"""Bundled transcription helper for Codex Meeting Recorder."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


DEFAULT_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_RESPONSE_FORMAT = "text"
DEFAULT_CHUNKING_STRATEGY = "auto"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_RESPONSE_FORMATS = {"text", "json"}


def die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def ensure_api_key(dry_run: bool) -> None:
    if os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is set.", file=sys.stderr)
        return
    if dry_run:
        warn("OPENAI_API_KEY is not set; dry-run only.")
        return
    die("OPENAI_API_KEY is not set. Export it before running.")


def create_client() -> Any:
    try:
        from openai import OpenAI
    except ImportError:
        die("openai SDK not installed. Install with `uv pip install openai` or run via `uv run --with openai`.")
    return OpenAI()


def normalize_response_format(value: str | None) -> str:
    fmt = (value or DEFAULT_RESPONSE_FORMAT).strip().lower()
    if fmt not in ALLOWED_RESPONSE_FORMATS:
        die("response-format must be one of: " + ", ".join(sorted(ALLOWED_RESPONSE_FORMATS)))
    return fmt


def normalize_chunking_strategy(value: str | None) -> Any:
    raw = (value or DEFAULT_CHUNKING_STRATEGY).strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            die("chunking-strategy JSON is invalid")
    return raw


def validate_audio(path: Path) -> None:
    if not path.exists():
        die(f"Audio file not found: {path}")
    size = path.stat().st_size
    if size == 0:
        die(f"Audio file is empty: {path}")
    if size > MAX_AUDIO_BYTES:
        warn(f"Audio file exceeds the common 25MB direct-upload limit ({size} bytes): {path}")


def format_output(result: Any, response_format: str) -> str:
    if response_format == "text":
        text = getattr(result, "text", None)
        return text if isinstance(text, str) else str(result)
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(), indent=2)
    if isinstance(result, (dict, list)):
        return json.dumps(result, indent=2)
    return json.dumps({"text": getattr(result, "text", str(result))}, indent=2)


def transcribe(args: argparse.Namespace) -> str:
    audio_path = Path(args.audio)
    validate_audio(audio_path)
    ensure_api_key(args.dry_run)

    payload: dict[str, Any] = {
        "model": args.model,
        "response_format": args.response_format,
        "chunking_strategy": args.chunking_strategy,
    }
    if args.language:
        payload["language"] = args.language
    if args.prompt:
        payload["prompt"] = args.prompt

    if args.dry_run:
        return json.dumps(payload, indent=2)

    client = create_client()
    with audio_path.open("rb") as audio_file:
        result = client.audio.transcriptions.create(file=audio_file, **payload)
    return format_output(result, args.response_format)


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe a meeting recording using OpenAI.")
    parser.add_argument("audio", help="Recording file to transcribe")
    parser.add_argument("--out", required=True, help="Transcript output path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Transcription model (default: {DEFAULT_MODEL})")
    parser.add_argument("--response-format", default=DEFAULT_RESPONSE_FORMAT, help="text or json")
    parser.add_argument("--chunking-strategy", default=DEFAULT_CHUNKING_STRATEGY, help="Chunking strategy, default: auto")
    parser.add_argument("--language", help="Optional language hint, e.g. en")
    parser.add_argument("--prompt", help="Optional transcription prompt")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the API payload without calling OpenAI")

    args = parser.parse_args()
    args.response_format = normalize_response_format(args.response_format)
    args.chunking_strategy = normalize_chunking_strategy(args.chunking_strategy)

    output = transcribe(args)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
