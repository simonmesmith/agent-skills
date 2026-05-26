#!/usr/bin/env python3
"""Codex-facing controller for the Codex Meeting Recorder skill."""

from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
HELPER_DIR = SKILL_DIR / "helper"
HELPER_BIN = HELPER_DIR / ".build" / "release" / "codex-meeting-recorder"
TRANSCRIPTION_CLI = SKILL_DIR / "scripts" / "transcription.py"
DEFAULT_STATUS_PORT = 47832


def recording_root(workspace: Path) -> Path:
    return workspace / "recordings"


def state_path(workspace: Path) -> Path:
    return recording_root(workspace) / ".current-recording.json"


def load_state(workspace: Path) -> dict[str, Any] | None:
    path = state_path(workspace)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_state(workspace: Path, state: dict[str, Any]) -> None:
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def clear_state(workspace: Path) -> None:
    path = state_path(workspace)
    if path.exists():
        path.unlink()


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def build_helper() -> None:
    if HELPER_BIN.exists():
        return
    subprocess.run(
        ["swift", "build", "-c", "release"],
        cwd=HELPER_DIR,
        check=True,
    )


def latest_recording_dir(workspace: Path) -> Path:
    root = recording_root(workspace)
    candidates = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
    candidates = [p for p in candidates if (p / "recording.mp4").exists()]
    if not candidates:
        raise SystemExit("No recording directories found.")
    return sorted(candidates)[-1]


def find_status_port(preferred: int = DEFAULT_STATUS_PORT) -> int:
    for port in [preferred, 0]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    raise RuntimeError("Could not allocate a localhost status port.")


def file_size(path: str | None) -> int:
    if not path:
        return 0
    target = Path(path)
    return target.stat().st_size if target.exists() else 0


