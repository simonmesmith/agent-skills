---
name: "codex-mood-board"
description: "Build lightweight mood boards, visual territories, creative directions, style boards, reference boards, art-direction boards, campaign mood explorations, brand look-and-feel explorations, image batches for visual ideation, more-like-this rounds from attached images, and follow-up mood-board iterations from annotated or attached images. Use this standalone skill instead of Creative Production when the user asks Codex to create, generate, explore, iterate, or review mood-board-style image directions."
---

# Codex Mood Board

Create compact local mood boards from generated image batches. This skill is standalone: do not use Creative Production.

## Workflow

1. Ask what the user wants a mood board for.
2. Collect a lightweight creative brief:
   - brief or subject
   - goal
   - target audience
   - desired mood or visual territories
   - must include
   - avoid
   - number of images
3. Default to 6 images when no count is specified. Accept follow-up requests for more images, up to 25 images in one batch.
4. Generate one distinct prompt per image. Do not use `n` as a substitute for separate creative directions.
5. Use the OpenAI Image API CLI path via `scripts/generate_mood_board.py`; do not use the built-in `image_gen` tool for this workflow.
6. Require `OPENAI_API_KEY` locally. If the key is missing, explain that the user must set it in their shell or Codex environment; never ask them to paste it into chat. The script uses the system imagegen CLI, which requires the Python `openai` SDK for real API calls; if the active environment is missing it and `uv` is available, the script automatically retries with `uv run --with openai` and points `UV_CACHE_DIR` at `/private/tmp/uv-cache-codex-mood-board` unless the environment already sets it.
7. Save outputs in a durable local output folder, then return the `index.html` path and tell the user to open it in Codex preview/browser.
8. The preview header should say: `Use Ctrl + Click to add images and annotations to the thread.`
9. Show short mood or direction names under each image, not full prompts. Store those names in each batch as `mood_names`; the user can ask for the underlying prompt when needed.
10. Use Codex theme variables for preview colors: `--codex-base-accent`, `--codex-base-surface`, and `--codex-base-ink`, with sensible fallbacks. Do not inject Codex theme colors into generated image prompts unless the user explicitly asks for that palette.

## Defaults

- Model: `gpt-image-2`
- Quality: `low`
- Size: `1024x1024`
- Output format: `png`
- Image count: `6`
- Maximum single batch: `25`
- Batch concurrency: match image count up to 25 unless the user asks for a lower value

## Script Use

Create a JSON spec and run:

```bash
python3 skills/codex-mood-board/scripts/generate_mood_board.py \
  --spec mood-board-spec.json \
  --output-dir mood-boards/codex-mood-board
```

The spec may include:

```json
{
  "title": "Mood Board",
  "brief": "Premium neighborhood coffee subscription",
  "goal": "Explore warm, credible visual directions for a landing page",
  "target_audience": "Busy urban professionals",
  "territories": ["morning ritual", "local craft", "quiet focus"],
  "mood_names": ["Morning Ritual", "Local Craft", "Quiet Focus"],
  "reference_images": ["references/brand-photo.png"],
  "must_include": ["coffee", "human warmth", "real interiors"],
  "avoid": ["generic stock-photo gloss", "visible brand logos"],
  "image_count": 6,
  "model": "gpt-image-2",
  "quality": "low",
  "size": "1024x1024",
  "output_format": "png"
}
```

For validation without API calls, use `--mock-images`. For API payload checks without image files, use `--dry-run`.
After HTML/CSS-only script changes, use `--rebuild-html --output-dir <board-folder>` to rebuild the preview from the existing manifest without creating a new batch.

Use the default `mood-boards/codex-mood-board` folder for local experiments. The repo ignores `mood-boards/`, so generated batches, previews, and manifests stay out of git.

If generation fails after creating a partial batch folder, rerun the same command against the same output directory. The script skips existing `batch-###-*` folders and writes the retry as the next batch, preserving the failed folder for diagnostics.

For multiple mood boards in one project, keep one output folder per board, for example:

```bash
python3 skills/codex-mood-board/scripts/generate_mood_board.py \
  --spec icon-directions.json \
  --output-dir mood-boards/icon-directions

python3 skills/codex-mood-board/scripts/generate_mood_board.py \
  --spec homepage-style.json \
  --output-dir mood-boards/homepage-style
```

Leave the title as `Mood Board` for a single board. When a project has several boards, set `title` in each spec, such as `Icon Direction Mood Board` or `Homepage Style Mood Board`.

## Follow-up Rounds

For user feedback such as "combine images 2 and 5" or "more like this but warmer", create a new spec with `follow_up` and run the script again against the same output folder. The script creates the next batch and prepends it to `index.html`.

If the user attaches or names reference images, default to using them as creative guidance in the prompt and add them to `reference_images`. This keeps the fast `generate-batch` path.

Only send images as true API references when the user explicitly asks for the images to be used as references, says the result should be based on attached images, or approves that tradeoff. In that case set `send_reference_images: true`. Warn the user that this uses the image edit/reference path, runs one API call per tile, and will be slower because it cannot use `generate-batch`.

Keep true API references selective: default to at most 4 images per batch even though the API can accept up to 16. If the user explicitly needs more, set `max_reference_images` up to 16 and explain that too many references can dilute the direction or increase latency/cost.

Reference image specs can be strings or objects:

```json
{
  "reference_images": [
    "references/favorite-1.png",
    {"path": "references/favorite-2.jpg", "role": "color and composition"}
  ],
  "send_reference_images": true,
  "max_reference_images": 4
}
```

After each batch, invite the user to use Codex image attachment or annotation features for the next round, for example:

- "Combine images 2 and 5."
- "Make more like this one but warmer."
- "Use these three attached images as references."

## Validation

After editing this skill, run:

```bash
python3 -m py_compile skills/codex-mood-board/scripts/generate_mood_board.py
python3 skills/codex-mood-board/scripts/generate_mood_board.py --check-env
python3 skills/codex-mood-board/scripts/generate_mood_board.py --spec /path/to/spec.json --output-dir /tmp/codex-mood-board-test --mock-images
```

Use a second `--mock-images` run against the same output folder to verify `Batch 2` appears above `Batch 1`.
