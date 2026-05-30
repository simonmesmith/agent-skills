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
from urllib.parse import parse_qs, unquote, urlparse


SKILL_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = SKILL_DIR / "assets"
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
    swift_sources = list((HELPER_DIR / "Sources").rglob("*.swift"))
    if HELPER_BIN.exists() and all(source.stat().st_mtime <= HELPER_BIN.stat().st_mtime for source in swift_sources):
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


def transcript_source_counts(text: str) -> dict[str, int]:
    counts = {"Microphone": 0, "System": 0, "Unlabeled": 0}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^\[(Microphone|System)\]\s+", stripped)
        if match:
            counts[match.group(1)] += 1
        else:
            counts["Unlabeled"] += 1
    return counts


def markdown_bullet(value: Any) -> str:
    if value in (None, ""):
        return "Unknown"
    return str(value).replace("\n", " ").strip()


def build_transcript_formatting_prompt(recording_dir: Path, live_transcript: Path, state: dict[str, Any] | None = None) -> str:
    metadata = state or {}
    raw_text = live_transcript.read_text(encoding="utf-8", errors="replace") if live_transcript.exists() else ""
    counts = transcript_source_counts(raw_text)
    workspace = Path(str(metadata.get("workspace") or recording_dir.parent.parent)).resolve()
    workspace_files: list[str] = []
    if workspace.exists():
        for path in sorted(workspace.rglob("*")):
            if path.is_file() and ".git" not in path.parts:
                try:
                    workspace_files.append(str(path.relative_to(workspace)))
                except ValueError:
                    workspace_files.append(str(path))
                if len(workspace_files) >= 50:
                    break

    workspace_file_list = "\n".join(f"- `{path}`" for path in workspace_files) or "- None found"
    source_note = (
        "Treat `[Microphone]` as `You` and `[System]` as `Others`. "
        "These are deterministic audio-source labels, not true speaker diarization."
    )
    attendee_note = (
        "Do not assume that every named person was on the call. Transcript-only attendee inference is unreliable "
        "because people may discuss absent colleagues, clients, partners, or future invitees. If attendees are not "
        "provided by the user or meeting metadata, separate confirmed participants from possible participants and "
        "people merely mentioned."
    )
    return f"""# Transcript Formatting Subagent Task

Create a polished final Markdown transcript at:

`{recording_dir / "formatted_transcript.md"}`

## Required Output

Use this structure:

1. `# <meeting title>`
2. `## Description` with a short summary that makes the meeting easy to recognize later.
3. `## Participants and Mentioned People` with confirmed participants, possible participants, and people merely mentioned. Mark every non-confirmed item clearly.
4. `## Source and Quality Notes` with source labels, transcript quality, and any capture warnings.
5. `## Formatted Transcript` with readable Markdown paragraphs. Use `**You:**` for `[Microphone]` and `**Others:**` for `[System]`.
6. `## Assumptions and Corrections` listing spelling fixes, vocabulary guesses, speaker/attendee uncertainty, and any other formatting assumptions.

## Formatting Rules

- Preserve the meaning of the source transcript. Do not invent substantive facts.
- Combine tiny chunks into logical sentences and paragraphs.
- Add capitalization and punctuation where appropriate.
- Use headings, bold labels, and short paragraphs to make the transcript easy to read.
- Fix likely spellings using meeting context and workspace context when available.
- Document every meaningful correction or inference in the assumptions section.
- Keep raw transcript artifacts unchanged for audit.
- {source_note}
- {attendee_note}

## Recording Metadata

- Recording directory: `{recording_dir}`
- Workspace: `{workspace}`
- Started at: {markdown_bullet(metadata.get("started_at"))}
- Stopped at: {markdown_bullet(metadata.get("stopped_at"))}
- Model: {markdown_bullet(metadata.get("model"))}
- Backend: {markdown_bullet(metadata.get("backend"))}
- Delay: {markdown_bullet(metadata.get("delay"))}
- Language: {markdown_bullet(metadata.get("language"))}
- Source overlap policy: {markdown_bullet(metadata.get("source_overlap_policy"))}
- Microphone chunks: {counts["Microphone"]}
- System chunks: {counts["System"]}
- Unlabeled chunks: {counts["Unlabeled"]}
- Audio health warnings: {markdown_bullet("; ".join(metadata.get("audio_health_check", {}).get("warnings", [])))}

## Workspace Files Available For Context

{workspace_file_list}

## Raw Source Transcript

```text
{raw_text.rstrip()}
```
"""


def write_formatting_prompt(recording_dir: Path, live_transcript: Path, state: dict[str, Any] | None = None) -> Path:
    prompt_path = recording_dir / "transcript_formatting_prompt.md"
    prompt_path.write_text(build_transcript_formatting_prompt(recording_dir, live_transcript, state), encoding="utf-8")
    return prompt_path


