---
name: "codex-meeting-recorder"
description: "Record a meeting or live conversation from a Codex thread on macOS, saving system audio and microphone audio to the workspace, then transcribe it with the bundled OpenAI transcription helper and summarize notes in-thread. Use when the user asks to start, stop, capture, transcribe, or summarize a meeting recording from the current thread."
---

# Codex Meeting Recorder

Record a meeting from the current workspace, transcribe it with this skill's bundled transcription helper, and summarize the transcript in the thread.

## Requirements

- macOS 15 or newer.
- Xcode command line tools with Swift.
- `OPENAI_API_KEY` available when transcribing.
- Python OpenAI SDK available to the transcriber. If missing, the controller can use `uv run --with openai` automatically when `uv` is installed.
- First recording may prompt for Screen Recording and Microphone permissions. If macOS grants permission but capture fails, ask the user to restart Codex/Terminal and retry.
- Start commands should be run outside Codex's command sandbox when available, because the live status page binds a localhost port and macOS permissions may need direct process access.

## Output Layout

Use a workspace-local folder:

```text
recordings/
  YYYY-MM-DD-HHMMSS/
    recording.mp4
    transcript.txt
    metadata.json
    recorder.log
    status-server.log
```

The recording is an `.mp4` container from ScreenCaptureKit. It is intentionally tiny video plus captured audio; the bundled transcriber reads the audio stream.
While a recording is active, the controller also starts a localhost status page and stores its URL in `recordings/.current-recording.json`.

## Commands

Set the skill path if needed:

```bash
export CODEX_MEETING_RECORDER_SKILL="/path/to/codex-meeting-recorder"
```

Start recording:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" start --workspace .
```

After start, share the returned `status_url` with the user. It shows a live recording indicator, timer, file size, destination path, and a stop button.

Stop recording:

```bash
python3 "$CODEX_MEETING_RECORDER_SKILL/scripts/recorderctl.py" stop --workspace .
```

Stop and transcribe:

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

1. Start recording when the user asks.
2. Return the `status_url` so the user can watch the recording indicator in a browser or Codex preview pane.
3. Leave the background recorder running until the user asks to stop or presses Stop Recording on the status page.
4. Stop the recorder and wait for the file to finalize.
5. Invoke the bundled transcriber with `recorderctl.py transcribe` or `recorderctl.py stop --transcribe`.
6. Read the transcript file and provide concise meeting notes in the thread.

## Notes Style

Summarize with:

- Decisions
- Action items with owners when stated
- Open questions
- Key discussion points

Keep notes grounded in the transcript. If speaker labels are unavailable, do not invent them.

## Current Limitations

- v1 writes one combined recording file. Separate system/microphone tracks are a future improvement.
- v1 uses OpenAI transcription via `scripts/transcription.py`. Future versions may add a local transcription backend behind the same command.
- ScreenCaptureKit permission prompts are controlled by macOS.
- The helper captures tiny video frames because ScreenCaptureKit recording output writes a media stream, but the useful payload is audio.
