#!/usr/bin/env python3
"""Extract text from source documents into a source_map.json file."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


TEXT_EXTS = {".txt", ".md", ".markdown", ".rst"}


def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for para in root.findall(".//w:p", ns):
        texts = [node.text or "" for node in para.findall(".//w:t", ns)]
        if texts:
            paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def read_pdf(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout
    except FileNotFoundError as exc:
        raise RuntimeError("pdftotext is required for PDF ingestion") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"pdftotext failed for {path}: {exc.stderr}") from exc


def read_csv(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        return "\n".join(" | ".join(cell.strip() for cell in row) for row in reader)


def read_json(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(data, indent=2, ensure_ascii=False)


def read_one(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTS:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix == ".csv":
        return read_csv(path)
    if suffix == ".json":
        return read_json(path)
    raise RuntimeError(f"unsupported source type: {path}")


def expand_inputs(paths: list[str]) -> list[Path]:
    found = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in TEXT_EXTS | {".docx", ".pdf", ".csv", ".json"}:
                    found.append(child)
        elif path.exists():
            found.append(path)
        else:
            raise FileNotFoundError(raw)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", help="Files or folders to ingest")
    parser.add_argument("--out", default="video-overview/source_map.json")
    args = parser.parse_args()

    entries = []
    for index, path in enumerate(expand_inputs(args.sources), start=1):
        try:
            text = read_one(path).strip()
            status = "ok"
            error = ""
        except Exception as exc:  # keep processing other documents
            text = ""
            status = "error"
            error = str(exc)
        entries.append(
            {
                "id": f"source_{index:03d}",
                "path": str(path),
                "filename": path.name,
                "status": status,
                "error": error,
                "char_count": len(text),
                "text": text,
            }
        )

    output = {"sources": entries}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    errors = [entry for entry in entries if entry["status"] != "ok"]
    print(f"Wrote {out_path} with {len(entries)} source(s), {len(errors)} error(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
