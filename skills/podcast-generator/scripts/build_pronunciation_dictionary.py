#!/usr/bin/env python3
"""Build ElevenLabs pronunciation dictionary artifacts from a glossary."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from table_io import read_rows

APPROVED_STATUSES = {"approved", "tested"}
PLS_NS = "http://www.w3.org/2005/01/pronunciation-lexicon"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
API_BASE = "https://api.elevenlabs.io/v1"


def infer_rule(row: dict[str, str]) -> dict[str, str] | None:
    term = (row.get("term") or "").strip()
    pronunciation = (row.get("pronunciation") or "").strip()
    if not term or not pronunciation:
        return None

    kind = (row.get("pronunciation_kind") or row.get("kind") or "").strip().lower()
    alphabet = (row.get("alphabet") or "").strip().lower()
    if not kind:
        kind = "phoneme" if alphabet == "ipa" else "alias"

    if kind == "phoneme":
        alphabet = alphabet or "ipa"
        if alphabet != "ipa":
            raise SystemExit(f"{term}: only IPA phoneme rules are supported by this exporter.")
        return {
            "string_to_replace": term,
            "type": "phoneme",
            "phoneme": pronunciation,
            "alphabet": alphabet,
        }
    if kind == "alias":
        return {
            "string_to_replace": term,
            "type": "alias",
            "alias": pronunciation,
        }
    if kind == "review":
        return None
    raise SystemExit(f"{term}: unsupported pronunciation_kind '{kind}'. Use alias, phoneme, or review.")


def load_rules(path: Path, statuses: set[str]) -> list[dict[str, str]]:
    rows, _ = read_rows(path)
    rules: list[dict[str, str]] = []
    for row in rows:
        status = (row.get("status") or "").strip().lower()
        if status not in statuses:
            continue
        rule = infer_rule(row)
        if rule:
            rules.append(rule)
    return rules


def write_json_payload(path: Path, rules: list[dict[str, str]], name: str, description: str | None) -> None:
    payload: dict[str, object] = {"name": name, "rules": rules}
    if description:
        payload["description"] = description
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_pls(path: Path, rules: list[dict[str, str]], language_code: str) -> None:
    ET.register_namespace("", PLS_NS)
    ET.register_namespace("xsi", XSI_NS)
    root = ET.Element(
        f"{{{PLS_NS}}}lexicon",
        {
            "version": "1.0",
            "alphabet": "ipa",
            "{http://www.w3.org/XML/1998/namespace}lang": language_code,
            f"{{{XSI_NS}}}schemaLocation": (
                "http://www.w3.org/2005/01/pronunciation-lexicon "
                "http://www.w3.org/TR/2007/CR-pronunciation-lexicon-20071212/pls.xsd"
            ),
        },
    )
    for rule in rules:
        lexeme = ET.SubElement(root, f"{{{PLS_NS}}}lexeme")
        grapheme = ET.SubElement(lexeme, f"{{{PLS_NS}}}grapheme")
        grapheme.text = rule["string_to_replace"]
        if rule["type"] == "phoneme":
            phoneme = ET.SubElement(lexeme, f"{{{PLS_NS}}}phoneme")
            phoneme.text = rule["phoneme"]
        else:
            alias = ET.SubElement(lexeme, f"{{{PLS_NS}}}alias")
            alias.text = rule["alias"]

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def post_json(path: str, payload: dict[str, object], api_key: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ElevenLabs request failed ({error.code}): {detail}") from error


def create_dictionary(rules: list[dict[str, str]], name: str, description: str | None, workspace_access: str | None) -> dict[str, object]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit("ELEVENLABS_API_KEY is not set.")
    payload: dict[str, object] = {"name": name, "rules": rules}
    if description:
        payload["description"] = description
    if workspace_access:
        payload["workspace_access"] = workspace_access
    return post_json("/pronunciation-dictionaries/add-from-rules", payload, api_key)


def update_dictionary(dictionary_id: str, rules: list[dict[str, str]]) -> dict[str, object]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit("ELEVENLABS_API_KEY is not set.")
    return post_json(f"/pronunciation-dictionaries/{dictionary_id}/add-rules", {"rules": rules}, api_key)


def write_manifest(path: Path, response: dict[str, object], name: str, rules: list[dict[str, str]]) -> None:
    dictionary_id = str(response.get("id") or "")
    version_id = str(response.get("version_id") or "")
    if not dictionary_id or not version_id:
        raise SystemExit(f"ElevenLabs response did not include id and version_id: {response}")
    manifest = {
        "pronunciation_dictionary_id": dictionary_id,
        "version_id": version_id,
        "locator": f"{dictionary_id}:{version_id}",
        "name": response.get("name") or name,
        "version_rules_num": response.get("version_rules_num"),
        "rules_count": len(rules),
        "created_at_unix": int(time.time()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("glossary", type=Path)
    parser.add_argument("--name", default="Podcast Pronunciation Dictionary")
    parser.add_argument("--description")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-pls", type=Path)
    parser.add_argument("--out-manifest", type=Path, help="Write the ElevenLabs dictionary locator response.")
    parser.add_argument("--language-code", default="en-US")
    parser.add_argument("--create", action="store_true", help="Create a new ElevenLabs pronunciation dictionary.")
    parser.add_argument("--update-dictionary-id", help="Add or replace rules in an existing ElevenLabs dictionary.")
    parser.add_argument(
        "--workspace-access",
        choices=["admin", "editor", "viewer"],
        help="Optional workspace access for dictionaries created with --create.",
    )
    parser.add_argument(
        "--include-status",
        action="append",
        default=[],
        help="Approved row status to include. Defaults to approved and tested.",
    )
    args = parser.parse_args()

    statuses = {value.strip().lower() for value in args.include_status if value.strip()} or APPROVED_STATUSES
    rules = load_rules(args.glossary, statuses)
    if not rules:
        raise SystemExit("No approved pronunciation rules found.")
    if args.create and args.update_dictionary_id:
        raise SystemExit("Use either --create or --update-dictionary-id, not both.")

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        write_json_payload(args.out_json, rules, args.name, args.description)
        print(f"Wrote JSON payload: {args.out_json}")
    if args.out_pls:
        args.out_pls.parent.mkdir(parents=True, exist_ok=True)
        write_pls(args.out_pls, rules, args.language_code)
        print(f"Wrote PLS lexicon: {args.out_pls}")

    response: dict[str, object] | None = None
    if args.create:
        response = create_dictionary(rules, args.name, args.description, args.workspace_access)
        print(f"Created ElevenLabs dictionary: {response['id']}:{response['version_id']}")
    elif args.update_dictionary_id:
        response = update_dictionary(args.update_dictionary_id, rules)
        print(f"Updated ElevenLabs dictionary: {response['id']}:{response['version_id']}")
    if response and args.out_manifest:
        write_manifest(args.out_manifest, response, args.name, rules)
        print(f"Wrote locator manifest: {args.out_manifest}")
    elif response:
        print(f"Locator: {response['id']}:{response['version_id']}")

    if not args.out_json and not args.out_pls and not response:
        json.dump({"name": args.name, "rules": rules}, sys.stdout, indent=2, ensure_ascii=False)
        print()
        print(f"Rules: {len(rules)}", file=sys.stderr)
    else:
        print(f"Rules: {len(rules)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
