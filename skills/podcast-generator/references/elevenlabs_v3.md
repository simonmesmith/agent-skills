# ElevenLabs v3 Reference

Use this reference when generating podcast dialogue audio with ElevenLabs.

## Defaults

- API key environment variable: `ELEVENLABS_API_KEY`
- Endpoint: `POST https://api.elevenlabs.io/v1/text-to-dialogue`
- Default model: `eleven_v3`
- Default output format: `mp3_44100_128`
- Default reliable chunk budget: 2,000 total text characters per dialogue request

## Voice Mapping

Each host needs a voice ID before live audio generation:

```bash
python scripts/generate_audio.py script.csv \
  --voice host_a=VOICE_ID_A \
  --voice host_b=VOICE_ID_B
```

Keep internal host labels stable (`host_a`, `host_b`) even when the user gives display names. Voice choices can change without rewriting the script.

For two-host first drafts, use the proven defaults unless the user supplied specific voices:

| host | default voice | voice ID |
| --- | --- | --- |
| `host_a` | Roger - Laid-Back, Casual, Resonant | `CwhRBWXzGAHq8TQ4Fs17` |
| `host_b` | Sarah - Mature, Reassuring, Confident | `EXAVITQu4vr4xnSDxMaL` |

Run:

```bash
python scripts/generate_audio.py script.csv --use-default-voices
```

## Audio Tags

Eleven v3 can respond to square-bracket performance tags. Use them sparingly and only when the user wants expressive delivery.

Useful examples:

```text
[laughs]
[whispers]
[sighs]
[curious]
[excited]
[sarcastic]
[clears throat]
```

Do not use SSML break tags for Eleven v3. Prefer punctuation, short turns, ellipses, or square-bracket performance tags.

## Pronunciation

Use pronunciation dictionaries when the user provides ElevenLabs pronunciation dictionary locators. Otherwise:

- Prefer approved project glossary spellings.
- Use inline phonetic spellings only when they are approved for the spoken script.
- Do not silently alter brand or medical terms.
- Keep pronunciation notes in the `pronunciation` column until approved.

If the project has an approved `pronunciation_glossary.csv`, pass it to the generator:

```bash
python scripts/generate_audio.py script.csv --pronunciation-glossary pronunciation_glossary.csv --voice host_a=VOICE_ID_A --voice host_b=VOICE_ID_B
```

This preserves the review script while applying term replacements only inside the ElevenLabs request payload.

## Chunking

Prefer chunk mode for natural host interaction. Line mode is useful for smoke tests and surgical replacements, but it can sound choppy because each line is generated independently.

Reliable request limits:

- Keep the total character count across all `inputs[].text` values at or below 2,000 characters per request.
- Keep a chunk to a coherent conversational section, not an arbitrary equal-size slice.
- Avoid splitting setup/response pairs, short exchanges, or a heading and its first explanatory turn.
- Use one chunk directly when the whole test segment fits; do not merge a one-chunk output unless a normalized final export is needed.

Chunk filenames carry the covered line IDs:

```text
chunk_001-008.mp3
chunk_009-016.mp3
```

Use line mode for surgical replacement:

```text
001_host_a.mp3
002_host_b.mp3
```

Chunk boundaries should avoid splitting a setup and response when possible. When client edits land inside a chunk, regenerate that chunk and reuse unaffected chunks.

Editing model:

```text
reuse chunk_001-008.mp3
regenerate chunk_009-016.mp3
reuse chunk_017-024.mp3
merge chunks in manifest order
```

The script table keeps line-level IDs. The audio manifest maps each generated chunk to `id_start` and `id_end`, which is how changed rows identify the smallest affected chunk.
