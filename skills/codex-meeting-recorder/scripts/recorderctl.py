#!/usr/bin/env python3
"""Codex-facing controller for the Codex Meeting Recorder skill."""

from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
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
REALTIME_TRANSCRIPTION_CLI = SKILL_DIR / "scripts" / "realtime_transcription.py"
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
        "transcript_size_bytes": file_size(state.get("transcript_file")),
    }


def cleanup_stale_state(workspace: Path, state: dict[str, Any]) -> None:
    status_pid = state.get("status_pid")
    if status_pid and is_running(int(status_pid)):
        try:
            os.kill(int(status_pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    clear_state(workspace)


def format_transcript_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    paragraphs: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        words = len(sentence.split())
        if current and current_words + words > 90:
            paragraphs.append(" ".join(current))
            current = []
            current_words = 0
        current.append(sentence)
        current_words += words
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs) + "\n"


def write_formatted_transcript(recording_dir: Path, live_transcript: Path) -> Path:
    formatted_path = recording_dir / "formatted_transcript.md"
    if live_transcript.exists():
        formatted_path.write_text(format_transcript_text(live_transcript.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    else:
        formatted_path.write_text("", encoding="utf-8")
    return formatted_path


def render_status_html(payload: dict[str, Any]) -> str:
    active = bool(payload.get("active"))
    transcript = ""
    transcript_file = payload.get("transcript_file")
    if transcript_file:
        path = Path(str(transcript_file))
        if path.exists():
            transcript = path.read_text(encoding="utf-8", errors="replace")
    initial_payload = html.escape(json.dumps(payload), quote=False)
    initial_transcript = html.escape(transcript.rstrip("\r\n"))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Transcript</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      background: #fff;
      color: #111;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: #fff;
    }}
    main {{
      box-sizing: border-box;
      width: min(900px, 100%);
      padding: 32px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 16px;
      line-height: 1.6;
    }}
    #cursor {{
      display: { "inline-block" if active else "none" };
      width: 1ch;
      margin-left: 1px;
      border-bottom: 2px solid #111;
      animation: blink 1s steps(2, start) infinite;
      transform: translateY(.12em);
    }}
    @keyframes blink {{
      50% {{ opacity: 0; }}
    }}
  </style>
</head>
<body>
  <main><span id="text">{initial_transcript}</span><span id="cursor"></span></main>
  <script id="initial-payload" type="application/json">{initial_payload}</script>
  <script>
    const initial = JSON.parse(document.getElementById("initial-payload").textContent);
    const els = {{
      text: document.getElementById("text"),
      cursor: document.getElementById("cursor")
    }};

    async function poll() {{
      try {{
        const [statusResponse, transcriptResponse] = await Promise.all([
          fetch("/status", {{ cache: "no-store" }}),
          fetch("/transcript", {{ cache: "no-store" }})
        ]);
        if (transcriptResponse.ok) {{
          els.text.textContent = (await transcriptResponse.text()).replace(/[\\r\\n]+$/g, "");
          window.scrollTo(0, document.body.scrollHeight);
        }}
        if (statusResponse.ok) {{
          const payload = await statusResponse.json();
          els.cursor.style.display = payload.active ? "inline-block" : "none";
        }}
      }} catch (error) {{
      }}
    }}

    els.cursor.style.display = initial.active ? "inline-block" : "none";
    setInterval(poll, 1000);
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
    if state.get("recording_file"):
        recording_file = Path(state["recording_file"])
        if not recording_file.exists() or recording_file.stat().st_size == 0:
            log_path = Path(state["log_file"])
            log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
            raise SystemExit(f"Recording file was not created correctly. Log:\n{log_text}")
        state["recording_size_bytes"] = recording_file.stat().st_size
    if state.get("transcript_file"):
        transcript_file = Path(state["transcript_file"])
        state["transcript_size_bytes"] = transcript_file.stat().st_size if transcript_file.exists() else 0
        formatted_path = write_formatted_transcript(Path(state["recording_dir"]), transcript_file)
        state["formatted_transcript_file"] = str(formatted_path)
        state["formatted_transcript_size_bytes"] = formatted_path.stat().st_size

    metadata_path = Path(state["recording_dir"]) / "metadata.json"
    state["stopped_at"] = datetime.now().isoformat(timespec="seconds")
    metadata_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    if stop_status_server:
        status_pid = state.get("status_pid")
        if status_pid and int(status_pid) != os.getpid() and is_running(int(status_pid)):
            os.kill(int(status_pid), signal.SIGTERM)

    if transcribe_after and state.get("recording_file"):
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


