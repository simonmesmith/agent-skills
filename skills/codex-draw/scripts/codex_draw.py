#!/usr/bin/env python3
"""Run a local Codex Draw vector workspace."""

from __future__ import annotations

import argparse
import copy
import html
import json
import mimetypes
import re
import subprocess
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


SKILL_DIR = Path(__file__).resolve().parents[1]
APP_DIR = SKILL_DIR / "assets" / "app"
DEFAULT_PORT = 8765
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def default_scene() -> dict[str, Any]:
    return {
        "version": 1,
        "revision": 1,
        "canvas": {"width": 1200, "height": 800, "background": "#ffffff"},
        "objects": [],
        "selection": [],
    }


class SceneStore:
    def __init__(self, workspace: Path, scene_name: str | None) -> None:
        self.workspace = workspace.resolve()
        self.exports_dir = self.workspace / "exports"
        self.assets_dir = self.workspace / "assets"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.scene_path = self.workspace / scene_name if scene_name else self.unique_scene_path()
        self.scene = self._load()
        self.save()

    def _load(self) -> dict[str, Any]:
        if not self.scene_path.exists():
            return default_scene()
        with self.scene_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return normalize_scene(loaded)

    def with_paths(self) -> dict[str, Any]:
        scene = copy.deepcopy(self.scene)
        scene["paths"] = {
            "workspace": str(self.workspace),
            "scene": str(self.scene_path),
            "exports": str(self.exports_dir),
            "assets": str(self.assets_dir),
        }
        return scene

    def bump(self) -> None:
        self.scene["revision"] = int(self.scene.get("revision") or 0) + 1

    def save(self) -> None:
        self.scene = normalize_scene(self.scene)
        with self.scene_path.open("w", encoding="utf-8") as handle:
            json.dump(self.scene, handle, indent=2)
            handle.write("\n")

    def replace(self, scene: dict[str, Any]) -> dict[str, Any]:
        self.scene = normalize_scene(scene)
        self.bump()
        self.save()
        return self.with_paths()

    def add_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        had_name = bool(str(payload.get("name") or "").strip())
        had_z_index = "zIndex" in payload
        item = normalize_object(payload)
        if not item.get("id"):
            item["id"] = self.next_id(str(item["type"]))
        if any(existing["id"] == item["id"] for existing in self.scene["objects"]):
            item["id"] = self.next_id(str(item["type"]))
        if not had_name:
            item["name"] = item["id"]
        if not had_z_index:
            item["zIndex"] = next_z_index(self.scene["objects"])
        self.scene["objects"].append(item)
        self.scene["selection"] = [item["id"]]
        self.bump()
        self.save()
        return self.with_paths()

    def patch_object(self, object_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        item = self.find_object(object_id)
        if item is None:
            raise KeyError(object_id)
        item.update(payload)
        normalized = normalize_object(item)
        item.clear()
        item.update(normalized)
        self.bump()
        self.save()
        return self.with_paths()

    def delete_object(self, object_id: str) -> dict[str, Any]:
        before = len(self.scene["objects"])
        self.scene["objects"] = [item for item in self.scene["objects"] if item["id"] != object_id]
        if len(self.scene["objects"]) == before:
            raise KeyError(object_id)
        self.scene["selection"] = [item for item in self.scene["selection"] if item != object_id]
        self.bump()
        self.save()
        return self.with_paths()

    def set_selection(self, selection: list[str]) -> dict[str, Any]:
        existing = {item["id"] for item in self.scene["objects"]}
        self.scene["selection"] = [item for item in selection if item in existing]
        self.bump()
        self.save()
        return self.with_paths()

    def selection_payload(self) -> dict[str, Any]:
        selected = set(self.scene.get("selection", []))
        return {
            "selection": list(self.scene.get("selection", [])),
            "objects": [item for item in self.scene["objects"] if item["id"] in selected],
        }

    def reorder(self, order: list[str]) -> dict[str, Any]:
        order_map = {object_id: index + 1 for index, object_id in enumerate(order)}
        next_index = len(order_map) + 1
        for item in sorted(self.scene["objects"], key=lambda obj: obj.get("zIndex", 0)):
            if item["id"] in order_map:
                item["zIndex"] = order_map[item["id"]]
            else:
                item["zIndex"] = next_index
                next_index += 1
        self.bump()
        self.save()
        return self.with_paths()

    def find_object(self, object_id: str) -> dict[str, Any] | None:
        return next((item for item in self.scene["objects"] if item["id"] == object_id), None)

    def next_id(self, object_type: str) -> str:
        used = {item["id"] for item in self.scene["objects"]}
        prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", object_type).strip("-") or "object"
        index = 1
        while f"{prefix}-{index}" in used:
            index += 1
        return f"{prefix}-{index}"

    def export_svg(self) -> Path:
        svg = scene_to_svg(self.scene)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.exports_dir / f"codex-draw-{stamp}.svg"
        path.write_text(svg, encoding="utf-8")
        return path

    def export_svg_payload(self) -> dict[str, str]:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return {
            "filename": f"codex-draw-{stamp}.svg",
            "svg": scene_to_svg(self.scene),
        }

    def export_svg_with_save_dialog(self) -> dict[str, Any]:
        payload = self.export_svg_payload()
        script = [
            "set outputFile to choose file name with prompt \"Export SVG as:\" default name "
            + json.dumps(payload["filename"]),
            "POSIX path of outputFile",
        ]
        try:
            completed = subprocess.run(
                ["osascript", *sum([["-e", line] for line in script], [])],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            return {"ok": False, "error": str(error)}
        if completed.returncode != 0:
            return {"ok": False, "cancelled": True}
        path = Path(completed.stdout.strip()).expanduser()
        try:
            path.write_text(payload["svg"], encoding="utf-8")
        except OSError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, "path": str(path)}

    def unique_scene_path(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        candidate = self.workspace / f"codex-draw-scene-{stamp}.json"
        index = 2
        while candidate.exists():
            candidate = self.workspace / f"codex-draw-scene-{stamp}-{index}.json"
            index += 1
        return candidate

    def new_scene(self) -> dict[str, Any]:
        self.save()
        self.scene_path = self.unique_scene_path()
        self.scene = default_scene()
        self.save()
        return {"scene": self.with_paths()}


def normalize_scene(scene: dict[str, Any]) -> dict[str, Any]:
    normalized = default_scene()
    if isinstance(scene, dict):
        normalized.update({key: copy.deepcopy(value) for key, value in scene.items() if key in normalized})
    canvas = normalized.get("canvas") if isinstance(normalized.get("canvas"), dict) else {}
    normalized["canvas"] = {
        "width": intish(canvas.get("width"), 1200, minimum=1),
        "height": intish(canvas.get("height"), 800, minimum=1),
        "background": str(canvas.get("background") or "#ffffff"),
    }
    normalized["objects"] = [normalize_object(item) for item in normalized.get("objects", []) if isinstance(item, dict)]
    normalized["objects"].sort(key=lambda item: item.get("zIndex", 0))
    for index, item in enumerate(normalized["objects"], start=1):
        item["zIndex"] = intish(item.get("zIndex"), index)
    object_ids = {item["id"] for item in normalized["objects"]}
    selection = normalized.get("selection", [])
    normalized["selection"] = [item for item in selection if isinstance(item, str) and item in object_ids]
    normalized["version"] = intish(normalized.get("version"), 1, minimum=1)
    normalized["revision"] = intish(normalized.get("revision"), 1, minimum=1)
    return normalized


def normalize_object(item: dict[str, Any]) -> dict[str, Any]:
    object_type = str(item.get("type") or "rect")
    if object_type not in {"rect", "ellipse", "line", "arrow", "text", "image"}:
        object_type = "rect"
    object_id = str(item.get("id") or "")
    if object_id and not ID_RE.match(object_id):
        object_id = ""
    normalized: dict[str, Any] = {
        "id": object_id,
        "type": object_type,
        "name": str(item.get("name") or object_id or object_type),
        "x": number(item.get("x"), 80),
        "y": number(item.get("y"), 80),
        "rotation": number(item.get("rotation"), 0),
        "fill": str(item.get("fill") if item.get("fill") is not None else "#ffffff"),
        "stroke": str(item.get("stroke") if item.get("stroke") is not None else "#1a1c1f"),
        "strokeWidth": number(item.get("strokeWidth"), 2, minimum=0),
        "opacity": number(item.get("opacity"), 1, minimum=0, maximum=1),
        "locked": bool(item.get("locked", False)),
        "visible": bool(item.get("visible", True)),
        "zIndex": intish(item.get("zIndex"), 1),
    }
    if object_type in {"line", "arrow"}:
        normalized.update(
            {
                "x2": number(item.get("x2"), normalized["x"] + 120),
                "y2": number(item.get("y2"), normalized["y"]),
                "fill": str(item.get("fill") or "none"),
            }
        )
    else:
        normalized.update(
            {
                "width": number(item.get("width"), 160, minimum=1),
                "height": number(item.get("height"), 100, minimum=1),
            }
        )
    if object_type == "ellipse" and "height" not in item:
        normalized["height"] = normalized["width"]
    if object_type == "text":
        normalized.update(
            {
                "text": str(item.get("text") or "Text"),
                "fontSize": number(item.get("fontSize"), 32, minimum=1),
                "fontFamily": str(item.get("fontFamily") or "Inter, system-ui, sans-serif"),
                "strokeWidth": number(item.get("strokeWidth"), 0, minimum=0),
            }
        )
    if object_type == "image":
        normalized["href"] = str(item.get("href") or "")
        normalized["preserveAspectRatio"] = str(item.get("preserveAspectRatio") or "xMidYMid meet")
    return normalized


def number(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if minimum is not None:
        parsed = max(float(minimum), parsed)
    if maximum is not None:
        parsed = min(float(maximum), parsed)
    return parsed


def intish(value: Any, default: int, *, minimum: int | None = None) -> int:
    parsed = int(round(number(value, default)))
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def next_z_index(objects: list[dict[str, Any]]) -> int:
    return max([intish(item.get("zIndex"), 0) for item in objects] or [0]) + 1


def scene_to_svg(scene: dict[str, Any]) -> str:
    canvas = scene["canvas"]
    width = intish(canvas.get("width"), 1200, minimum=1)
    height = intish(canvas.get("height"), 800, minimum=1)
    background = html.escape(str(canvas.get("background") or "#ffffff"), quote=True)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "  <defs>",
        '    <marker id="arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        '      <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/>',
        "    </marker>",
        "  </defs>",
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="{background}"/>',
    ]
    for item in sorted(scene["objects"], key=lambda obj: obj.get("zIndex", 0)):
        if item.get("visible") is False:
            continue
        lines.append(f"  {object_to_svg(item)}")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def object_to_svg(item: dict[str, Any]) -> str:
    attrs = base_svg_attrs(item)
    object_type = item["type"]
    if object_type == "rect":
        return f'<rect {attrs} x="{fmt(item["x"])}" y="{fmt(item["y"])}" width="{fmt(item["width"])}" height="{fmt(item["height"])}"/>'
    if object_type == "ellipse":
        cx = item["x"] + item["width"] / 2
        cy = item["y"] + item["height"] / 2
        return f'<ellipse {attrs} cx="{fmt(cx)}" cy="{fmt(cy)}" rx="{fmt(item["width"] / 2)}" ry="{fmt(item["height"] / 2)}"/>'
    if object_type in {"line", "arrow"}:
        marker = ' marker-end="url(#arrowhead)"' if object_type == "arrow" else ""
        return f'<line {attrs} x1="{fmt(item["x"])}" y1="{fmt(item["y"])}" x2="{fmt(item["x2"])}" y2="{fmt(item["y2"])}" stroke-linecap="round"{marker}/>'
    if object_type == "text":
        text = html.escape(str(item.get("text") or ""), quote=False)
        family = html.escape(str(item.get("fontFamily") or "Inter, system-ui, sans-serif"), quote=True)
        return f'<text {attrs} x="{fmt(item["x"])}" y="{fmt(item["y"])}" font-size="{fmt(item["fontSize"])}" font-family="{family}">{text}</text>'
    if object_type == "image":
        href = html.escape(str(item.get("href") or ""), quote=True)
        par = html.escape(str(item.get("preserveAspectRatio") or "xMidYMid meet"), quote=True)
        return f'<image {attrs} x="{fmt(item["x"])}" y="{fmt(item["y"])}" width="{fmt(item["width"])}" height="{fmt(item["height"])}" href="{href}" preserveAspectRatio="{par}"/>'
    return ""


def base_svg_attrs(item: dict[str, Any]) -> str:
    attrs = {
        "id": item.get("id"),
        "fill": item.get("fill"),
        "stroke": item.get("stroke"),
        "stroke-width": item.get("strokeWidth"),
        "opacity": item.get("opacity"),
    }
    if item.get("rotation"):
        center_x, center_y = svg_object_center(item)
        attrs["transform"] = f'rotate({fmt(item["rotation"])} {fmt(center_x)} {fmt(center_y)})'
    return " ".join(f'{key}="{html.escape(fmt(value), quote=True)}"' for key, value in attrs.items() if value is not None)


def svg_object_center(item: dict[str, Any]) -> tuple[float, float]:
    if item["type"] in {"line", "arrow"}:
        return ((item["x"] + item["x2"]) / 2, (item["y"] + item["y2"]) / 2)
    if item["type"] == "text":
        text_width = max(40, len(str(item.get("text") or "")) * item.get("fontSize", 24) * 0.55)
        text_height = item.get("fontSize", 24) * 1.3
        return (item["x"] + text_width / 2, item["y"] - text_height / 2)
    return (item["x"] + item.get("width", 1) / 2, item["y"] + item.get("height", 1) / 2)


def fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class DrawRequestHandler(BaseHTTPRequestHandler):
    server: "DrawServer"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.serve_file(APP_DIR / "index.html")
        elif parsed.path == "/static/codex-draw-icon.png":
            self.serve_file(SKILL_DIR / "assets" / "codex-draw-icon.png")
        elif parsed.path.startswith("/static/"):
            self.serve_file(APP_DIR / unquote(parsed.path.removeprefix("/static/")))
        elif parsed.path.startswith("/exports/"):
            self.serve_workspace_file(self.server.store.exports_dir, unquote(parsed.path.removeprefix("/exports/")))
        elif parsed.path.startswith("/assets/"):
            self.serve_workspace_file(self.server.store.assets_dir, unquote(parsed.path.removeprefix("/assets/")))
        elif parsed.path == "/api/scene":
            self.send_json(self.server.store.with_paths())
        elif parsed.path == "/api/selection":
            self.send_json(self.server.store.selection_payload())
        elif parsed.path == "/api/meta":
            self.send_json({"url": self.server.base_url, "paths": self.server.store.with_paths()["paths"]})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        self.handle_mutation("POST")

    def do_PATCH(self) -> None:
        self.handle_mutation("PATCH")

    def do_DELETE(self) -> None:
        self.handle_mutation("DELETE")

    def handle_mutation(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json() if method in {"POST", "PATCH"} else {}
            if method == "POST" and path == "/api/scene":
                self.send_json(self.server.store.replace(payload))
            elif method == "POST" and path == "/api/objects":
                self.send_json(self.server.store.add_object(payload))
            elif method == "PATCH" and path.startswith("/api/objects/"):
                self.send_json(self.server.store.patch_object(unquote(path.removeprefix("/api/objects/")), payload))
            elif method == "DELETE" and path.startswith("/api/objects/"):
                self.send_json(self.server.store.delete_object(unquote(path.removeprefix("/api/objects/"))))
            elif method == "POST" and path == "/api/selection":
                selection = payload.get("selection", [])
                if not isinstance(selection, list):
                    raise ValueError("selection must be a list")
                self.send_json(self.server.store.set_selection([str(item) for item in selection]))
            elif method == "POST" and path == "/api/objects/reorder":
                order = payload.get("order", [])
                if not isinstance(order, list):
                    raise ValueError("order must be a list")
                self.send_json(self.server.store.reorder([str(item) for item in order]))
            elif method == "POST" and path == "/api/save":
                self.server.store.bump()
                self.server.store.save()
                self.send_json({"path": str(self.server.store.scene_path), "scene": self.server.store.with_paths()})
            elif method == "POST" and path == "/api/new":
                self.send_json(self.server.store.new_scene())
            elif method == "POST" and path == "/api/export/svg-content":
                self.send_json(self.server.store.export_svg_payload())
            elif method == "POST" and path == "/api/export/svg-save-as":
                self.send_json(self.server.store.export_svg_with_save_dialog())
            elif method == "POST" and path == "/api/export/svg":
                export_path = self.server.store.export_svg()
                self.send_json({"path": str(export_path), "url": f"/exports/{export_path.name}"})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except KeyError as error:
            self.send_json({"error": f"object not found: {error.args[0]}"}, HTTPStatus.NOT_FOUND)
        except (json.JSONDecodeError, ValueError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        data = self.rfile.read(length).decode("utf-8")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")
        return payload

    def serve_file(self, path: Path) -> None:
        root = APP_DIR.resolve()
        requested = path.resolve()
        if root not in requested.parents and requested != root:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not requested.exists() or not requested.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        data = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_workspace_file(self, root: Path, relative_path: str) -> None:
        requested = (root / relative_path).resolve()
        root = root.resolve()
        if root not in requested.parents and requested != root:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not requested.exists() or not requested.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        data = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[codex-draw] " + fmt % args + "\n")


class DrawServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type[DrawRequestHandler], store: SceneStore) -> None:
        super().__init__(address, handler)
        self.store = store
        host, port = self.server_address
        self.base_url = f"http://{host}:{port}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local Codex Draw workspace.")
    parser.add_argument("--workspace", default="drawings", help="Workspace folder for scene, exports, and assets.")
    parser.add_argument("--scene", default=None, help="Optional scene JSON filename inside the workspace.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port. Use 0 for an available random port.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = SceneStore(Path(args.workspace), args.scene)
    server = DrawServer((args.host, args.port), DrawRequestHandler, store)
    print(f"Codex Draw: {server.base_url}", flush=True)
    print(f"Scene: {store.scene_path}", flush=True)
    print(f"Exports: {store.exports_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Codex Draw.", flush=True)


if __name__ == "__main__":
    main()
