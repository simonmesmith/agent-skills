---
name: polished-presentations
description: "Create polished executive or client presentation decks in Codex through a staged workflow: optional research, user direction, narrative outline, subagent critique, wireframe, mood boards, and final full-slide image design. Use when the user asks to make a polished presentation, turn research, notes, or dictated direction into a deck, create an executive/client deck, build a deck with mood boards, or design slides using image generation. Requires the imagegen skill for mood boards and final slide images."
---

# Polished Presentations

## Dependency

Load and follow the `imagegen` skill before generating mood boards or final slide designs.

Use the built-in image generation path by default unless the user explicitly asks for the imagegen CLI fallback.

## Core Principle

Separate content creation from design production.

Do not create content and final design at the same time. First resolve the research, direction, narrative, critique, and wireframe. Only then generate mood boards and final slide images.

## Workspace Structure

If the workspace already has a clear versioning convention, follow it. Otherwise create versioned folders:

```text
sources/
research/
v1/
vN/
```

Use the next available version folder. Do not overwrite an existing version unless the user explicitly asks.

- `sources/`: original or user-provided files such as RFPs, briefs, brand guidelines, PDFs, docs, spreadsheets, datasets, exported threads, and reusable brand assets.
- `research/`: reusable Codex-created source notes, source indexes, extracted quotes, Slack findings, web findings, and file summaries.
- `vN/`: version-specific synthesis and deck artifacts.

Each version should usually contain:

```text
vN/
  source_manifest.md
  research_brief.md
  direction.md
  outline.md
  critique.md
  wireframe.pptx
  mood_boards/
  designed_slides/
  final_deck.pptx
```

Create `vN/source_manifest.md` listing the exact files, research notes, links, and datasets used for that version. Keep reusable source files in `sources/`; do not duplicate large source files into every version unless the source itself must be snapshotted.

Always save the content outline as a standalone Markdown file at `vN/outline.md` before critique and wireframing.

## Workflow

### 1. Research

Research is optional. If the user says no research is needed, proceed from their direction, notes, or dictated thoughts.

When research is needed, gather relevant source material from the user-provided context, local files, connected tools, Slack channels, web sources, docs, data, or notes.

Save reusable findings in `research/`. Save a version-specific synthesis in `vN/research_brief.md`.

Track:

- facts and examples that should inform the deck
- useful quotes or source excerpts
- open questions, weak evidence, and assumptions
- source links, file paths, dates, and relevant context

Keep research notes separate from slide copy.

### 2. Direction

Capture the user's point of view in `vN/direction.md`.

Treat dictation or messy idea dumps as a strong input format. Preserve the user's intent, emphasis, and language, then organize it into usable direction.

Include:

- target audience and what they care about
- purpose of the presentation
- desired audience reaction or decision
- thesis or emerging point of view
- tone, stakes, constraints, and must-say points
- what to avoid

If direction is thin, ask concise questions before outlining unless the user has asked you to proceed with assumptions.

### 3. Outline

Create `vN/outline.md` as the working source of truth for the deck's content.

Include:

- title or working title
- audience and purpose
- thesis
- slide-by-slide outline
- each slide's role in the story
- audience-facing slide content
- evidence or data needed for the slide
- notes for presenter emphasis, when helpful
- open questions or unresolved content risks

Keep the outline audience-facing. Do not include meta-content that explains the workflow, the prompt, the AI, the research process, or the fact that a deck is being created unless that is intended for the actual presentation audience.

### 4. Critique

Critique the outline before wireframing.

If the environment supports subagents, use them. Keep critique subagents clean and objective: give them the outline, audience, purpose, and necessary source context, but do not pass the full working conversation, your intended revisions, or leading conclusions. Ask each subagent for independent critique rather than validation.

Cover exactly these perspectives:

