# Codex Meeting Recorder

## v1 Scope

This version records live macOS system audio and microphone audio from a Codex thread, streams it to OpenAI Realtime transcription, writes a live transcript, and shows a minimal browser preview.

Default meeting outputs:

- `live_transcript.txt`: live text stream used by the preview and by Codex during a meeting.
- `formatted_transcript.md`: post-stop readable transcript generated from the live stream.
- `metadata.json`: run metadata and output paths.
- `recorder.log`, `audio-capture.log`, `status-server.log`: operational logs.

Debug-only outputs:

- `transcript_events.jsonl`, enabled with `--save-events`.
- `input_audio.pcm`, enabled with `--save-raw-audio`.

## Known v1 Behavior

- Realtime punctuation and capitalization are imperfect. `live_transcript.txt` intentionally stays close to the streamed model output.
- `formatted_transcript.md` currently applies only lightweight whitespace and paragraph formatting.
- The preview is intentionally plain: white background, transcript text, and a blinking cursor while active.
- The controller should open the preview in the Codex in-app browser and explicitly set browser visibility before navigation.
- ScreenCaptureKit can fail when displays are asleep, so the worker wakes the display and holds a `caffeinate` assertion while capture is active.

## Future Considerations

- Use `--save-events` runs to evaluate whether Realtime events expose useful sentence, segment, timing, speaker, or confidence signals for formatting.
- Explore `conversation.item.input_audio_transcription.segment` if available from the selected model; it may provide better boundaries than text-only deltas.
- Consider server-side VAD instead of manual commits, especially if it provides useful `speech_started` and `speech_stopped` events without hurting latency.
- Add an optional post-stop punctuation restoration pass using an LLM with strict instructions: restore punctuation and paragraphs without rewriting wording.
- Add a local realtime backend adapter, likely behind the existing backend boundary. Candidate: NVIDIA Nemotron speech streaming or a local Whisper-compatible streaming ASR.
- Improve microphone handling and calibration, including visible diagnostics for mic level, system audio level, and silence-gate thresholds.
- Add robust speaker labeling if the backend exposes speaker segments or if a diarization pass is added after stop.
- Add a proactive Codex watcher mode that periodically reads `live_transcript.txt` and can suggest comments or follow-up questions when asked.
- Package a small smoke-test command for generated system audio so future changes can quickly verify ScreenCaptureKit plus Realtime transcription.
