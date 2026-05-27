# Agent Guidance

This repo contains Simon Smith's personal Codex skills. Treat each skill as a reusable tool that should be clear, tested, and easy for future agents to run.

## Repo Conventions

- Skills live in `skills/<skill-name>/`.
- The primary documentation for a skill is its `SKILL.md`.
- When adding or removing a skill, update the skill list in the root `README.md`.
- Avoid adding per-skill `README.md` files unless the user explicitly asks for one; use GitHub issues for roadmaps and open work.
- Keep repo-level docs concise.
- Generated meeting recordings, logs, caches, build outputs, and raw debug artifacts should stay out of git.

## Working On Skills

- Read the relevant `SKILL.md` before changing behavior.
- Prefer existing scripts and helper binaries over creating parallel one-off tools.
- Keep changes scoped to the skill being edited unless the user asks for cross-skill cleanup.
- Preserve plain-text/source artifacts when a skill also renders a polished preview.
- Update `SKILL.md` when behavior, commands, outputs, or maintenance expectations change.
- Track future work in GitHub issues with a `skill:<skill-name>` label.

## Verification

- Run the smallest meaningful checks for the changed skill.
- For Python scripts, run `python3 -m py_compile` on edited files.
- For Swift helpers, run `swift build -c release` from the helper directory when Swift files change.
- For browser-facing previews, verify the local URL in the Codex in-app browser when practical.

## Git

- Do not push until the user has tested or explicitly approved pushing.
- Keep commits focused and name the skill or behavior changed.
- Never revert user changes unless the user explicitly asks.