- Narrative / story flow: clear arc, tension, payoff, logical progression, and slide-to-slide momentum. A Nancy Duarte-style presentation strategist perspective is useful here.
- Content and conciseness: usefulness, brevity, non-repetition, presenter-friendliness, and whether each slide earns its place. A Strunk & White or William Zinsser-style clarity perspective is useful here.
- Target audience resonance: relevance, credibility, likely objections, and whether the intended audience will care.

If subagents are unavailable, simulate these separate reviewer perspectives yourself and state that the critique was simulated locally.

Save critique notes and resulting changes in `vN/critique.md`. Then revise `vN/outline.md` before proceeding.

### 5. Wireframe

Create a simple 16:9 content wireframe deck at `vN/wireframe.pptx`.

Use minimal design only: clean layouts, black text when reasonable, simple shapes, and clear hierarchy. The wireframe is for content control, not final visual polish.

Requirements:

- Include only audience-facing content.
- Make each slide content-complete enough to guide image generation.
- Use concise slide copy intended for presentation, not reading.
- Include clear chart titles, legends, axis labels, and data labels on every chart.
- Add labels to key values and data points that must survive final image generation.
- Avoid ambiguous charts, unlabeled axes, and decorative design decisions that should be handled later.

### 6. Mood Boards

Load the `imagegen` skill.

Generate three meaningfully different mood board directions in `vN/mood_boards/`. Each direction should be a distinct territory relevant to the subject matter, not three minor variations of the same style.

Each direction should explore:

- style and visual language
- color palette
- typography feel
- composition and layout rhythm
- imagery, texture, and atmosphere
- emotional tone

Use brand guidelines, brand colors, required fonts, palette constraints, or user-provided references when available. Ask the user to pick, reject, or combine directions before final slide design unless they have already given clear design direction.

### 7. Design

Load and follow the `imagegen` skill.

For each wireframe slide, generate one full-bleed 16:9 image in the chosen mood board style. Save final selected images in `vN/designed_slides/`.

Default design rule:

Generate the entire slide as a single image, including background, text, charts, labels, layout, and visual design. Do not generate only a background. Do not overlay live PowerPoint text or charts unless the user explicitly chooses a hybrid editable workflow.

The image must be a true 16:9 slide image, full bleed edge-to-edge, with no white border, margin, frame, mat, canvas edge, screenshot frame, or padding around the slide.

Each slide image prompt should include:

- production slide number for tracking and the visible slide title
- exact audience-facing text to render
- chart data and required labels, if any
- the chosen mood board/style direction
- full-bleed 16:9 slide composition
- cohesion with the rest of the deck
- variation from adjacent slides so the deck is not visually monotonous

If the user does not want visible slide numbers, do not render slide numbers on the slide image. Still track slide numbers in filenames and production notes.

Compile the designed images into `vN/final_deck.pptx` as image-only full-slide slides.

## Failure Modes To Prevent

- Do not write content and design at the same time.
- Do not generate final slide backgrounds and then add live text by default.
- Do not mix generated imagery with live text or charts unless the user explicitly asks for a hybrid editable deck.
- Do not include fourth-wall content such as "this slide explains," "the deck will show," "based on the research," "AI-generated," or notes about the workflow unless the presentation audience is supposed to see that.
- Do not omit data labels from wireframe charts.
- Do not leave charts, axes, legends, or key values ambiguous before image generation.
- Do not allow white borders, margins, frames, mats, or padding around generated slide images; every final slide image must be true 16:9 full bleed.
- Do not make every final slide composition look the same; enforce cohesion with variation.
- Do not overwrite prior versions or source artifacts.
- Do not leave project-bound generated images only under the default imagegen output location; move or copy final assets into the active version folder.

## Tradeoffs To Note

Image-only final decks can look much more polished, but they are harder to edit, less suitable for object-level animation, and can produce large files.

If the user needs a highly editable final deck, offer a hybrid workflow as an explicit alternative: generated backgrounds with editable foreground text/charts. This workflow has not been optimized here, so use it only when editability is more important than image-native polish.
