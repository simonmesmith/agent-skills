---
name: "codex-meeting-recorder"
description: "Record and live-transcribe a meeting or live conversation from a Codex thread on macOS, saving streaming transcript text to the workspace with OpenAI Realtime transcription, then summarize notes in-thread. Use when the user asks to start, stop, capture, transcribe, ask questions about, or summarize a meeting recording from the current thread."
---

# Codex Meeting Recorder

Record and live-transcribe a meeting from the current workspace. The default v2 path streams macOS system audio and microphone audio to OpenAI Realtime transcription, writes transcript text as it arrives, shows a minimal live preview, and leaves the transcript files in the workspace for Codex to answer questions during or after the meeting.

## Requirements

- macOS 15 or newer.
- Xcode command line tools with Swift.
- `OPENAI_API_KEY` available when live transcription starts.
- `websocket-client` available to the realtime worker. If missing, the controller can use `uv run --with websocket-client` automatically when `uv` is installed.
- Python OpenAI SDK available only when using the legacy finished-file transcriber. If missing, the controller can use `uv run --with openai` automatically when `uv` is installed.
- First recording may prompt for Screen Recording and Microphone permissions. If macOS grants permission but capture fails, ask the user to restart Codex/Terminal and retry.
- Start commands should be run outside Codex's command sandbox when available, because the live status page binds a localhost port and macOS permissions may need direct process access.

## Output Layout

Use a workspace-local folder:

```text
recordings/
  YYYY-MM-DD-HHMMSS/
    recording.mp4
    live_transcript.txt
    formatted_transcript.md
    metadata.json
    recorder.log
    audio-capture.log
    status-server.log
```

In v2, `live_transcript.txt` is the canonical live text stream. The preview reads this file and renders only the transcript text plus a blinking cursor while transcription is active. Codex should also use this file as the source of truth when answering questions during the meeting.

When the meeting stops, the controller writes `formatted_transcript.md` from the live stream for future reading and final summarization. Treat `formatted_transcript.md` as a post-meeting artifact, not a live source.

The legacy `recording.mp4` and `scripts/transcription.py` path remains available for finished-file transcription, but the default start command uses realtime transcription.

While transcription is active, the controller starts a localhost preview page and stores its URL in `recordings/.current-recording.json`.

For debugging only, `start --save-events` writes `transcript_events.jsonl`, and `start --save-raw-audio` writes `input_audio.pcm`. Do not enable raw audio for ordinary meetings because PCM grows quickly.

## Commands

Set the skill path if needed:

```bash
export CODEX_MEETING_RECORDER_SKILL="/path/to/codex-meeting-recorder"
```

Start live transcription:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" start --workspace .
```

After start, immediately open the returned `status_url` in Codex's in-app browser/preview, then share the URL with the user. When using the in-app browser, explicitly set browser visibility to true before navigating to the URL. The preview is intentionally minimal: a white page with transcribed text and a subtle blinking cursor while transcription is live.

Stop live transcription:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" stop --workspace .
```

During a meeting, answer questions by reading the current transcript file from the active state:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" status --workspace .
```

Then read or search the `transcript_file` path from the returned JSON. Prefer the file over the preview server as the source of truth.

Optional realtime settings:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" start --workspace . --delay minimal
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" start --workspace . --no-system-audio
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" start --workspace . --save-events
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" start --workspace . --backend local-nemotron
```

`local-nemotron` is a placeholder adapter boundary for future local streaming ASR and intentionally exits until that backend is implemented.

Legacy stop-and-transcribe for an MP4 recording:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" stop --workspace . --transcribe
```

The `--transcribe` path invokes this skill's bundled transcription helper:

```bash
"$CODEX_MEETING_RECORDER_SKILL/scripts/transcription.py"
```

If the caller's Python environment is missing the OpenAI SDK, the controller may fall back to `uv run --with openai`.

Check status:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" status --workspace .
```

Serve only the live status page, usually for debugging:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" serve-status --workspace . --port 47832
```

Transcribe the latest stopped recording:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" transcribe --workspace .
```

## Workflow

1. Start live transcription when the user asks.
2. Immediately open the returned `status_url` in Codex's in-app browser/preview so the live transcript is visible without the user doing anything. For the in-app browser, explicitly set the browser visibility capability to true before navigation.
3. Return the `status_url` so the user can also open it manually if needed.
4. Leave the background realtime worker running until the user asks to stop.
5. If the user asks questions during the meeting, load or search the active `transcript_file`; do not build a separate preview API client.
6. Stop the worker with `recorderctl.py stop --workspace .`.
7. Read `formatted_transcript_file` when available, otherwise `transcript_file`, and provide concise meeting notes in the thread.

## Notes Style

Summarize with:

- Decisions
- Action items with owners when stated
- Open questions
- Key discussion points

Keep notes grounded in the transcript. If speaker labels are unavailable, do not invent them.

## Current Limitations

- v2 writes live transcript files, not a combined MP4, on the default realtime path. Separate system/microphone tracks are a future improvement.
- v2 uses `gpt-realtime-whisper` through `scripts/realtime_transcription.py`. The backend boundary is intentionally small so a local streaming ASR backend, such as Nemotron via NeMo/Riva, can be added later.
- ScreenCaptureKit permission prompts are controlled by macOS.
- The realtime worker uses `caffeinate` while capture is active because ScreenCaptureKit can fail to enumerate displays when the display is asleep.
- A small local silence gate avoids sending empty audio commits during quiet moments.
- Raw Realtime events and raw PCM audio are debug-only opt-ins, not normal meeting outputs.
- The Swift helper still supports the legacy MP4 recording mode, but the realtime path uses `stream-pcm` to emit 24 kHz mono PCM for the Realtime API.
