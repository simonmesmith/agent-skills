---
name: podcast-generator
description: Generate NotebookLM-style podcasts from documents or supplied scripts. Use when Codex needs to turn source material into a reviewable single-host, two-host, or multi-host podcast script; create host-marked dialogue with stable IDs; manage client revisions and pronunciation guidance; generate ElevenLabs v3 Text to Dialogue audio in natural chunks; or merge chunked audio with FFmpeg into a final episode.
---

# Podcast Generator

## Overview

Produce reviewable, revision-friendly podcast episodes from source material or an existing script. Default to a two-host conversational format with `host_a` and `host_b`, but support one or more hosts when the user's request makes that clear.

Keep the user flow short. Ask at most two setup questions for document-driven work when needed: target length and audience/tone. If the user supplies a script, normalize it directly and ask only when host attribution or source intent is blocking.

## Workflow

1. Determine input mode:
   - **Documents to podcast**: extract key claims, source context, and high-risk terminology; draft a conversational script.
   - **Script to production table**: preserve meaning, split long turns, assign IDs, and flag unclear host attribution.
2. Create the script table as the source of truth. Use `assets/podcast_script_template.csv` or `.xlsx` when the user needs a review file.
3. Check pronunciation guidance before final script approval:
   - Look for files named like `pronunciation.csv`, `glossary.*`, `brand_terms.*`, or brand/medical notes in the source set.
   - If no guidance exists, identify high-risk terms such as brands, drug names, acronyms, mechanisms, institutions, and names, then create a lightweight pronunciation glossary for review.
   - Do not hardcode medical pronunciations into the skill.
4. Validate the script table with `scripts/validate_script.py`.
5. After user/client approval, optionally renumber inserted IDs with `scripts/renumber_script.py`.
6. Generate audio with `scripts/generate_audio.py`, using ElevenLabs Text to Dialogue chunk mode by default.
7. Use the generated chunk directly when there is only one chunk. Merge ordered chunks with `scripts/merge_audio.py` only when the episode spans multiple chunks or the user wants a normalized final export.
8. For client edits, compare the revised table against the previous table, regenerate only changed chunks/lines, and preserve prior audio.

## Script Table Rules

Required columns:

```text
id,host,text
```

Recommended columns:

```text
status,notes,pronunciation
```

Production/revision columns:

```text
production_id,source_id,audio_file,chunk_id
```

Use `001`, `002`, `003` for initial IDs. For insertions, use sortable decimal IDs such as `002.5`, `002.1`, or `002.2`. Before final generation, renumber to clean production IDs while preserving `source_id`.

Use these statuses:

```text
draft
client-edited
approved
generated
regenerate
skip
```

Load `references/script_schema.md` for exact schema details and examples.

## Audio Generation

Default to ElevenLabs Text to Dialogue with `model_id=eleven_v3`. Require `ELEVENLABS_API_KEY` in the environment before live generation. Never ask the user to paste the key into chat.

Prefer chunk mode for natural host interaction. Use line mode only when surgical replacement matters more than conversational flow. Keep each dialogue request under the current reliable generation budget; this skill defaults to 2,000 total text characters per chunk.

Voice IDs must be supplied before live ElevenLabs generation:

```bash
python scripts/generate_audio.py script.csv --voice host_a=VOICE_ID_A --voice host_b=VOICE_ID_B
```

For two-host tests and first drafts, prefer the proven defaults:

```bash
python scripts/generate_audio.py script.csv --use-default-voices
```

Default voices:

```text
host_a: Roger - Laid-Back, Casual, Resonant (CwhRBWXzGAHq8TQ4Fs17)
host_b: Sarah - Mature, Reassuring, Confident (EXAVITQu4vr4xnSDxMaL)
```

When an approved pronunciation glossary exists, pass it during generation:

```bash
python scripts/generate_audio.py script.csv --pronunciation-glossary pronunciation_glossary.csv --voice host_a=VOICE_ID_A --voice host_b=VOICE_ID_B
```

The script writes ordered audio plus `audio_manifest.csv`. If the manifest has one generated chunk, that MP3 is already the usable episode segment. Use `scripts/merge_audio.py` to create the final episode when there are multiple chunks.

Load `references/elevenlabs_v3.md` when selecting audio tags, pronunciation behavior, chunking, or ElevenLabs request options.

## Client Revisions

Use the table IDs to keep revisions stable. Treat edited rows, inserted IDs, skipped rows, and deleted rows as production changes. Regenerate only the affected line or chunk. Preserve previous audio files unless the user explicitly asks to replace them.

Load `references/revision_workflow.md` before comparing a returned client file to an earlier approved script.

## Bundled Resources

- `assets/podcast_script_template.csv`: client-review CSV template.
- `assets/podcast_script_template.xlsx`: client-review workbook template.
- `scripts/validate_script.py`: validate table columns, IDs, hosts, text, statuses, and ordering.
- `scripts/renumber_script.py`: create clean production IDs while retaining original source IDs.
- `scripts/generate_audio.py`: generate ElevenLabs dialogue audio and an audio manifest.
- `scripts/merge_audio.py`: concatenate manifest audio with FFmpeg.
- `scripts/compare_revisions.py`: compare old and revised script tables and report reuse/regeneration actions.
- `scripts/extract_pronunciation_glossary.py`: create a review starter glossary from source text.
