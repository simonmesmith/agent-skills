#!/usr/bin/env python3
"""Create a starter pronunciation glossary from source files."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9-]{1,}\b")
BRANDISH_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]+|[0-9]+[A-Za-z]*)\b")
MEDICAL_SUFFIX_RE = re.compile(r"\b[A-Za-z]+(?:mab|nib|stat|vir|tinib|zumab|ximab|oxetine|azole|cycline|platin|parib|gliflozin|gliptin|pril|sartan)\b", re.I)
WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b")

SKIP = {
    "The",
    "This",
    "That",
    "These",
    "Those",
    "There",
    "They",
    "Their",
    "When",
    "Where",
    "What",
    "Why",
    "How",
    "And",
    "But",
    "For",
}


def classify(term: str) -> str:
    if ACRONYM_RE.fullmatch(term):
        return "acronym"
    if MEDICAL_SUFFIX_RE.fullmatch(term):
        return "medical-term"
    if BRANDISH_RE.fullmatch(term):
        return "brand-or-product"
    return "proper-name"


def extract_text(path: Path) -> str:
    if path.suffix.lower() not in {".txt", ".md", ".csv", ".tsv"}:
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("pronunciation_glossary.csv"))
    args = parser.parse_args()

    found: dict[str, dict[str, str]] = {}
    for source in args.sources:
        paths = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in paths:
            if not path.is_file():
                continue
            text = extract_text(path)
            if not text:
                continue
            candidates = set(ACRONYM_RE.findall(text))
            candidates.update(BRANDISH_RE.findall(text))
            candidates.update(MEDICAL_SUFFIX_RE.findall(text))
            for word in WORD_RE.findall(text):
                if word[:1].isupper() and word not in SKIP and len(word) > 3:
                    candidates.add(word)
            for term in candidates:
                if term in SKIP or term.isdigit():
                    continue
                found.setdefault(
                    term,
                    {
                        "term": term,
                        "type": classify(term),
                        "source": str(path),
                        "pronunciation": "",
                        "notes": "Review before audio generation.",
                    },
                )

    rows = sorted(found.values(), key=lambda row: (row["type"], row["term"].lower()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["term", "type", "source", "pronunciation", "notes"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} terms to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