def write_formatted_transcript_placeholder(recording_dir: Path, prompt_path: Path) -> Path:
    formatted_path = recording_dir / "formatted_transcript.md"
    formatted_path.write_text(
        "# Transcript Formatting Pending\n\n"
        "This file is reserved for the post-meeting formatted transcript.\n\n"
        "Launch a Codex subagent with the task in "
        f"`{prompt_path.name}` and have it replace this placeholder with the final Markdown transcript.\n\n"
        "Raw audit artifacts remain available in `live_transcript.txt` and `metadata.json`.\n",
        encoding="utf-8",
    )
    return formatted_path


def source_icon_html(source: str) -> str:
    icon = "microphone-solid-full.svg" if source == "Microphone" else "volume-high-solid-full.svg"
    label = "Microphone" if source == "Microphone" else "System audio"
    return (
        f'<span class="source-badge source-{source.lower()}" title="{label}" aria-label="{label}">'
        f'<span class="source-icon" style="--source-icon-url: url(&quot;/assets/{icon}&quot;)" aria-hidden="true"></span>'
        "</span>"
    )


def render_transcript_preview_html(text: str) -> str:
    escaped = html.escape(text.rstrip("\r\n"))
    return re.sub(
        r"^\[(Microphone|System)\]\s*",
        lambda match: source_icon_html(match.group(1)),
        escaped,
        flags=re.MULTILINE,
    )


def theme_style_from_path(path: str) -> str:
    params = parse_qs(urlparse(path).query)
    css_names = {
        "accent": ["--recorder-accent"],
        "surface": ["--recorder-page-surface", "--recorder-badge-surface"],
        "ink": ["--recorder-ink"],
    }
    declarations = []
    for param, css_name_list in css_names.items():
        value = unquote(params.get(param, [""])[0]).strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            declarations.extend(f"{css_name}: {value.lower()};" for css_name in css_name_list)
    return " ".join(declarations)


