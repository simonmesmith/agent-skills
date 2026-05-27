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
DEFAULT_DELAY = "low"


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


class TranscriptStore:
    """Append-only transcript files that the preview and Codex can read live."""

    def __init__(self, paths: TranscriptPaths) -> None:
        self.paths = paths
        self.lock = threading.Lock()
        self.delta_items: set[str] = set()
        self.text = ""
        paths.live.parent.mkdir(parents=True, exist_ok=True)
        paths.live.write_text("", encoding="utf-8")
        if paths.events:
            paths.events.parent.mkdir(parents=True, exist_ok=True)
            paths.events.write_text("", encoding="utf-8")

    def write_event(self, event: dict[str, Any]) -> None:
        if not self.paths.events:
            return
        with self.lock:
            with self.paths.events.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def append_delta(self, item_id: str, text: str) -> None:
        if not text:
            return
        with self.lock:
            self.delta_items.add(item_id)
            self.text += text
            with self.paths.live.open("a", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()

    def complete_item(self, item_id: str, transcript: str) -> None:
        if not transcript:
            return
        with self.lock:
            if item_id not in self.delta_items:
                self.text = join_transcript(self.text, transcript)
            else:
                self.text = re.sub(r"[ \t]+", " ", self.text).strip()
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


class OpenAIRealtimeWhisperBackend:
    def __init__(self, args: argparse.Namespace, store: TranscriptStore) -> None:
        self.args = args
        self.store = store
        self.stop_event = threading.Event()
        self.ws: Any = None
        self.audio_process: subprocess.Popen[bytes] | None = None
        self.caffeinate_process: subprocess.Popen[bytes] | None = None
        self.last_commit_at = time.monotonic()
        self.last_audio_at = time.monotonic()
        self.has_uncommitted_audio = False
        self.trailing_chunks_remaining = 0

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

        while not self.stop_event.is_set():
            self.ws = websocket.WebSocketApp(
                self.args.websocket_url,
                header=headers,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
            )
            self.ws.run_forever()
            print("websocket_run_forever_returned", file=sys.stderr, flush=True)
            self.stop_audio_process()
            if not self.stop_event.is_set():
                time.sleep(1)
        self.stop_audio_process()

    def handle_signal(self, signum: int, frame: Any) -> None:
        self.stop_event.set()
        self.stop_audio_process()
        if self.ws:
            try:
                self.commit_audio()
                self.ws.close()
            except Exception:
                pass

    def on_open(self, ws: Any) -> None:
        self.last_commit_at = time.monotonic()
        self.last_audio_at = time.monotonic()
        self.has_uncommitted_audio = False
        self.trailing_chunks_remaining = 0
        ws.send(json.dumps(self.session_update_event()))
        threading.Thread(target=self.stream_audio, args=(ws,), daemon=True).start()

    def on_message(self, ws: Any, message: str) -> None:
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            return

        event_type = event.get("type")
        if event_type:
            self.store.write_event(event)

        if event_type == "conversation.item.input_audio_transcription.delta":
            self.store.append_delta(str(event.get("item_id", "")), str(event.get("delta", "")))
        elif event_type == "conversation.item.input_audio_transcription.completed":
            self.store.complete_item(str(event.get("item_id", "")), str(event.get("transcript", "")))
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
        ]
        if not self.args.system_audio:
            command.append("--no-system-audio")
        if not self.args.microphone:
            command.append("--no-microphone")

        log_handle = self.args.audio_log.open("ab")
        raw_handle = self.args.raw_audio.open("ab") if self.args.raw_audio else None
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
                        print("audio_capture_exited", file=sys.stderr, flush=True)
                        self.stop_event.set()
                        if self.ws:
                            self.ws.close()
                        break
                    continue
                if raw_handle:
                    raw_handle.write(chunk)
                    raw_handle.flush()
                voice = self.has_voice(chunk)
                if voice:
                    self.trailing_chunks_remaining = self.args.trailing_silence_chunks
                should_send = voice or self.trailing_chunks_remaining > 0
                if not should_send:
                    if self.has_uncommitted_audio and time.monotonic() - self.last_audio_at >= 0.5:
                        self.commit_audio()
                    continue
                if not voice:
                    self.trailing_chunks_remaining -= 1
                ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }))
                self.last_audio_at = time.monotonic()
                self.has_uncommitted_audio = True
                if time.monotonic() - self.last_commit_at >= self.args.commit_interval:
                    self.commit_audio()
        finally:
            if raw_handle:
                raw_handle.close()
            self.commit_audio()
        self.stop_audio_process()

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
    parser.add_argument("--commit-interval", type=float, default=1.25)
    parser.add_argument("--audio-chunk-ms", type=int, default=100)
    parser.add_argument("--silence-threshold", type=float, default=8.0)
    parser.add_argument("--peak-threshold", type=float, default=80.0)
    parser.add_argument("--trailing-silence-chunks", type=int, default=5)
    parser.add_argument("--system-audio", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--microphone", action=argparse.BooleanOptionalAction, default=True)
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