def realtime_worker_command(args: argparse.Namespace, out_dir: Path) -> list[str]:
    if not REALTIME_TRANSCRIPTION_CLI.exists():
        raise SystemExit(f"Missing realtime transcription helper: {REALTIME_TRANSCRIPTION_CLI}")

    runner = [sys.executable, "-u"]
    try:
        subprocess.run(
            [sys.executable, "-c", "import websocket"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError:
        uv = shutil.which("uv")
        if not uv:
            raise SystemExit("websocket-client is not installed and `uv` was not found for automatic dependency isolation.")
        runner = [uv, "run", "--with", "websocket-client", "python", "-u"]

    command = [
        *runner,
        str(REALTIME_TRANSCRIPTION_CLI),
        "--helper-bin",
        str(HELPER_BIN),
        "--transcript",
        str(out_dir / "live_transcript.txt"),
        "--audio-log",
        str(out_dir / "audio-capture.log"),
        "--backend",
        args.backend,
        "--model",
        args.model,
        "--language",
        args.language,
        "--delay",
        args.delay,
        "--commit-interval",
        str(args.commit_interval),
        "--audio-chunk-ms",
        str(args.audio_chunk_ms),
        "--silence-threshold",
        str(args.silence_threshold),
        "--peak-threshold",
        str(args.peak_threshold),
        "--trailing-silence-chunks",
        str(args.trailing_silence_chunks),
    ]
    if not args.system_audio:
        command.append("--no-system-audio")
    if not args.microphone:
        command.append("--no-microphone")
    if args.save_raw_audio:
        command.extend(["--raw-audio", str(out_dir / "input_audio.pcm")])
    if args.save_events:
        command.extend(["--events", str(out_dir / "transcript_events.jsonl")])
    return command


def start(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    existing = load_state(workspace)
    if existing:
        if is_running(int(existing["pid"])):
            raise SystemExit(f"Recording already running: pid={existing['pid']} dir={existing['recording_dir']}")
        cleanup_stale_state(workspace, existing)

    build_helper()

    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = recording_root(workspace) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_file = out_dir / "live_transcript.txt"
    log_file = out_dir / "recorder.log"
    metadata_file = out_dir / "metadata.json"

    command = realtime_worker_command(args, out_dir)

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
        "mode": "realtime",
        "recording_file": None,
        "transcript_file": str(transcript_file),
        "formatted_transcript_file": str(out_dir / "formatted_transcript.md"),
        "log_file": str(log_file),
        "audio_log_file": str(out_dir / "audio-capture.log"),
        "backend": args.backend,
        "model": args.model,
        "delay": args.delay,
        "language": args.language,
        "system_audio": bool(args.system_audio),
        "microphone": bool(args.microphone),
    }
    if args.save_raw_audio:
        state["raw_audio_file"] = str(out_dir / "input_audio.pcm")
    if args.save_events:
        state["transcript_events_file"] = str(out_dir / "transcript_events.jsonl")
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
        cleanup_stale_state(workspace, state)
        log_text = log_file.read_text(errors="replace") if log_file.exists() else ""
        raise SystemExit(f"Recorder exited during startup. Log:\n{log_text}")
    if log_file.exists():
        log_text = log_file.read_text(errors="replace")
        if "audio_capture_exited" in log_text or "Error:" in log_text:
            cleanup_stale_state(workspace, state)
            raise SystemExit(f"Recorder startup failed. Log:\n{log_text}")

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
            if self.path == "/transcript":
                transcript_file = payload.get("transcript_file")
                if transcript_file and Path(str(transcript_file)).exists():
                    self._send(200, "text/plain; charset=utf-8", Path(str(transcript_file)).read_text(encoding="utf-8", errors="replace"))
                else:
                    self._send(200, "text/plain; charset=utf-8", "")
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
    start_parser.add_argument("--backend", choices=["openai-realtime-whisper", "local-nemotron"], default="openai-realtime-whisper")
    start_parser.add_argument("--model", default="gpt-realtime-whisper")
    start_parser.add_argument("--language", default="en")
    start_parser.add_argument("--delay", choices=["minimal", "low", "medium", "high", "xhigh"], default="low")
    start_parser.add_argument("--commit-interval", type=float, default=3.0)
    start_parser.add_argument("--audio-chunk-ms", type=int, default=100)
    start_parser.add_argument("--silence-threshold", type=float, default=20.0)
    start_parser.add_argument("--peak-threshold", type=float, default=250.0)
    start_parser.add_argument("--trailing-silence-chunks", type=int, default=5)
    start_parser.add_argument("--save-events", action="store_true", help="Debug only: save raw Realtime events as transcript_events.jsonl")
    start_parser.add_argument("--save-raw-audio", action="store_true", help="Debug only: save streamed PCM audio as input_audio.pcm")
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