def render_status_html(payload: dict[str, Any], *, theme_style: str = "") -> str:
    active = bool(payload.get("active"))
    transcript = ""
    transcript_file = payload.get("transcript_file")
    if transcript_file:
        path = Path(str(transcript_file))
        if path.exists():
            transcript = path.read_text(encoding="utf-8", errors="replace")
    initial_payload = html.escape(json.dumps(payload), quote=False)
    initial_transcript = render_transcript_preview_html(transcript)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Transcript</title>
  <style>
    :root {{
      color-scheme: light dark;
      --recorder-accent: var(--codex-base-accent, #339cff);
      --recorder-page-surface: var(--codex-base-surface, #ffffff);
      --recorder-badge-surface: var(--codex-base-surface, #ffffff);
      --recorder-ink: var(--codex-base-ink, #1a1c1f);
      --recorder-muted: color-mix(in srgb, var(--recorder-ink) 8%, var(--recorder-page-surface));
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      background: var(--recorder-page-surface);
      color: var(--recorder-ink);
      {theme_style}
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --recorder-page-surface: var(--codex-base-surface, #181818);
        --recorder-badge-surface: var(--codex-base-surface, #181818);
        --recorder-ink: var(--codex-base-ink, #ffffff);
      }}
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--recorder-page-surface);
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
    .source-badge {{
      display: inline-flex;
      justify-content: center;
      align-items: center;
      width: 1.45em;
      height: 1.45em;
      margin-right: 0.5em;
      border-radius: 999px;
      vertical-align: -0.18em;
      border: 1px solid transparent;
    }}
    .source-microphone {{
      color: #ffffff;
      background: var(--recorder-accent);
    }}
    .source-system {{
      color: var(--recorder-page-surface);
      background: var(--recorder-ink);
      border-color: color-mix(in srgb, var(--recorder-ink) 18%, transparent);
    }}
    .source-icon {{
      display: block;
      width: 0.72em;
      height: 0.72em;
      background: currentColor;
      -webkit-mask: var(--source-icon-url) center / contain no-repeat;
      mask: var(--source-icon-url) center / contain no-repeat;
    }}
    #cursor {{
      display: { "inline-block" if active else "none" };
      width: 1ch;
      margin-left: 1px;
      border-bottom: 2px solid var(--recorder-ink);
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
          els.text.innerHTML = renderTranscript(await transcriptResponse.text());
          window.scrollTo(0, document.body.scrollHeight);
        }}
        if (statusResponse.ok) {{
          const payload = await statusResponse.json();
          els.cursor.style.display = payload.active ? "inline-block" : "none";
        }}
      }} catch (error) {{
      }}
    }}

    function escapeHtml(value) {{
      return value.replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function renderTranscript(value) {{
      return escapeHtml(value.replace(/[\\r\\n]+$/g, "")).replace(
        /^\\[(Microphone|System)\\]\\s*/gm,
        (_, source) => {{
          const icon = source === "Microphone" ? "microphone-solid-full.svg" : "volume-high-solid-full.svg";
          const label = source === "Microphone" ? "Microphone" : "System audio";
          return `<span class="source-badge source-${{source.toLowerCase()}}" title="${{label}}" aria-label="${{label}}"><span class="source-icon" style="--source-icon-url: url(&quot;/assets/${{icon}}&quot;)" aria-hidden="true"></span></span>`;
        }}
      );
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
        try:
            os.killpg(os.getpgid(pid), signal.SIGINT)
        except ProcessLookupError:
            pass
        except PermissionError:
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
        formatting_prompt_path = write_formatting_prompt(Path(state["recording_dir"]), transcript_file, state)
        formatted_path = write_formatted_transcript_placeholder(Path(state["recording_dir"]), formatting_prompt_path)
        state["transcript_formatting_prompt_file"] = str(formatting_prompt_path)
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
        "--source-overlap-policy",
        args.source_overlap_policy,
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


def audio_health_thresholds(args: argparse.Namespace, source: str) -> tuple[float, float]:
    if source == "microphone":
        return args.mic_silence_threshold, args.mic_peak_threshold
    return args.system_silence_threshold, args.system_peak_threshold


def run_audio_health_check(args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    command = [
        str(HELPER_BIN),
        "probe-audio",
        "--duration",
        str(args.audio_health_duration),
    ]
    if not args.system_audio:
        command.append("--no-system-audio")
    if not args.microphone:
        command.append("--no-microphone")

    log_path = out_dir / "audio-health.log"
    started_at = datetime.now().isoformat(timespec="seconds")
    result: dict[str, Any] = {
        "enabled": True,
        "started_at": started_at,
        "duration_seconds": args.audio_health_duration,
        "strict": bool(args.strict_audio_health_check),
        "thresholds": {
            "mic_silence_threshold": args.mic_silence_threshold,
            "mic_peak_threshold": args.mic_peak_threshold,
            "system_silence_threshold": args.system_silence_threshold,
            "system_peak_threshold": args.system_peak_threshold,
        },
        "log_file": str(log_path),
        "sources": {},
        "warnings": [],
    }

    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=max(10.0, args.audio_health_duration + 8.0),
        )
    except subprocess.CalledProcessError as exc:
        log_path.write_bytes((exc.stderr or b"") + (exc.stdout or b""))
        raise SystemExit(f"Audio health check failed before recorder startup. Log:\n{log_path.read_text(errors='replace')}") from exc
    except subprocess.TimeoutExpired as exc:
        log_path.write_bytes((exc.stderr or b"") + (exc.stdout or b""))
        raise SystemExit(f"Audio health check timed out before recorder startup. Log:\n{log_path.read_text(errors='replace')}") from exc

    log_path.write_bytes(completed.stderr)
    try:
        probe_results = json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Audio health check returned invalid JSON: {completed.stdout.decode('utf-8', errors='replace')}") from exc

    expected_sources = []
    if args.microphone:
        expected_sources.append("microphone")
    if args.system_audio:
        expected_sources.append("system")

    by_source = {str(item.get("source")): item for item in probe_results if isinstance(item, dict)}
    for source in expected_sources:
        item = by_source.get(source, {})
        captured_bytes = int(item.get("capturedBytes", 0) or 0)
        sample_count = int(item.get("sampleCount", 0) or 0)
        rms = float(item.get("rms", 0.0) or 0.0)
        peak = float(item.get("peak", 0.0) or 0.0)
        silence_threshold, peak_threshold = audio_health_thresholds(args, source)
        status = "ok"
        message = ""
        if captured_bytes <= 0 or sample_count <= 0:
            status = "silent"
            message = f"{source.title()} stream produced no captured samples."
        elif rms < silence_threshold and peak < peak_threshold:
            status = "silent"
            if source == "microphone":
                message = "Microphone stream appears silent. Check macOS input device, meeting app input device, and microphone permissions."
            else:
                message = "System audio stream appears silent. Check macOS Screen Recording permission and confirm meeting audio is playing."

        source_result = {
            "status": status,
            "captured_bytes": captured_bytes,
            "sample_count": sample_count,
            "rms": rms,
            "peak": peak,
            "silence_threshold": silence_threshold,
            "peak_threshold": peak_threshold,
            "message": message,
        }
        result["sources"][source] = source_result
        if message:
            result["warnings"].append(message)

    result["completed_at"] = datetime.now().isoformat(timespec="seconds")
    if result["warnings"]:
        for warning in result["warnings"]:
            print(f"audio_health_warning: {warning}", file=sys.stderr)
        if args.strict_audio_health_check:
            raise SystemExit("Audio health check failed:\n" + "\n".join(result["warnings"]))
    return result


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
    audio_health_check: dict[str, Any] = {"enabled": False}

    if args.audio_health_check:
        audio_health_check = run_audio_health_check(args, out_dir)

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
        "audio_health_check": audio_health_check,
        "source_diarization": {
            "enabled": True,
            "microphone_label": "Microphone" if args.microphone else None,
            "system_label": "System" if args.system_audio else None,
            "strategy": "tagged_single_capture_separate_realtime_sessions",
        },
        "audio_gate": {
            "silence_threshold": args.silence_threshold,
            "peak_threshold": args.peak_threshold,
            "trailing_silence_chunks": args.trailing_silence_chunks,
        },
        "source_overlap_policy": args.source_overlap_policy,
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
            parsed_path = urlparse(self.path)
            if parsed_path.path == "/status":
                self._send(200, "application/json", json.dumps(payload, indent=2, sort_keys=True))
                return
            if parsed_path.path == "/transcript":
                transcript_file = payload.get("transcript_file")
                if transcript_file and Path(str(transcript_file)).exists():
                    self._send(200, "text/plain; charset=utf-8", Path(str(transcript_file)).read_text(encoding="utf-8", errors="replace"))
                else:
                    self._send(200, "text/plain; charset=utf-8", "")
                return
            if parsed_path.path.startswith("/assets/"):
                asset_name = Path(parsed_path.path).name
                asset_path = ASSETS_DIR / asset_name
                if asset_path.exists() and asset_path.suffix == ".svg":
                    self._send(200, "image/svg+xml", asset_path.read_bytes())
                else:
                    self._send(404, "text/plain; charset=utf-8", "Not found")
                return
            self._send(200, "text/html; charset=utf-8", render_status_html(payload, theme_style=theme_style_from_path(self.path)))

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/stop":
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


def prepare_formatting(args: argparse.Namespace) -> None:
    recording_dir = args.recording_dir.resolve()
    metadata_path = recording_dir / "metadata.json"
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    transcript_file = Path(str(metadata.get("transcript_file") or recording_dir / "live_transcript.txt"))
    if not transcript_file.is_absolute():
        transcript_file = recording_dir / transcript_file
    if not transcript_file.exists():
        raise SystemExit(f"Missing transcript file: {transcript_file}")
    formatting_prompt_path = write_formatting_prompt(recording_dir, transcript_file, metadata)
    formatted_path = write_formatted_transcript_placeholder(recording_dir, formatting_prompt_path)
    metadata["transcript_formatting_prompt_file"] = str(formatting_prompt_path)
    metadata["formatted_transcript_file"] = str(formatted_path)
    metadata["formatted_transcript_size_bytes"] = formatted_path.stat().st_size
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "formatted_transcript_file": str(formatted_path),
        "transcript_formatting_prompt_file": str(formatting_prompt_path),
    }, indent=2, sort_keys=True))


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
    start_parser.add_argument("--delay", choices=["minimal", "low", "medium", "high", "xhigh"], default="medium")
    start_parser.add_argument("--commit-interval", type=float, default=6.0)
    start_parser.add_argument("--audio-chunk-ms", type=int, default=200)
    start_parser.add_argument("--silence-threshold", type=float, default=8.0)
    start_parser.add_argument("--peak-threshold", type=float, default=80.0)
    start_parser.add_argument("--audio-health-check", action=argparse.BooleanOptionalAction, default=True)
    start_parser.add_argument("--strict-audio-health-check", action="store_true", help="Fail startup when an enabled audio source appears silent")
    start_parser.add_argument("--audio-health-duration", type=float, default=3.0)
    start_parser.add_argument("--mic-silence-threshold", type=float, default=10.0)
    start_parser.add_argument("--mic-peak-threshold", type=float, default=120.0)
    start_parser.add_argument("--system-silence-threshold", type=float, default=10.0)
    start_parser.add_argument("--system-peak-threshold", type=float, default=120.0)
    start_parser.add_argument("--trailing-silence-chunks", type=int, default=5)
    start_parser.add_argument("--source-overlap-policy", choices=["keep", "suppress-mic", "mark-overlap"], default="suppress-mic")
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

    formatting_parser = subparsers.add_parser("prepare-formatting")
    formatting_parser.add_argument("recording_dir", type=Path)
    formatting_parser.set_defaults(func=prepare_formatting)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
