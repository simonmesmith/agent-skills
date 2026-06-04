---
name: "codex-draw"
description: "Launch and operate a local Codex drawing workspace: a lightweight vector board where the user and Codex share editable scene state through a browser UI, workspace-backed JSON, local API mutations, and SVG export. Use when the user wants to draw, edit, inspect, generate, refine, or export simple vector artifacts such as logos, icons, diagrams, badges, layout sketches, SVG assets, or visual scratchpad scenes."
---

# Codex Draw

Create and edit a local vector drawing board that both the user and Codex can modify. The browser UI is for visual editing; Codex should inspect and mutate the scene through the local API.

## Start The Workspace

Run from the project or artifact workspace:

```bash
python3 skills/codex-draw/scripts/codex_draw.py --workspace drawings
```

The server prints a local URL such as `http://127.0.0.1:8765`. Open that URL in the Codex preview or in-app browser when practical.

Useful options:

```bash
python3 skills/codex-draw/scripts/codex_draw.py --workspace drawings --port 8787
python3 skills/codex-draw/scripts/codex_draw.py --workspace /path/to/workspace/drawings --scene codex-draw-scene-20260604-113000.json
```

When `--scene` is omitted, the server creates a new timestamped scene JSON in the workspace.

The workspace contains:

- `*.json`: saved scene graphs; new drawings use timestamped names such as `codex-draw-scene-20260604-113000.json`
- `exports/*.svg`: exported SVG files
- `assets/`: user or generated image assets that can be inserted into scenes

Generated `drawings/` workspaces are ignored by this repo by default.

## Core Workflow

1. Start the server and open the printed URL.
2. Let the user draw or ask Codex to create objects.
3. Use `GET /api/scene` before making agentic edits.
4. For agent-created drawings, build in visible stages: add the title/background first, then major figures, then details and labels. Use short pauses or browser reloads between larger batches so the user can see the drawing come to life.
5. Mutate the scene with object API calls, then re-read the scene if precision matters.
6. Let the user choose an SVG destination with the UI export button, or use `POST /api/export/svg-content` when an agent needs raw SVG text.
7. Return the active scene path and any important limitations.

Prefer API edits over browser-click automation. Only drive the UI directly when verifying visual behavior or when the user specifically asks.

## Scene API

Replace `{base}` with the printed localhost URL.

```bash
curl {base}/api/scene
curl -X POST {base}/api/objects -H 'Content-Type: application/json' -d '{"type":"rect","x":120,"y":90,"width":220,"height":120,"fill":"#ffffff","stroke":"#1a1c1f","strokeWidth":2}'
curl -X PATCH {base}/api/objects/rect-1 -H 'Content-Type: application/json' -d '{"fill":"#339cff"}'
curl -X DELETE {base}/api/objects/rect-1
curl -X POST {base}/api/selection -H 'Content-Type: application/json' -d '{"selection":["rect-1"]}'
curl -X POST {base}/api/export/svg-content
```

Available endpoints:

- `GET /api/scene`: return full scene JSON.
- `POST /api/scene`: replace the full scene.
- `POST /api/objects`: create an object and assign an ID if missing.
- `PATCH /api/objects/:id`: update an object.
- `DELETE /api/objects/:id`: delete an object.
- `POST /api/objects/reorder`: set object order with `{"order":["id-a","id-b"]}` from back to front.
- `GET /api/selection`: return selected IDs and object data.
- `POST /api/selection`: set selected IDs.
- `POST /api/save`: force-save the active scene JSON.
- `POST /api/new`: save the current scene, switch to a new timestamped scene JSON, and return the blank active scene.
- `POST /api/export/svg-content`: return `{"filename":"...svg","svg":"..."}` without writing to the workspace.
- `POST /api/export/svg-save-as`: open a native macOS save dialog and write the SVG to the user-selected path.
- `POST /api/export/svg`: write an SVG to `exports/` and return its path. Prefer the UI export button for user-facing downloads.

The UI autosaves after object changes and shows the active scene path plus the last save time in the top status line. It also polls scene state, so API mutations appear in the browser shortly after they are saved.

## Scene Model

Scenes are JSON:

```json
{
  "version": 1,
  "revision": 1,
  "canvas": {
    "width": 1200,
    "height": 800,
    "background": "#ffffff"
  },
  "objects": [],
  "selection": []
}
```

Object fields are intentionally explicit and human-readable:

- Common: `id`, `type`, `name`, `x`, `y`, `width`, `height`, `rotation`, `fill`, `stroke`, `strokeWidth`, `opacity`, `locked`, `visible`, `zIndex`
- `rect`: uses `x`, `y`, `width`, `height`
- `ellipse`: uses `x`, `y`, `width`, `height`
- `line` and `arrow`: use `x`, `y`, `x2`, `y2`
- `text`: uses `x`, `y`, `text`, `fontSize`, `fontFamily`, `fill`
- `image`: uses `x`, `y`, `width`, `height`, `href`

Keep coordinates in canvas pixels. Keep object order stable by setting `zIndex` values from back to front.

## Visual Direction

Codex Draw should feel like a small Codex utility, not a generic SaaS tool. The app uses compact Codex theme variables with fallbacks:

- `--codex-base-accent`
- `--codex-base-surface`
- `--codex-base-ink`

The UI should stay quiet: crisp borders, compact icon-like tool buttons, clear focus states, no landing page, no decorative gradients, and no marketing copy.

## Validation

After editing this skill, run:

```bash
python3 -m py_compile skills/codex-draw/scripts/codex_draw.py
python3 skills/codex-draw/scripts/codex_draw.py --workspace /tmp/codex-draw-test --port 8765
```

In another shell, verify:

```bash
curl http://127.0.0.1:8765/api/scene
curl -X POST http://127.0.0.1:8765/api/objects -H 'Content-Type: application/json' -d '{"type":"ellipse","x":120,"y":90,"width":120,"height":120,"fill":"#339cff"}'
curl -X POST http://127.0.0.1:8765/api/export/svg-content
```

Open the URL in the Codex in-app browser when practical and confirm the canvas is visible, object creation works, API-created objects appear, the right panel scrolls independently for long object lists, and the UI export button lets the user choose a destination.
