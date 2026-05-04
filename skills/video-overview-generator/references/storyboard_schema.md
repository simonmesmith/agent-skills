# Storyboard Schema

`storyboard.json` is the source of truth. Keep row IDs stable so media can be regenerated selectively.

## Top-Level Shape

```json
{
  "title": "Short video title",
  "description": "Optional project note",
  "visual_direction": "One cohesive art direction for every generated image.",
  "narration_direction": "One cohesive voice, tone, pacing, and audience direction.",
  "defaults": {
    "image_model": "gpt-image-2",
    "image_size": "2048x1152",
    "image_quality": "medium",
    "audio_model": "gpt-4o-mini-tts",
    "voice": "alloy",
    "transition": "fade"
  },
  "rows": []
}
```

## Row Fields

Required:

- `id`: stable unique row ID, for example `row_001`.
- `section_title`: short grouping label.
- `narration_text`: text to speak. Keep under 4096 characters.
- `image_prompt`: prompt for one static 16:9 image.

Recommended:

- `source_refs`: array of source IDs or filenames supporting the row.
- `caption_text`: caption/subtitle text. Defaults to `narration_text`.
- `image_style`: short row-specific addition to the project-level `visual_direction`.
- `transition`: `cut`, `fade`, or `none`.
- `duration_override`: optional number of seconds. Usually omit and use audio duration.

Generated/managed by scripts:

- `audio_path`
- `audio_duration`
- `image_path`
- `content_hash`
- `status`

## Edit Rules

- If `narration_text`, `voice`, or `audio_model` changes, regenerate that row's audio.
- If `image_prompt`, `image_style`, `image_model`, `image_size`, or `image_quality` changes, regenerate that row's image.
- If only captions, timing, title, or transition changes, rerender the video without API regeneration.
- Do not renumber row IDs during edits unless the user asks for a structural rewrite.

## Prompt Guidance

Image prompts should describe the visible scene, not the script. Use one strong visual idea per row. Favor editorial, documentary, diagrammatic, or clean conceptual stills depending on the source. Avoid asking for exact text in the image; put text in captions instead.

Before writing rows, define a project-level visual system:

- Palette and accent colors.
- Rendering mode, such as editorial 3D, documentary photography, clean diagrams, or premium software UI illustration.
- Composition rules, such as centered object on dark field, wide cinematic workspace, or consistent timeline motif.
- Lighting and texture rules.
- Recurring motifs that can repeat across rows.
- Avoid-list for clutter, brand names, text, watermarks, and inconsistent styles.

Then make each row prompt inherit that system and vary only the scene content. The finished output should feel like one designed sequence, not unrelated images.

Before generating audio, define a project-level narration system:

- Audience.
- Tone.
- Pacing.
- Voice.
- Pronunciation notes.
- Repeated phrases or vocabulary to keep consistent.

Every `narration_text` row should sound like the same host continuing one script.