def elapsed_seconds(started_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    return max(0, int((datetime.now() - started).total_seconds()))


def status_payload(workspace: Path) -> dict[str, Any]:
    state = load_state(workspace)
    if not state:
        return {
            "active": False,
            "workspace": str(workspace),
            "message": "No active recording.",
        }
    running = is_running(int(state["pid"]))
    return {
        **state,
        "active": running,
        "running": running,
        "elapsed_seconds": elapsed_seconds(state.get("started_at")),
        "recording_size_bytes": file_size(state.get("recording_file")),
    }


def human_size(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def render_status_html(payload: dict[str, Any]) -> str:
    active = bool(payload.get("active"))
    elapsed = int(payload.get("elapsed_seconds", 0))
    minutes, seconds = divmod(elapsed, 60)
    hours, minutes = divmod(minutes, 60)
    timer = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    recording_file = html.escape(str(payload.get("recording_file", "")))
    recording_dir = html.escape(str(payload.get("recording_dir", "")))
    size = human_size(int(payload.get("recording_size_bytes", 0)))
    status_text = "Recording" if active else html.escape(str(payload.get("message", "Stopped")))
    system_audio = "on" if payload.get("system_audio") else "off"
    microphone = "on" if payload.get("microphone") else "off"
    dot_class = "dot live" if active else "dot"
    stop_disabled = "" if active else "disabled"
    initial_payload = html.escape(json.dumps(payload), quote=False)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex Meeting Recorder</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #111318;
      color: #f4f6fb;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: radial-gradient(circle at 50% 0%, #242936, #111318 54%);
    }}
    main {{
      width: min(560px, calc(100vw - 32px));
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 8px;
      padding: 22px;
      background: rgba(20, 23, 31, .92);
      box-shadow: 0 24px 80px rgba(0,0,0,.32);
    }}
    .header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .state {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 14px;
      color: #cbd2df;
    }}
    .dot {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #6f7684;
    }}
    .dot.live {{
      background: #ff3b30;
      box-shadow: 0 0 0 0 rgba(255,59,48,.58);
      animation: pulse 1.3s infinite;
    }}
    @keyframes pulse {{
      70% {{ box-shadow: 0 0 0 14px rgba(255,59,48,0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(255,59,48,0); }}
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }}
    .timer {{
      margin: 24px 0 18px;
      font-variant-numeric: tabular-nums;
      font-size: clamp(42px, 12vw, 72px);
      line-height: 1;
      font-weight: 700;
    }}
    dl {{
      display: grid;
      grid-template-columns: 130px 1fr;
      gap: 10px 14px;
      margin: 0 0 22px;
      font-size: 13px;
    }}
    dt {{ color: #8f98aa; }}
    dd {{
      margin: 0;
      min-width: 0;
      overflow-wrap: anywhere;
      color: #eef2f8;
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    button {{
      appearance: none;
      border: 0;
      border-radius: 7px;
      padding: 10px 14px;
      background: #ff453a;
      color: white;
      font-weight: 650;
      cursor: pointer;
    }}
    button:disabled {{
      cursor: default;
      background: #454b57;
      color: #aeb5c2;
    }}
    .hint {{
      font-size: 12px;
      color: #8f98aa;
    }}
  </style>
</head>
<body>
  <main>
    <div class="header">
      <h1>Codex Meeting Recorder</h1>
      <div class="state"><span id="dot" class="{dot_class}"></span><span id="status-text">{status_text}</span></div>
    </div>
    <div id="timer" class="timer">{timer}</div>
    <dl>
      <dt>System audio</dt><dd id="system-audio">{system_audio}</dd>
      <dt>Microphone</dt><dd id="microphone">{microphone}</dd>
      <dt>File size</dt><dd id="file-size">{size}</dd>
      <dt>Folder</dt><dd id="recording-dir">{recording_dir}</dd>
      <dt>Recording</dt><dd id="recording-file">{recording_file}</dd>
    </dl>
    <div class="actions">
      <button id="stop" {stop_disabled}>Stop Recording</button>
      <span id="hint" class="hint">Updates every second</span>
    </div>
  </main>
  <script id="initial-payload" type="application/json">{initial_payload}</script>
  <script>
    const initial = JSON.parse(document.getElementById("initial-payload").textContent);
    const els = {{
      dot: document.getElementById("dot"),
      statusText: document.getElementById("status-text"),
      timer: document.getElementById("timer"),
      systemAudio: document.getElementById("system-audio"),
      microphone: document.getElementById("microphone"),
      fileSize: document.getElementById("file-size"),
      recordingDir: document.getElementById("recording-dir"),
      recordingFile: document.getElementById("recording-file"),
      stop: document.getElementById("stop"),
      hint: document.getElementById("hint")
    }};

    function formatTimer(totalSeconds) {{
      const total = Math.max(0, Number(totalSeconds || 0));
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const seconds = total % 60;
      return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
    }}

    function formatBytes(bytes) {{
      let value = Number(bytes || 0);
      for (const unit of ["B", "KB", "MB", "GB"]) {{
        if (value < 1024 || unit === "GB") {{
          return unit === "B" ? `${{Math.round(value)}} B` : `${{value.toFixed(1)}} ${{unit}}`;
        }}
        value = value / 1024;
      }}
      return "0 B";
    }}

    function render(payload) {{
      const active = Boolean(payload.active);
      els.dot.className = active ? "dot live" : "dot";
      els.statusText.textContent = active ? "Recording" : (payload.message || "Stopped");
      els.timer.textContent = formatTimer(payload.elapsed_seconds);
      els.systemAudio.textContent = payload.system_audio ? "on" : "off";
      els.microphone.textContent = payload.microphone ? "on" : "off";
      els.fileSize.textContent = formatBytes(payload.recording_size_bytes);
      els.recordingDir.textContent = payload.recording_dir || "";
      els.recordingFile.textContent = payload.recording_file || "";
      els.stop.disabled = !active;
      els.hint.textContent = active ? "Updates every second" : "Stopped";
    }}

    async function poll() {{
      try {{
        const response = await fetch("/status", {{ cache: "no-store" }});
        if (response.ok) {{
          render(await response.json());
        }}
      }} catch (error) {{
        els.hint.textContent = "Waiting for recorder status...";
      }}
    }}

    render(initial);
    setInterval(poll, 1000);
    document.getElementById("stop").addEventListener("click", async () => {{
      const button = document.getElementById("stop");
      button.disabled = true;
      button.textContent = "Stopping...";
      await fetch("/stop", {{ method: "POST" }});
      button.textContent = "Stop Recording";
      await poll();
    }});
  </script>
</body>
</html>"""


def stop_active_recording(workspace: Path, timeout: float, *, transcribe_after: bool, stop_status_server: bool) -> dict[str, Any]:
    state = load_state(workspace)
    if not state:
        raise SystemExit("No active recording state found.")

    pid = int(state["pid"])
    if is_running(pid):
        os.kill(pid, signal.SIGINT)
        deadline = time.time() + timeout
        while time.time() < deadline and is_running(pid):
            time.sleep(0.25)
        if is_running(pid):
            raise SystemExit(f"Recorder pid {pid} did not stop within {timeout} seconds.")

    clear_state(workspace)
    recording_file = Path(state["recording_file"])
    if not recording_file.exists() or recording_file.stat().st_size == 0:
        log_path = Path(state["log_file"])
        log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
        raise SystemExit(f"Recording file was not created correctly. Log:\n{log_text}")

    metadata_path = Path(state["recording_dir"]) / "metadata.json"
    state["stopped_at"] = datetime.now().isoformat(timespec="seconds")
    state["recording_size_bytes"] = recording_file.stat().st_size
    metadata_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    if stop_status_server:
        status_pid = state.get("status_pid")
        if status_pid and int(status_pid) != os.getpid() and is_running(int(status_pid)):
            os.kill(int(status_pid), signal.SIGTERM)

    if transcribe_after:
        transcribe_recording(workspace, Path(state["recording_dir"]))

    return state


def start_status_server_process(workspace: Path, out_dir: Path) -> tuple[int, str]:
    port = find_status_port()
    log_file = out_dir / "status-server.log"
    log_handle = log_file.open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "serve-status",
            "--workspace",
            str(workspace),
            "--port",
            str(port),
        ],
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )
    time.sleep(0.4)
    if not is_running(process.pid):
        log_text = log_file.read_text(errors="replace") if log_file.exists() else ""
        raise RuntimeError(f"Status server did not start. Log:\n{log_text}")
    return process.pid, f"http://127.0.0.1:{port}"


def start(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    existing = load_state(workspace)
    if existing and is_running(int(existing["pid"])):
        raise SystemExit(f"Recording already running: pid={existing['pid']} dir={existing['recording_dir']}")

    build_helper()

    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = recording_root(workspace) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    recording_file = out_dir / "recording.mp4"
    log_file = out_dir / "recorder.log"
    metadata_file = out_dir / "metadata.json"

    command = [
        str(HELPER_BIN),
        "record",
        "--out",
        str(recording_file),
    ]
    if not args.system_audio:
        command.append("--no-system-audio")
    if not args.microphone:
        command.append("--no-microphone")

    log_handle = log_file.open("ab")
    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )

    state = {
        "pid": process.pid,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "workspace": str(workspace),
        "recording_dir": str(out_dir),
        "recording_file": str(recording_file),
        "log_file": str(log_file),
        "system_audio": bool(args.system_audio),
        "microphone": bool(args.microphone),
    }
    save_state(workspace, state)

    try:
        status_pid, status_url = start_status_server_process(workspace, out_dir)
        state["status_pid"] = status_pid
        state["status_url"] = status_url
    except RuntimeError as exc:
        state["status_error"] = str(exc)
    save_state(workspace, state)
    metadata_file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    time.sleep(1.5)
    if not is_running(process.pid):
        clear_state(workspace)
        log_text = log_file.read_text(errors="replace") if log_file.exists() else ""
        raise SystemExit(f"Recorder exited during startup. Log:\n{log_text}")

    print(json.dumps(state, indent=2, sort_keys=True))


def stop(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    state = stop_active_recording(
        workspace,
        args.timeout,
        transcribe_after=args.transcribe,
        stop_status_server=True,
    )
    print(json.dumps(state, indent=2, sort_keys=True))


def status(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    state = load_state(workspace)
    if not state:
        print("No active recording.")
        return
    state = status_payload(workspace)
    print(json.dumps(state, indent=2, sort_keys=True))


def serve_status(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, status: int, content_type: str, body: str | bytes) -> None:
            payload = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            payload = status_payload(workspace)
            if self.path == "/status":
                self._send(200, "application/json", json.dumps(payload, indent=2, sort_keys=True))
                return
            self._send(200, "text/html; charset=utf-8", render_status_html(payload))

        def do_POST(self) -> None:
            if self.path != "/stop":
                self._send(404, "text/plain; charset=utf-8", "Not found")
                return
            try:
                result = stop_active_recording(
                    workspace,
                    args.timeout,
                    transcribe_after=False,
                    stop_status_server=False,
                )
                self._send(200, "application/json", json.dumps(result, indent=2, sort_keys=True))
                threading.Timer(2.0, self.server.shutdown).start()
            except SystemExit as exc:
                self._send(409, "text/plain; charset=utf-8", str(exc))

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def transcribe_recording(workspace: Path, recording_dir: Path | None = None) -> None:
    if not TRANSCRIPTION_CLI.exists():
        raise SystemExit(f"Missing bundled transcription helper: {TRANSCRIPTION_CLI}")

    target_dir = recording_dir or latest_recording_dir(workspace)
    recording_file = target_dir / "recording.mp4"
    transcript_file = target_dir / "transcript.txt"
    if not recording_file.exists():
        raise SystemExit(f"Missing recording file: {recording_file}")

    command = [
        sys.executable,
        str(TRANSCRIPTION_CLI),
        str(recording_file),
        "--out",
        str(transcript_file),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError:
        uv = shutil.which("uv")
        if not uv:
            raise
        subprocess.run(
            [
                uv,
                "run",
                "--with",
                "openai",
                "python",
                str(TRANSCRIPTION_CLI),
                str(recording_file),
                "--out",
                str(transcript_file),
            ],
            check=True,
        )
    print(str(transcript_file))


def transcribe(args: argparse.Namespace) -> None:
    transcribe_recording(args.workspace.resolve(), args.recording_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Control Codex Meeting Recorder recordings.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    start_parser.add_argument("--system-audio", action=argparse.BooleanOptionalAction, default=True)
    start_parser.add_argument("--microphone", action=argparse.BooleanOptionalAction, default=True)
    start_parser.set_defaults(func=start)

    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    stop_parser.add_argument("--timeout", type=float, default=15.0)
    stop_parser.add_argument("--transcribe", action="store_true")
    stop_parser.set_defaults(func=stop)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    status_parser.set_defaults(func=status)

    serve_parser = subparsers.add_parser("serve-status")
    serve_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    serve_parser.add_argument("--port", type=int, default=DEFAULT_STATUS_PORT)
    serve_parser.add_argument("--timeout", type=float, default=15.0)
    serve_parser.set_defaults(func=serve_status)

    transcribe_parser = subparsers.add_parser("transcribe")
    transcribe_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    transcribe_parser.add_argument("--recording-dir", type=Path)
    transcribe_parser.set_defaults(func=transcribe)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
