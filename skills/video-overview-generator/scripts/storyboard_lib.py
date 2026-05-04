#!/usr/bin/env python3
"""Shared helpers for Video Overview Generator scripts."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


DEFAULTS: Dict[str, Any] = {
    "image_model": "gpt-image-2",
    "image_size": "2048x1152",
    "image_quality": "medium",
    "audio_model": "gpt-4o-mini-tts",
    "voice": "alloy",
    "transition": "fade",
}

CSV_FIELDS = [
    "id",
    "section_title",
    "source_refs",
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
    "audio_path",
    "audio_duration",
    "image_path",
    "status",
]


def load_storyboard(path: str | Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_storyboard(data)


def save_storyboard(data: Dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_storyboard(data: Dict[str, Any]) -> Dict[str, Any]:
    data.setdefault("title", "Video Overview")
    data.setdefault("description", "")
    defaults = dict(DEFAULTS)
    defaults.update(data.get("defaults") or {})
    data["defaults"] = defaults
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ValueError("storyboard.json must contain a top-level rows array")
    seen = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"row {index} must be an object")
        row.setdefault("id", f"row_{index:03d}")
        if row["id"] in seen:
            raise ValueError(f"duplicate row id: {row['id']}")
        seen.add(row["id"])
        row.setdefault("section_title", "")
        row.setdefault("source_refs", [])
        row.setdefault("caption_text", row.get("narration_text", ""))
        row.setdefault("image_style", "")
        row.setdefault("transition", defaults["transition"])
        row.setdefault("status", "draft")
        for key in ("voice", "audio_model", "image_model", "image_size", "image_quality"):
            row.setdefault(key, defaults[key])
    return data


def validate_storyboard(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    try:
        normalize_storyboard(data)
    except Exception as exc:
        return [str(exc)]
    for row in data["rows"]:
        prefix = row.get("id", "<missing id>")
        for field in ("id", "narration_text", "image_prompt"):
            if not str(row.get(field, "")).strip():
                errors.append(f"{prefix}: missing required field {field}")
        if len(str(row.get("narration_text", ""))) > 4096:
            errors.append(f"{prefix}: narration_text exceeds 4096 characters")
        refs = row.get("source_refs", [])
        if refs and not isinstance(refs, list):
            errors.append(f"{prefix}: source_refs must be an array")
        override = row.get("duration_override")
        if override not in (None, ""):
            try:
                if float(override) <= 0:
                    errors.append(f"{prefix}: duration_override must be positive")
            except ValueError:
                errors.append(f"{prefix}: duration_override must be numeric")
    return errors


def media_hash(row: Dict[str, Any], kind: str) -> str:
    if kind == "audio":
        keys = ["narration_text", "voice", "audio_model"]
    elif kind == "image":
        keys = ["image_prompt", "image_style", "image_model", "image_size", "image_quality"]
    else:
        raise ValueError("kind must be audio or image")
    payload = {key: row.get(key, "") for key in keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def export_csv(data: Dict[str, Any], path: str | Path) -> None:
    rows = normalize_storyboard(data)["rows"]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            flat = {field: row.get(field, "") for field in CSV_FIELDS}
            flat["source_refs"] = json.dumps(row.get("source_refs", []), ensure_ascii=False)
            writer.writerow(flat)


def import_csv(path: str | Path, title: str = "Video Overview") -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for raw in reader:
            row = {key: value for key, value in raw.items() if value not in (None, "")}
            if "source_refs" in row:
                try:
                    row["source_refs"] = json.loads(row["source_refs"])
                except json.JSONDecodeError:
                    row["source_refs"] = [part.strip() for part in row["source_refs"].split(";") if part.strip()]
            rows.append(row)
    return normalize_storyboard({"title": title, "defaults": dict(DEFAULTS), "rows": rows})


def read_manifest(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"rows": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def write_manifest(path: str | Path, manifest: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def selected_rows(data: Dict[str, Any], ids: str | None) -> Iterable[Tuple[int, Dict[str, Any]]]:
    wanted = None if not ids else {item.strip() for item in ids.split(",") if item.strip()}
    for index, row in enumerate(data["rows"], start=1):
        if wanted is None or row["id"] in wanted:
            yield index, row
