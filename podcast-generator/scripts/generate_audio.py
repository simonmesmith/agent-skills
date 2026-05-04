#!/usr/bin/env python3
"""Generate ordered ElevenLabs podcast dialogue audio from a script table."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from table_io import id_key, read_rows

APPROVED_STATUSES = {"approved", "regenerate", "generated", ""}
DEFAULT_VOICES = {
    "host_a": "CwhRBWXzGAHq8TQ4Fs17",  # Roger - Laid-Back, Casual, Resonant
    "host_b": "EXAVITQu4vr4xnSDxMaL",  # Sarah - Mature, Reassuring, Confident
}


def parse_voice(values: list[str]) -> dict[str, str]:
    voices: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --voice '{value}'. Use host=voice_id.")
        host, voice_id = value.split("=", 1)
        voices[host.strip()] = voice_id.strip()
    return voices


def parse_locator(values: list[str]) -> list[dict[str, str]]:
    locators: list[dict[str, str]] = []
    for value in values:
        if ":" not in value:
            raise SystemExit(f"Invalid pronunciation locator '{value}'. Use dictionary_id:version_id.")
        dictionary_id, version_id = value.split(":", 1)
        locators.append({"pronunciation_dictionary_id": dictionary_id, "version_id": version_id})
    return locators


def load_pronunciation_glossary(path: Path | None) -> list[tuple[str, str]]:
    if path is None:
        return []
    rows, _ = read_rows(path)
    replacements: list[tuple[str, str]] = []
    for row in rows:
        term = (row.get("term") or "").strip()
        pronunciation = (row.get("pronunciation") or "").strip()
        if term and pronunciation:
            replacements.append((term, pronunciation))
    return sorted(replacements, key=lambda item: len(item[0]), reverse=True)


def apply_pronunciations(text: str, replacements: list[tuple[str, str]]) -> str:
    updated = text
    for term, pronunciation in replacements:
        pattern = re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])")
        updated = pattern.sub(pronunciation, updated)
    return updated


def script_order(row: dict[str, str]) -> tuple[int, ...]:
    return id_key(row.get("production_id") or row.get("id") or "0")


def chunk_rows(rows: list[dict[str, str]], max_chars: int) -> list[list[dict[str, str]]]:
    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_chars = 0
    for row in rows:
        text_len = len(row.get("text", ""))
        if current and current_chars + text_len > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(row)
        current_chars += text_len
    if current:
        chunks.append(current)
    return chunks


def call_elevenlabs(payload: dict, output: Path, api_key: str, output_format: str) -> None:
    url = f"https://api.elevenlabs.io/v1/text-to-dialogue?output_format={output_format}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            output.write_bytes(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ElevenLabs request failed ({error.code}): {detail}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("audio"))
    parser.add_argument("--voice", action="append", default=[], help="Host voice mapping, e.g. host_a=VOICE_ID")
    parser.add_argument("--use-default-voices", action="store_true", help="Use Roger for host_a and Sarah for host_b.")
    parser.add_argument("--mode", choices=["chunk", "line"], default="chunk")
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument("--model-id", default="eleven_v3")
    parser.add_argument("--output-format", default="mp3_44100_128")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--language-code")
    parser.add_argument("--pronunciation-locator", action="append", default=[], help="dictionary_id:version_id")
    parser.add_argument("--pronunciation-glossary", type=Path, help="CSV/XLSX with term and pronunciation columns.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = [
        row
        for row in sorted(read_rows(args.script)[0], key=script_order)
        if (row.get("status") or "").strip() in APPROVED_STATUSES and (row.get("text") or "").strip()
    ]
    voices = dict(DEFAULT_VOICES) if args.use_default_voices else {}
    voices.update(parse_voice(args.voice))
    missing_hosts = sorted({row.get("host", "").strip() for row in rows} - set(voices))
    if missing_hosts:
        raise SystemExit(f"Missing voice mappings for: {', '.join(missing_hosts)}")

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key and not args.dry_run:
        raise SystemExit("ELEVENLABS_API_KEY is not set. Use --dry-run to create the manifest without live generation.")

    groups = [[row] for row in rows] if args.mode == "line" else chunk_rows(rows, args.max_chars)
    audio_dir = args.out_dir / ("lines" if args.mode == "line" else "chunks")
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "audio_manifest.csv"
    locators = parse_locator(args.pronunciation_locator)
    pronunciation_replacements = load_pronunciation_glossary(args.pronunciation_glossary)
    manifest: list[dict[str, str]] = []

    for index, group in enumerate(groups, start=1):
        start_id = group[0].get("production_id") or group[0].get("id")
        end_id = group[-1].get("production_id") or group[-1].get("id")
        if args.mode == "line":
            row = group[0]
            filename = f"{start_id}_{row.get('host')}.mp3"
        else:
            filename = f"chunk_{start_id}-{end_id}.mp3"
        output = audio_dir / filename

        payload: dict = {
            "model_id": args.model_id,
            "inputs": [
                {"text": apply_pronunciations(row["text"], pronunciation_replacements), "voice_id": voices[row["host"].strip()]}
                for row in group
            ],
        }
        if args.seed is not None:
            payload["seed"] = args.seed
        if args.language_code:
            payload["language_code"] = args.language_code
        if locators:
            payload["pronunciation_dictionary_locators"] = locators

        if args.dry_run:
            output.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        else:
            call_elevenlabs(payload, output, api_key or "", args.output_format)
            time.sleep(0.2)

        manifest.append(
            {
                "order": str(index),
                "id_start": start_id or "",
                "id_end": end_id or "",
                "mode": args.mode,
                "file": str(output),
                "status": "dry-run" if args.dry_run else "generated",
            }
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["order", "id_start", "id_end", "mode", "file", "status"])
        writer.writeheader()
        writer.writerows(manifest)

    print(f"Wrote manifest: {manifest_path}")
    print(f"Items: {len(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
