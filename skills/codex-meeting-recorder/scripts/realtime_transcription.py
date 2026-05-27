#!/usr/bin/env python3
"""Realtime transcription worker for Codex Meeting Recorder."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Protocol


REALTIME_WS_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
PCM_RATE = 24_000
PCM_BYTES_PER_SAMPLE = 2
DEFAULT_MODEL = "gpt-realtime-whisper"
DEFAULT_DELAY = "medium"


def die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def join_transcript(existing: str, addition: str) -> str:
    addition = addition.strip()
    if not addition:
        return existing
    if not existing:
        return addition
    if existing[-1].isspace() or addition[0] in ".,;:!?)]}%":
        return existing + addition
    return existing + " " + addition


@dataclass
class TranscriptPaths:
    live: Path
    events: Path | None = None


@dataclass(frozen=True)
class SourceConfig:
    name: str
    label: str
    helper_args: list[str]


class TranscriptStore:
    """Append-only transcript files that the preview and Codex can read live."""

    def __init__(self, paths: TranscriptPaths) -> None:
        self.paths = paths
        self.lock = threading.Lock()
        self.delta_items: set[str] = set()
        self.item_sources: dict[str, str] = {}
        self.text = ""
        paths.live.parent.mkdir(parents=True, exist_ok=True)
        paths.live.write_text("", encoding="utf-8")
        if paths.events:
            paths.events.parent.mkdir(parents=True, exist_ok=True)
            paths.events.write_text("", encoding="utf-8")

    def write_event(self, event: dict[str, Any], source: str | None = None) -> None:
        if not self.paths.events:
            return
        if source:
            event = {**event, "codex_audio_source": source}
        with self.lock:
            with self.paths.events.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def append_delta(self, item_id: str, text: str, source: str, label: str) -> None:
        if not text:
            return
        source_item_id = f"{source}:{item_id}"
        with self.lock:
            if source_item_id not in self.item_sources:
                prefix = f"\n\n[{label}] " if self.text.strip() else f"[{label}] "
                text = text.lstrip()
                self.text += prefix
                with self.paths.live.open("a", encoding="utf-8") as handle:
                    handle.write(prefix)
                    handle.flush()
            self.item_sources[source_item_id] = source
            self.delta_items.add(source_item_id)
            self.text += text
            with self.paths.live.open("a", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()

    def complete_item(self, item_id: str, transcript: str, source: str, label: str) -> None:
        if not transcript:
            return
        source_item_id = f"{source}:{item_id}"
        with self.lock:
            if source_item_id not in self.delta_items:
                prefix = f"\n\n[{label}] " if self.text.strip() else f"[{label}] "
                self.text = f"{self.text}{prefix}{transcript}"
            else:
                self.text = re.sub(r"[ \t]+", " ", self.text).strip()
            self.item_sources[source_item_id] = source
            self.paths.live.write_text(self.text, encoding="utf-8")


class TranscriptionBackend(Protocol):
    def run(self) -> None:
        ...


class LocalNemotronBackend:
    """Placeholder adapter boundary for future local streaming ASR."""

    def run(self) -> None:
        die(
            "The local Nemotron backend is not implemented yet. "
            "The current v2 path uses OpenAI Realtime transcription."
        )


class SourceRealtimeSession:
    def __init__(
        self,
        args: argparse.Namespace,
        store: TranscriptStore,
        source: SourceConfig,
        headers: list[str],
        stop_event: threading.Event,
    ) -> None:
        self.args = args
        self.store = store
        self.source = source
        self.headers = headers
        self.stop_event = stop_event
        self.ws: Any = None
        self.opened_event = threading.Event()
        self.send_lock = threading.Lock()
        self.audio_process: subprocess.Popen[bytes] | None = None
        self.caffeinate_process: subprocess.Popen[bytes] | None = None
        self.last_commit_at = time.monotonic()
        self.last_audio_at = time.monotonic()
        self.last_stats_at = time.monotonic()
        self.has_uncommitted_audio = False
        self.trailing_chunks_remaining = 0
        self.chunks_read = 0
        self.chunks_sent = 0
        self.voice_chunks = 0
        self.suppressed_chunks = 0

    def run(self, websocket_module: Any) -> None:
        while not self.stop_event.is_set():
            self.ws = websocket_module.WebSocketApp(
                self.args.websocket_url,
                header=self.headers,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
            )
            self.ws.run_forever()
            print(f"{self.source.name}_websocket_run_forever_returned", file=sys.stderr, flush=True)
            self.stop_audio_process()
            if not self.stop_event.is_set():
                time.sleep(1)
        self.stop_audio_process()

    def on_open(self, ws: Any) -> None:
        self.last_commit_at = time.monotonic()
        self.last_audio_at = time.monotonic()
        self.has_uncommitted_audio = False
        self.trailing_chunks_remaining = 0
        with self.send_lock:
            ws.send(json.dumps(self.session_update_event()))
        self.opened_event.set()

    def on_message(self, ws: Any, message: str) -> None:
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            return

        event_type = event.get("type")
        if event_type:
            self.store.write_event(event, self.source.name)

        if event_type == "conversation.item.input_audio_transcription.delta":
            self.store.append_delta(
                str(event.get("item_id", "")),
                str(event.get("delta", "")),
                self.source.name,
                self.source.label,
            )
        elif event_type == "conversation.item.input_audio_transcription.completed":
            self.store.complete_item(
                str(event.get("item_id", "")),
                str(event.get("transcript", "")),
                self.source.name,
                self.source.label,
            )
        elif event_type == "error":
            print(json.dumps(event, indent=2), file=sys.stderr, flush=True)

    def on_error(self, ws: Any, error: Any) -> None:
        print(f"websocket_error: {error}", file=sys.stderr, flush=True)
        self.stop_audio_process()

    def on_close(self, ws: Any, close_status_code: Any, close_msg: Any) -> None:
        print(f"websocket_closed: status={close_status_code} message={close_msg}", file=sys.stderr, flush=True)
        self.stop_audio_process()

    def session_update_event(self) -> dict[str, Any]:
        transcription: dict[str, Any] = {
            "model": self.args.model,
        }
        if self.args.language:
            transcription["language"] = self.args.language
        if self.args.delay:
            transcription["delay"] = self.args.delay

        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": PCM_RATE,
                        },
                        "transcription": transcription,
                        "turn_detection": None,
                    }
                },
            },
        }

    def stream_audio(self, ws: Any) -> None:
        command = [
            str(self.args.helper_bin),
            "stream-pcm",
            *self.source.helper_args,
        ]

        log_handle = self.args.audio_log.open("ab")
        raw_handle = None
        if self.args.raw_audio:
            raw_handle = self.args.raw_audio.with_name(f"{self.args.raw_audio.stem}-{self.source.name}{self.args.raw_audio.suffix}").open("ab")
        self.wake_display()
        self.audio_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=log_handle,
        )
        self.start_caffeinate()

        chunk_bytes = max(
            PCM_BYTES_PER_SAMPLE,
            int(PCM_RATE * PCM_BYTES_PER_SAMPLE * (self.args.audio_chunk_ms / 1000.0)),
        )
        try:
            while not self.stop_event.is_set():
                if self.audio_process.stdout is None:
                    break
                chunk = self.audio_process.stdout.read(chunk_bytes)
                if not chunk:
                    if self.audio_process.poll() is not None:
                        print(f"{self.source.name}_audio_capture_exited", file=sys.stderr, flush=True)
                        self.stop_event.set()
                        if self.ws:
                            self.ws.close()
                        break
                    continue
                if raw_handle:
                    raw_handle.write(chunk)
                    raw_handle.flush()
                self.chunks_read += 1
                voice = self.has_voice(chunk)
                if voice:
                    self.voice_chunks += 1
                    self.trailing_chunks_remaining = self.args.trailing_silence_chunks
                should_send = voice or self.trailing_chunks_remaining > 0
                if not should_send:
                    if self.has_uncommitted_audio and time.monotonic() - self.last_audio_at >= 0.5:
                        self.commit_audio()
                    self.write_audio_stats(log_handle)
                    continue
                if not voice:
                    self.trailing_chunks_remaining -= 1
                ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }))
                self.chunks_sent += 1
                self.last_audio_at = time.monotonic()
                self.has_uncommitted_audio = True
                if time.monotonic() - self.last_commit_at >= self.args.commit_interval:
                    self.commit_audio()
                self.write_audio_stats(log_handle)
        finally:
            self.write_audio_stats(log_handle, force=True)
            if raw_handle:
                raw_handle.close()
            self.commit_audio()
        self.stop_audio_process()

    def accept_chunk(self, chunk: bytes, log_handle: Any, raw_handle: Any | None = None) -> None:
        if not self.ws or not self.opened_event.is_set():
            return
        if raw_handle:
            raw_handle.write(chunk)
            raw_handle.flush()
        self.chunks_read += 1
        voice = self.has_voice(chunk)
        if voice:
            self.voice_chunks += 1
            self.trailing_chunks_remaining = self.args.trailing_silence_chunks
        should_send = voice or self.trailing_chunks_remaining > 0
        if not should_send:
            if self.has_uncommitted_audio and time.monotonic() - self.last_audio_at >= 0.5:
                self.commit_audio()
            self.write_audio_stats(log_handle)
            return
        if not voice:
            self.trailing_chunks_remaining -= 1
        with self.send_lock:
            self.ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }))
        self.chunks_sent += 1
        self.last_audio_at = time.monotonic()
        self.has_uncommitted_audio = True
        if time.monotonic() - self.last_commit_at >= self.args.commit_interval:
            self.commit_audio()
        self.write_audio_stats(log_handle)

    def suppress_chunk(self, log_handle: Any) -> None:
        self.chunks_read += 1
        self.suppressed_chunks += 1
        if self.has_uncommitted_audio and time.monotonic() - self.last_audio_at >= 0.5:
            self.commit_audio()
        self.write_audio_stats(log_handle)

    def write_audio_stats(self, log_handle: Any, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_stats_at < 2.0:
            return
        self.last_stats_at = now
        log_handle.write(
            (
                f"{self.source.name}_audio_stats "
                f"chunks_read={self.chunks_read} "
                f"voice_chunks={self.voice_chunks} "
                f"chunks_sent={self.chunks_sent} "
                f"suppressed_chunks={self.suppressed_chunks}\n"
            ).encode("utf-8")
        )
        log_handle.flush()

    def start_caffeinate(self) -> None:
        if not self.audio_process:
            return
        try:
            self.caffeinate_process = subprocess.Popen(
                ["caffeinate", "-dimsu", "-w", str(self.audio_process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.caffeinate_process = None

    def wake_display(self) -> None:
        try:
            subprocess.Popen(
                ["caffeinate", "-u", "-t", "5"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
        except FileNotFoundError:
            return

    def commit_audio(self) -> None:
        if not self.ws or not self.has_uncommitted_audio:
            return
        try:
            with self.send_lock:
                self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            self.last_commit_at = time.monotonic()
            self.has_uncommitted_audio = False
        except Exception as exc:
            print(f"commit_failed: {exc}", file=sys.stderr, flush=True)

    def has_voice(self, chunk: bytes) -> bool:
        if len(chunk) < 2:
            return False
        sample_count = len(chunk) // 2
        total = 0
        for index in range(0, sample_count * 2, 2):
            total += abs(int.from_bytes(chunk[index:index + 2], "little", signed=True))
        mean_abs = total / sample_count
        peak = max(
            abs(int.from_bytes(chunk[index:index + 2], "little", signed=True))
            for index in range(0, sample_count * 2, 2)
        )
        return mean_abs >= self.args.silence_threshold or peak >= self.args.peak_threshold

    def stop_audio_process(self) -> None:
        process = self.audio_process
        if not process or process.poll() is not None:
            return
        try:
            process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return
        deadline = time.time() + 5
        while time.time() < deadline and process.poll() is None:
            time.sleep(0.1)
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        deadline = time.time() + 2
        while time.time() < deadline and process.poll() is None:
            time.sleep(0.1)
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        if self.caffeinate_process and self.caffeinate_process.poll() is None:
            self.caffeinate_process.terminate()


class OpenAIRealtimeWhisperBackend:
    def __init__(self, args: argparse.Namespace, store: TranscriptStore) -> None:
        self.args = args
        self.store = store
        self.stop_event = threading.Event()
        self.sessions: list[SourceRealtimeSession] = []
        self.audio_process: subprocess.Popen[bytes] | None = None
        self.caffeinate_process: subprocess.Popen[bytes] | None = None
        self.system_voice_until = 0.0

    def run(self) -> None:
        if not os.getenv("OPENAI_API_KEY"):
            die("OPENAI_API_KEY is not set. Export it before starting realtime transcription.")

        try:
            import websocket
        except ImportError:
            die("websocket-client is not installed. Run this worker with `uv run --with websocket-client python ...`.")

        headers = [
            "Authorization: Bearer " + os.environ["OPENAI_API_KEY"],
            "OpenAI-Safety-Identifier: codex-meeting-recorder",
        ]
        signal.signal(signal.SIGINT, self.handle_signal)
        signal.signal(signal.SIGTERM, self.handle_signal)

        sources = self.source_configs()
        if not sources:
            die("At least one audio source must be enabled.")
        self.store.write_event({
            "type": "codex.source_diarization.started",
            "sources": [{"name": source.name, "label": source.label} for source in sources],
        })

        self.sessions = [
            SourceRealtimeSession(self.args, self.store, source, headers, self.stop_event)
            for source in sources
        ]
        threads = [
            threading.Thread(target=session.run, args=(websocket,), daemon=True)
            for session in self.sessions
        ]
        for thread in threads:
            thread.start()
        for session in self.sessions:
            session.opened_event.wait(timeout=10)
        audio_thread = threading.Thread(target=self.stream_tagged_audio, daemon=True)
        audio_thread.start()
        try:
            while (audio_thread.is_alive() or any(thread.is_alive() for thread in threads)) and not self.stop_event.is_set():
                time.sleep(0.25)
        finally:
            self.stop_event.set()
            self.stop_audio_process()
            for session in self.sessions:
                if session.ws:
                    try:
                        session.commit_audio()
                        session.ws.close()
                    except Exception:
                        pass
            for thread in threads:
                thread.join(timeout=5)
            audio_thread.join(timeout=5)

    def source_configs(self) -> list[SourceConfig]:
        sources: list[SourceConfig] = []
        if self.args.microphone:
            sources.append(SourceConfig("microphone", "Microphone", ["--no-system-audio"]))
        if self.args.system_audio:
            sources.append(SourceConfig("system", "System", ["--no-microphone"]))
        return sources

    def handle_signal(self, signum: int, frame: Any) -> None:
        self.stop_event.set()
        self.stop_audio_process()
        for session in self.sessions:
            if session.ws:
                try:
                    session.commit_audio()
                    session.ws.close()
                except Exception:
                    pass

    def stream_tagged_audio(self) -> None:
        command = [
            str(self.args.helper_bin),
            "stream-pcm-json",
        ]
        if not self.args.system_audio:
            command.append("--no-system-audio")
        if not self.args.microphone:
            command.append("--no-microphone")

        sessions = {session.source.name: session for session in self.sessions}
        log_handle = self.args.audio_log.open("ab")
        raw_handles: dict[str, Any] = {}
        if self.args.raw_audio:
            for source in sessions:
                raw_handles[source] = self.args.raw_audio.with_name(f"{self.args.raw_audio.stem}-{source}{self.args.raw_audio.suffix}").open("ab")
        self.wake_display()
        self.audio_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=log_handle,
        )
        self.start_caffeinate()
        try:
            while not self.stop_event.is_set():
                if self.audio_process.stdout is None:
                    break
                line = self.audio_process.stdout.readline()
                if not line:
                    if self.audio_process.poll() is not None:
                        print("tagged_audio_capture_exited", file=sys.stderr, flush=True)
                        self.stop_event.set()
                        break
                    continue
                try:
                    payload = json.loads(line)
                    source = str(payload.get("source", ""))
                    audio = base64.b64decode(str(payload.get("audio", "")))
                except Exception as exc:
                    log_handle.write(f"tagged_audio_decode_failed error={exc}\n".encode("utf-8"))
                    log_handle.flush()
                    continue
                session = sessions.get(source)
                if session and audio:
                    system_session = sessions.get("system")
                    if source == "system" and system_session and system_session.has_voice(audio):
                        self.system_voice_until = time.monotonic() + 1.0
                    if (
                        source == "microphone"
                        and self.args.source_overlap_policy == "suppress-mic"
                        and time.monotonic() < self.system_voice_until
                    ):
                        session.suppress_chunk(log_handle)
                        continue
                    session.accept_chunk(audio, log_handle, raw_handles.get(source))
        finally:
            for session in self.sessions:
                session.write_audio_stats(log_handle, force=True)
                session.commit_audio()
            for handle in raw_handles.values():
                handle.close()
            self.stop_audio_process()

    def wake_display(self) -> None:
        try:
            subprocess.Popen(
                ["caffeinate", "-u", "-t", "5"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
        except FileNotFoundError:
            return

    def start_caffeinate(self) -> None:
        if not self.audio_process:
            return
        try:
            self.caffeinate_process = subprocess.Popen(
                ["caffeinate", "-dimsu", "-w", str(self.audio_process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.caffeinate_process = None

    def stop_audio_process(self) -> None:
        process = self.audio_process
        if process and process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            deadline = time.time() + 2
            while time.time() < deadline and process.poll() is None:
                time.sleep(0.1)
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            deadline = time.time() + 2
            while time.time() < deadline and process.poll() is None:
                time.sleep(0.1)
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        if self.caffeinate_process and self.caffeinate_process.poll() is None:
            self.caffeinate_process.terminate()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream meeting audio to OpenAI Realtime transcription.")
    parser.add_argument("--helper-bin", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--audio-log", type=Path, required=True)
    parser.add_argument("--raw-audio", type=Path)
    parser.add_argument("--backend", choices=["openai-realtime-whisper", "local-nemotron"], default="openai-realtime-whisper")
    parser.add_argument("--websocket-url", default=REALTIME_WS_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--language", default="en")
    parser.add_argument("--delay", choices=["minimal", "low", "medium", "high", "xhigh"], default=DEFAULT_DELAY)
    parser.add_argument("--commit-interval", type=float, default=6.0)
    parser.add_argument("--audio-chunk-ms", type=int, default=200)
    parser.add_argument("--silence-threshold", type=float, default=8.0)
    parser.add_argument("--peak-threshold", type=float, default=80.0)
    parser.add_argument("--trailing-silence-chunks", type=int, default=5)
    parser.add_argument("--system-audio", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--microphone", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--source-overlap-policy", choices=["keep", "suppress-mic", "mark-overlap"], default="suppress-mic")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = TranscriptStore(TranscriptPaths(args.transcript, args.events))
    if args.backend == "local-nemotron":
        backend: TranscriptionBackend = LocalNemotronBackend()
    else:
        backend = OpenAIRealtimeWhisperBackend(args, store)
    backend.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
