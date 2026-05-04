---
name: video-overview-generator
description: Create narrated video overviews from source documents using editable storyboard rows, OpenAI text-to-speech audio, OpenAI image generation, captions, and deterministic ffmpeg assembly. Use when Codex needs to turn PDFs, DOCX files, Markdown, text notes, research, meeting materials, briefs, or folders of documents into a NotebookLM-style static-image video overview with voiceover, or when the user wants to edit/regenerate individual narration or image rows without rebuilding the whole project.
---

# Video Overview Generator

Create document-grounded narrated videos from static generated images, voiceover, captions, and deterministic video assembly. Do not imply this skill generates motion-video footage. It creates an editable storyboard and renders a conventional video file from still images and audio.

## Default Decisions

- Project folder: create all work in a new `video-overview/` folder unless the user names another destination.
- Source ingestion: extract source text into `source_map.json` before writing a storyboard.
- Storyboard source of truth: use `storyboard.json`. Export `storyboard.csv` only for easier human editing.
- Image default: `gpt-image-2`, `2048x1152`, `medium`, PNG, 16:9.
- Audio default: `gpt-4o-mini-tts`, voice `alloy`, MP3.
- Brand/icon default: start with `#2563EB`, then update `agents/openai.yaml` to match the final icon accent after sampling the generated PNG.
- Disclosure: include a brief note in user-facing deliverables when the audio is AI-generated.
- Cohesion default: define one project-level `visual_direction` and `narration_direction` before writing rows, then make every image prompt and spoken segment follow those directions.

## Workflow

1. **Set up project files**
   - Copy or reference the user's documents.
   - Run `scripts/ingest_sources.py <source paths...> --out video-overview/source_map.json`.
   - Read `references/storyboard_schema.md` before drafting or editing `storyboard.json`.

2. **Create the storyboard**
   - Write a concise, source-grounded sequence of rows in `storyboard.json`.
   - Add a project-level `visual_direction` describing palette, composition, rendering style, lighting, recurring motifs, and avoid-list.
   - Add a project-level `narration_direction` describing tone, pacing, audience, voice continuity, and pronunciation notes if needed.
   - Keep each `narration_text` under 4096 characters for the speech endpoint; prefer 20-80 spoken words per row.
   - Write image prompts that describe a single strong 16:9 still image and explicitly inherit the same visual direction. Avoid precise in-image text unless the user explicitly needs it.
   - Keep speech elements aligned: same voice, tone, cadence, audience, and vocabulary level unless the user asks for a format shift.
   - Include `source_refs` for traceability.
   - Run `scripts/create_storyboard.py --validate storyboard.json --csv storyboard.csv`.

3. **Generate media**
   - Require `OPENAI_API_KEY` for live OpenAI API calls.
   - Run `scripts/generate_media.py storyboard.json --media-dir media`.
   - The script writes one image and one audio file per row and skips unchanged assets using hashes in `manifest.json`.
   - Use `--ids row_001,row_004` to regenerate selected rows.

4. **Render the video**
   - Run `scripts/render_video.py storyboard.json --media-dir media --out output/video-overview.mp4`.
   - The renderer uses actual audio durations, creates captions, and concatenates row clips.
   - If only timing, captions, or transitions changed, rerender without regenerating API assets.

5. **Iterate safely**
   - For narration edits, regenerate only affected audio rows.
   - For image prompt edits, regenerate only affected image rows.
   - For timing/caption edits, skip API calls and rerender.
   - Preserve old assets unless the user explicitly wants cleanup.

## Resource Map

- `references/storyboard_schema.md`: required storyboard fields, defaults, and edit rules.
- `scripts/ingest_sources.py`: extract text from TXT, Markdown, JSON, CSV, PDF via `pdftotext`, and DOCX via OOXML.
- `scripts/create_storyboard.py`: validate JSON, normalize defaults, export/import CSV.
- `scripts/generate_media.py`: call OpenAI image and speech endpoints row by row with caching.
- `scripts/render_video.py`: assemble still images, row audio, and captions into MP4 with ffmpeg.
- `scripts/regen_item.py`: patch one row from CLI flags and regenerate only needed assets.
- `assets/icon_prompts.json`: prompts used to generate this skill's icon family.
- `assets/brand.json`: provisional and sampled brand color metadata.

## Quality Checks

- Validate `storyboard.json` before media generation.
- Confirm every row has an image, audio file, and measured audio duration before final render.
- Confirm images are 16:9 when rendered into the final video.
- Confirm the image sequence feels like one designed system, not five unrelated generations.
- Confirm narration sounds like one coherent host/script, not disconnected row snippets.
- Confirm final video duration roughly matches the sum of row audio durations.
- Report any missing external tools (`ffmpeg`, `ffprobe`, `pdftotext`) plainly.
