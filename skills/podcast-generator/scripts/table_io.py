"""Small CSV/XLSX table helpers for podcast dialogue scripts."""

from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CELL_RE = re.compile(r"([A-Z]+)(\d+)")


def id_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.strip().split("."))


def column_index(cell_ref: str) -> int:
    match = CELL_RE.match(cell_ref)
    if not match:
        return 0
    letters = match.group(1)
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall("x:si", NS):
        text = "".join(node.text or "" for node in item.findall(".//x:t", NS))
        values.append(text)
    return values


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS))
    value_node = cell.find("x:v", NS)
    if value_node is None or value_node.text is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value_node.text)]
    return value_node.text


def read_xlsx(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_shared_strings(archive)
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    matrix: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: list[str] = []
        for cell in row.findall("x:c", NS):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = cell_value(cell, shared_strings)
        matrix.append(values)

    if not matrix:
        return [], []
    headers = [value.strip() for value in matrix[0]]
    rows = []
    for values in matrix[1:]:
        row = {headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))}
        if any(value.strip() for value in row.values()):
            rows.append(row)
    return rows, headers


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if path.suffix.lower() == ".xlsx":
        return read_xlsx(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader), reader.fieldnames or []


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
