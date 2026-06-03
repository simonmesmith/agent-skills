#!/usr/bin/env python3
"""Generate mood-board batches and maintain a local HTML preview."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import zlib
from pathlib import Path
from typing import Any


DEFAULT_COUNT = 6
MAX_BATCH = 25
DEFAULT_MAX_REFERENCE_IMAGES = 4
API_MAX_REFERENCE_IMAGES = 16
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_QUALITY = "low"
DEFAULT_SIZE = "1024x1024"
DEFAULT_FORMAT = "png"
DEFAULT_UV_CACHE_DIR = Path("/private/tmp/uv-cache-codex-mood-board")
TITLE = "Mood Board"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", help="JSON mood-board spec")
    parser.add_argument("--output-dir", default="mood-boards/codex-mood-board", help="Durable output folder")
    parser.add_argument("--imagegen-cli", help="Path to imagegen scripts/image_gen.py")
    parser.add_argument("--dry-run", action="store_true", help="Create jobs and run CLI dry-run without images")
    parser.add_argument("--mock-images", action="store_true", help="Create deterministic placeholder PNGs for validation")
    parser.add_argument("--check-env", action="store_true", help="Check local API key and CLI availability")
    parser.add_argument("--rebuild-html", action="store_true", help="Rebuild index.html from an existing manifest without creating a batch")
    return parser.parse_args()


def fail(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        fail(f"spec file not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"spec file is not valid JSON: {exc}")
    if not isinstance(data, dict):
        fail("spec must be a JSON object")
    return data


def imagegen_cli_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "skills" / ".system" / "imagegen" / "scripts" / "image_gen.py"


def check_env(cli: Path, require_key: bool) -> None:
    if not cli.exists():
        fail(f"imagegen CLI not found at {cli}")
    if require_key and not os.environ.get("OPENAI_API_KEY"):
        fail(
            "OPENAI_API_KEY is not set locally. Set it in your shell or Codex environment; "
            "do not paste it into chat."
        )


def slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or fallback


def as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def clamp_count(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_COUNT
    try:
        count = int(value)
    except (TypeError, ValueError):
        fail("image_count must be an integer")
    if count < 1:
        fail("image_count must be at least 1")
    if count > MAX_BATCH:
        fail(f"image_count cannot exceed {MAX_BATCH} for a single batch")
    return count


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"title": TITLE, "batches": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("title", TITLE)
    data.setdefault("batches", [])
    return data


def next_batch_number(manifest: dict[str, Any]) -> int:
    batches = manifest.get("batches", [])
    if not batches:
        return 1
    return max(int(batch.get("batch_number", 0)) for batch in batches) + 1


def next_available_batch_number(manifest: dict[str, Any], output_dir: Path) -> int:
    batch_number = next_batch_number(manifest)
    existing_numbers = []
    for path in output_dir.glob("batch-[0-9][0-9][0-9]-*"):
        if not path.is_dir():
            continue
        try:
            existing_numbers.append(int(path.name.split("-", 2)[1]))
        except (IndexError, ValueError):
            continue
    if existing_numbers:
        batch_number = max(batch_number, max(existing_numbers) + 1)
    return batch_number


def reference_entries(spec: dict[str, Any]) -> list[Any]:
    value = spec.get("reference_images")
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def reference_label(entry: Any) -> str:
    if isinstance(entry, dict):
        path = str(entry.get("path") or entry.get("image") or "").strip()
        role = str(entry.get("role") or entry.get("note") or "").strip()
        if role and path:
            return f"{path} ({role})"
        return path or role
    return str(entry).strip()


def reference_path(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("path") or entry.get("image") or "").strip()
    return str(entry).strip()


def max_reference_images(spec: dict[str, Any]) -> int:
    raw = spec.get("max_reference_images", DEFAULT_MAX_REFERENCE_IMAGES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        fail("max_reference_images must be an integer")
    if value < 1:
        fail("max_reference_images must be at least 1")
    if value > API_MAX_REFERENCE_IMAGES:
        fail(f"max_reference_images cannot exceed the API limit of {API_MAX_REFERENCE_IMAGES}")
    return value


def resolve_reference_images(spec: dict[str, Any], spec_path: Path) -> list[Path]:
    entries = reference_entries(spec)
    if not entries:
        return []
    cap = max_reference_images(spec)
    if len(entries) > cap:
        fail(
            f"reference_images includes {len(entries)} images, but this skill is capped at {cap} for this run. "
            f"Use fewer references or set max_reference_images up to {API_MAX_REFERENCE_IMAGES}."
        )

    resolved: list[Path] = []
    for entry in entries:
        raw_path = reference_path(entry)
        if not raw_path:
            fail("each reference image must be a path string or an object with a path field")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            candidate = spec_path.parent / path
            path = candidate if candidate.exists() else Path.cwd() / path
        if not path.exists():
            fail(f"reference image not found: {path}")
        if path.stat().st_size > 50 * 1024 * 1024:
            fail(f"reference image exceeds the 50MB API limit: {path}")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            fail(f"reference image should be PNG, JPG, JPEG, or WEBP: {path}")
        resolved.append(path)
    return resolved


def send_reference_images(spec: dict[str, Any]) -> bool:
    return bool(spec.get("send_reference_images") or spec.get("attach_reference_images"))


def format_timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    local = parsed.astimezone()
    return local.strftime("%b %-d, %Y, %-I:%M %p %Z")


def build_prompts(spec: dict[str, Any], count: int, *, reference_mode: bool = False) -> list[str]:
    explicit = as_list(spec.get("prompts"))
    if explicit:
        if len(explicit) < count:
            fail("spec.prompts has fewer prompts than image_count")
        return explicit[:count]

    brief = str(spec.get("brief") or spec.get("subject") or "a visual direction").strip()
    goal = str(spec.get("goal") or "explore a useful mood-board direction").strip()
    audience = str(spec.get("target_audience") or "the intended audience").strip()
    territories = as_list(spec.get("territories") or spec.get("desired_mood"))
    must = as_list(spec.get("must_include"))
    avoid = as_list(spec.get("avoid"))
    refs = [label for label in (reference_label(entry) for entry in reference_entries(spec)) if label]
    follow_up = str(spec.get("follow_up") or "").strip()

    default_territories = [
        "warm human moment",
        "clean editorial composition",
        "tactile material detail",
        "environmental context",
        "bold campaign energy",
        "quiet premium restraint",
        "unexpected color story",
        "close-up sensory detail",
        "wide establishing scene",
        "social-ready still life",
    ]
    if not territories:
        territories = default_territories

    prompts: list[str] = []
    for index in range(count):
        territory = territories[index % len(territories)]
        must_line = "; ".join(must) if must else "visual specificity, believable details, no text"
        avoid_line = "; ".join(avoid) if avoid else "logos, watermarks, generic stock-photo gloss, illegible text"
        ref_line = ""
        if refs:
            ref_line = (
                " Use these reference notes or files as visual guidance for style, composition, motifs, and constraints: "
                + "; ".join(refs)
                + ". Do not copy them exactly unless the user explicitly asks for a close variation."
            )
            if not reference_mode:
                ref_line += " Treat these as prompt guidance; do not assume the images are attached to the API request."
        follow_line = f" Follow-up direction: {follow_up}." if follow_up else ""
        prompt = f"""
        Use case: ads-marketing
        Asset type: mood-board tile {index + 1} of {count}
        Primary request: Create a distinct mood-board image for {brief}.
        Goal: {goal}
        Target audience: {audience}
        Visual territory: {territory}
        Composition/framing: make this tile meaningfully different from the others; strong standalone art-direction reference.
        Lighting/mood: aligned to the territory, polished but exploratory.
        Constraints: must include {must_line}.{follow_line}{ref_line}
        Avoid: {avoid_line}.
        No captions, no visible UI, no watermark, no brand logos unless explicitly requested.
        """
        prompts.append(textwrap.dedent(prompt).strip())
    return prompts


def title_from_text(value: str) -> str:
    small_words = {"a", "an", "and", "as", "for", "in", "of", "or", "the", "to", "with"}
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words:
        return ""
    titled = []
    for index, word in enumerate(words[:5]):
        lower = word.lower()
        if index > 0 and lower in small_words:
            titled.append(lower)
        else:
            titled.append(lower.capitalize())
    return " ".join(titled)


def build_mood_names(spec: dict[str, Any], count: int) -> list[str]:
    explicit = as_list(spec.get("mood_names") or spec.get("names") or spec.get("titles"))
    if explicit:
        names = explicit[:count]
        while len(names) < count:
            names.append(f"Direction {len(names) + 1}")
        return names

    territories = as_list(spec.get("territories") or spec.get("desired_mood"))
    if territories:
        return [title_from_text(territories[index % len(territories)]) or f"Direction {index + 1}" for index in range(count)]

    return [f"Direction {index}" for index in range(1, count + 1)]


def write_jobs(
    prompts: list[str],
    batch_dir: Path,
    model: str,
    quality: str,
    size: str,
    output_format: str,
) -> Path:
    jobs_path = batch_dir / "jobs.jsonl"
    with jobs_path.open("w", encoding="utf-8") as f:
        for index, prompt in enumerate(prompts, start=1):
            job = {
                "prompt": prompt,
                "model": model,
                "quality": quality,
                "size": size,
                "output_format": output_format,
                "n": 1,
                "out": f"image-{index:02d}.{output_format}",
            }
            f.write(json.dumps(job, ensure_ascii=False) + "\n")
    return jobs_path


def cli_command(python_command: list[str], cli: Path, jobs_path: Path, batch_dir: Path, concurrency: int, dry_run: bool) -> list[str]:
    command = [
        *python_command,
        str(cli),
        "generate-batch",
        "--input",
        str(jobs_path),
        "--out-dir",
        str(batch_dir),
        "--concurrency",
        str(concurrency),
        "--no-augment",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def command_env(command: list[str]) -> dict[str, str]:
    env = os.environ.copy()
    if command and Path(command[0]).name == "uv":
        env.setdefault("UV_CACHE_DIR", str(DEFAULT_UV_CACHE_DIR))
    return env


def run_command(command: list[str], batch_dir: Path, label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=command_env(command),
    )
    (batch_dir / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (batch_dir / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    return result


def run_cli(cli: Path, jobs_path: Path, batch_dir: Path, concurrency: int, dry_run: bool) -> None:
    first = run_command(
        cli_command([sys.executable], cli, jobs_path, batch_dir, concurrency, dry_run),
        batch_dir,
        "imagegen",
    )
    if first.returncode == 0:
        return

    sdk_missing = "openai SDK not installed" in first.stderr
    uv = shutil.which("uv")
    if sdk_missing and uv:
        fallback = run_command(
            cli_command([uv, "run", "--with", "openai", "python"], cli, jobs_path, batch_dir, concurrency, dry_run),
            batch_dir,
            "imagegen-uv",
        )
        if fallback.returncode == 0:
            return
        fail(f"imagegen CLI uv fallback failed with exit code {fallback.returncode}. See {batch_dir / 'imagegen-uv.stderr.txt'}")

    if sdk_missing:
        fail(
            "openai SDK is not installed and `uv` was not found for automatic dependency isolation. "
            "Install with `uv pip install openai` or run via `uv run --with openai`."
        )
    fail(f"imagegen CLI failed with exit code {first.returncode}. See {batch_dir / 'imagegen.stderr.txt'}")


def edit_command(
    python_command: list[str],
    cli: Path,
    *,
    prompt: str,
    reference_images: list[Path],
    out_path: Path,
    model: str,
    quality: str,
    size: str,
    output_format: str,
    dry_run: bool,
) -> list[str]:
    command = [
        *python_command,
        str(cli),
        "edit",
        "--prompt",
        prompt,
        "--out",
        str(out_path),
        "--model",
        model,
        "--quality",
        quality,
        "--size",
        size,
        "--output-format",
        output_format,
        "--n",
        "1",
        "--no-augment",
    ]
    for image in reference_images:
        command.extend(["--image", str(image)])
    if dry_run:
        command.append("--dry-run")
    return command


def run_edit_job(
    *,
    cli: Path,
    prompt: str,
    reference_images: list[Path],
    out_path: Path,
    model: str,
    quality: str,
    size: str,
    output_format: str,
    dry_run: bool,
    batch_dir: Path,
    label: str,
) -> None:
    first = run_command(
        edit_command(
            [sys.executable],
            cli,
            prompt=prompt,
            reference_images=reference_images,
            out_path=out_path,
            model=model,
            quality=quality,
            size=size,
            output_format=output_format,
            dry_run=dry_run,
        ),
        batch_dir,
        label,
    )
    if first.returncode == 0:
        return

    sdk_missing = "openai SDK not installed" in first.stderr
    uv = shutil.which("uv")
    if sdk_missing and uv:
        fallback = run_command(
            edit_command(
                [uv, "run", "--with", "openai", "python"],
                cli,
                prompt=prompt,
                reference_images=reference_images,
                out_path=out_path,
                model=model,
                quality=quality,
                size=size,
                output_format=output_format,
                dry_run=dry_run,
            ),
            batch_dir,
            f"{label}-uv",
        )
        if fallback.returncode == 0:
            return
        raise RuntimeError(f"imagegen edit uv fallback failed with exit code {fallback.returncode}. See {batch_dir / (label + '-uv.stderr.txt')}")

    if sdk_missing:
        raise RuntimeError(
            "openai SDK is not installed and `uv` was not found for automatic dependency isolation. "
            "Install with `uv pip install openai` or run via `uv run --with openai`."
        )
    raise RuntimeError(f"imagegen edit failed with exit code {first.returncode}. See {batch_dir / (label + '.stderr.txt')}")


def run_reference_edit_batch(
    cli: Path,
    prompts: list[str],
    reference_images: list[Path],
    batch_dir: Path,
    concurrency: int,
    dry_run: bool,
    model: str,
    quality: str,
    size: str,
    output_format: str,
) -> None:
    errors: list[str] = []
    workers = min(concurrency, len(prompts))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []
        for index, prompt in enumerate(prompts, start=1):
            futures.append(
                executor.submit(
                    run_edit_job,
                    cli=cli,
                    prompt=prompt,
                    reference_images=reference_images,
                    out_path=batch_dir / f"image-{index:02d}.{output_format}",
                    model=model,
                    quality=quality,
                    size=size,
                    output_format=output_format,
                    dry_run=dry_run,
                    batch_dir=batch_dir,
                    label=f"imagegen-edit-{index:02d}",
                )
            )
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - collect all job failures.
                errors.append(str(exc))
    if errors:
        fail("; ".join(errors))


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")


def make_mock_png(path: Path, seed: int) -> None:
    width = 512
    height = 512
    colors = [
        (240, 108, 79),
        (255, 209, 102),
        (91, 192, 190),
        (61, 64, 91),
        (244, 241, 222),
        (35, 42, 52),
    ]
    c1 = colors[seed % len(colors)]
    c2 = colors[(seed + 2) % len(colors)]
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            mix = (x + y + seed * 31) / (width + height + seed * 31)
            r = int(c1[0] * (1 - mix) + c2[0] * mix)
            g = int(c1[1] * (1 - mix) + c2[1] * mix)
            b = int(c1[2] * (1 - mix) + c2[2] * mix)
            raw.extend([r, g, b])
    png = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def write_mock_images(batch_dir: Path, count: int, output_format: str) -> None:
    if output_format != "png":
        fail("--mock-images only supports png output_format")
    for index in range(1, count + 1):
        make_mock_png(batch_dir / f"image-{index:02d}.png", index)


def collect_outputs(batch_dir: Path, count: int, output_format: str) -> list[str]:
    outputs = []
    for index in range(1, count + 1):
        path = batch_dir / f"image-{index:02d}.{output_format}"
        if path.exists():
            outputs.append(path.name)
    return outputs


def copy_favicon(output_dir: Path) -> str | None:
    skill_dir = Path(__file__).resolve().parents[1]
    icon = skill_dir / "assets" / "mood-board-icon.png"
    if not icon.exists():
        return None
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(exist_ok=True)
    target = assets_dir / "mood-board-icon.png"
    shutil.copyfile(icon, target)
    return target.relative_to(output_dir).as_posix()


def render_html(manifest: dict[str, Any], output_dir: Path) -> None:
    favicon = copy_favicon(output_dir)
    board_title = str(manifest.get("title") or TITLE)
    batches = sorted(manifest.get("batches", []), key=lambda b: int(b["batch_number"]), reverse=True)
    batch_html = []
    for batch in batches:
        items = []
        for idx, rel_path in enumerate(batch.get("output_paths", []), start=1):
            mood_names = batch.get("mood_names", [])
            mood_name = mood_names[idx - 1] if idx - 1 < len(mood_names) else f"Direction {idx}"
            items.append(
                f"""
                <figure class="tile">
                  <div class="image-frame">
                    <img src="{html.escape(rel_path)}" alt="Batch {batch['batch_number']} image {idx}: {html.escape(mood_name)}">
                  </div>
                  <figcaption>
                    <strong>{idx}</strong>
                    <span>{html.escape(mood_name)}</span>
                  </figcaption>
                </figure>
                """
            )
        batch_html.append(
            f"""
            <section class="batch" id="batch-{batch['batch_number']}">
              <div class="batch-head">
                <h2>Batch {batch['batch_number']}</h2>
                <p>{html.escape(format_timestamp(batch.get('timestamp')))} · {len(batch.get('output_paths', []))} images · {html.escape(batch.get('model', ''))} · {html.escape(batch.get('quality', ''))}</p>
              </div>
              <div class="grid">
                {''.join(items)}
              </div>
            </section>
            """
        )

    favicon_tag = f'<link rel="icon" href="{html.escape(favicon)}" type="image/png">' if favicon else ""
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(board_title)}</title>
  {favicon_tag}
  <style>
    :root {{
      color-scheme: light dark;
      --board-accent: var(--codex-base-accent, #339cff);
      --board-surface: var(--codex-base-surface, #ffffff);
      --board-ink: var(--codex-base-ink, #1a1c1f);
      --board-muted: color-mix(in srgb, var(--board-ink) 52%, var(--board-surface));
      --board-line: color-mix(in srgb, var(--board-ink) 14%, transparent);
      --board-tile-surface: color-mix(in srgb, var(--board-surface) 94%, var(--board-ink));
      --board-image-matte: color-mix(in srgb, var(--board-ink) 5%, var(--board-surface));
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --board-surface: var(--codex-base-surface, #181818);
        --board-ink: var(--codex-base-ink, #ffffff);
        --board-muted: color-mix(in srgb, var(--board-ink) 62%, var(--board-surface));
        --board-line: color-mix(in srgb, var(--board-ink) 22%, transparent);
        --board-tile-surface: color-mix(in srgb, var(--board-surface) 88%, var(--board-ink));
        --board-image-matte: color-mix(in srgb, var(--board-ink) 12%, var(--board-surface));
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--board-surface);
      color: var(--board-ink);
      font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 18px;
      border-bottom: 1px solid var(--board-line);
    }}
    h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: 0;
    }}
    header p, .batch-head p {{
      margin: 8px 0 0;
      color: var(--board-muted);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px 20px 48px;
    }}
    .batch {{
      margin: 0 0 34px;
    }}
    .batch-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 12px;
    }}
    h2 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}
    .tile {{
      margin: 0;
      min-width: 0;
      border: 1px solid var(--board-line);
      background: var(--board-tile-surface);
    }}
    .image-frame {{
      padding: 8px;
      background: var(--board-image-matte);
      border-bottom: 1px solid var(--board-line);
    }}
    .tile img {{
      display: block;
      width: 100%;
      aspect-ratio: 1 / 1;
      object-fit: cover;
      background: color-mix(in srgb, var(--board-ink) 8%, var(--board-surface));
      border: 1px solid var(--board-line);
    }}
    figcaption {{
      display: grid;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 8px;
      padding: 9px 10px 11px;
      color: var(--board-muted);
      font-size: 13px;
      min-height: 44px;
    }}
    figcaption strong {{
      color: var(--board-surface);
      background: var(--board-accent);
      width: 22px;
      height: 22px;
      border-radius: 50%;
      display: inline-grid;
      place-items: center;
      font-size: 12px;
      line-height: 1;
    }}
    figcaption span {{
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }}
    @media (max-width: 760px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .batch-head {{ display: block; }}
    }}
    @media (max-width: 460px) {{
      .grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(board_title)}</h1>
    <p>{len(batches)} batch{'es' if len(batches) != 1 else ''}. Newest batches appear first. Use Ctrl + Click to add images and annotations to the thread.</p>
  </header>
  <main>
    {''.join(batch_html)}
  </main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    args = parse_args()
    cli = imagegen_cli_path(args.imagegen_cli)
    require_key = not (args.dry_run or args.mock_images or args.rebuild_html)
    check_env(cli, require_key=require_key)
    if args.check_env:
        sdk_status = "installed"
        if importlib.util.find_spec("openai") is None:
            uv = shutil.which("uv")
            if not uv:
                fail(
                    "the openai Python SDK is not installed in this Python environment and `uv` was not found "
                    "for automatic dependency isolation. Install with `uv pip install openai` or run via "
                    "`uv run --with openai`."
                )
            uv_cache_dir = os.environ.get("UV_CACHE_DIR", str(DEFAULT_UV_CACHE_DIR))
            sdk_status = f"available through uv fallback at {uv} with UV_CACHE_DIR={uv_cache_dir}"
        print(f"OK: imagegen CLI found at {cli}")
        print("OK: OPENAI_API_KEY is set locally")
        print(f"OK: openai Python SDK {sdk_status}")
        return
    if args.rebuild_html:
        output_dir = Path(args.output_dir).expanduser()
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.exists():
            fail(f"manifest not found: {manifest_path}")
        render_html(load_manifest(manifest_path), output_dir)
        print(f"HTML preview: {output_dir / 'index.html'}")
        return
    if not args.spec:
        fail("--spec is required unless --check-env is used")

    started = time.monotonic()
    spec_path = Path(args.spec).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    batch_number = next_available_batch_number(manifest, output_dir)
    timestamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    spec = read_json(spec_path)
    board_title = str(spec.get("title") or manifest.get("title") or TITLE).strip() or TITLE
    count = clamp_count(spec.get("image_count") or spec.get("count"))
    api_reference_mode = send_reference_images(spec)
    reference_images = resolve_reference_images(spec, spec_path) if api_reference_mode else []
    model = str(spec.get("model") or DEFAULT_MODEL)
    quality = str(spec.get("quality") or DEFAULT_QUALITY)
    size = str(spec.get("size") or DEFAULT_SIZE)
    output_format = str(spec.get("output_format") or DEFAULT_FORMAT).lower()
    concurrency = min(int(spec.get("concurrency") or count), MAX_BATCH, count)
    if concurrency < 1:
        fail("concurrency must be at least 1")

    slug = slugify(str(spec.get("brief") or spec.get("subject") or "mood-board"), "mood-board")
    batch_dir = output_dir / f"batch-{batch_number:03d}-{slug}"
    batch_dir.mkdir(parents=True, exist_ok=False)

    prompts = build_prompts(spec, count, reference_mode=api_reference_mode)
    mood_names = build_mood_names(spec, count)
    jobs_path = write_jobs(prompts, batch_dir, model, quality, size, output_format)
    spec_copy = batch_dir / "spec.json"
    spec_copy.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.mock_images:
        write_mock_images(batch_dir, count, output_format)
    elif reference_images:
        run_reference_edit_batch(
            cli,
            prompts,
            reference_images,
            batch_dir,
            concurrency,
            dry_run=args.dry_run,
            model=model,
            quality=quality,
            size=size,
            output_format=output_format,
        )
    else:
        run_cli(cli, jobs_path, batch_dir, concurrency, dry_run=args.dry_run)

    outputs = collect_outputs(batch_dir, count, output_format)
    if not args.dry_run and len(outputs) != count:
        fail(f"expected {count} output images, found {len(outputs)} in {batch_dir}")

    duration = round(time.monotonic() - started, 2)
    rel_outputs = [(batch_dir / name).relative_to(output_dir).as_posix() for name in outputs]
    batch_record = {
        "batch_number": batch_number,
        "timestamp": timestamp,
        "brief": spec.get("brief") or spec.get("subject") or "",
        "prompts": prompts,
        "mood_names": mood_names,
        "model": model,
        "quality": quality,
        "size": size,
        "output_format": output_format,
        "concurrency": concurrency,
        "duration_seconds": duration,
        "output_paths": rel_outputs,
        "reference_images": [reference_label(entry) for entry in reference_entries(spec)],
        "sent_reference_images": [str(path) for path in reference_images],
        "sent_reference_image_count": len(reference_images),
        "send_reference_images": api_reference_mode,
        "jobs_path": (jobs_path.relative_to(output_dir)).as_posix(),
        "spec_path": (spec_copy.relative_to(output_dir)).as_posix(),
        "dry_run": bool(args.dry_run),
        "mock_images": bool(args.mock_images),
    }
    manifest["title"] = board_title
    manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest["batches"].append(batch_record)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render_html(manifest, output_dir)

    print(f"Batch {batch_number} complete: {len(outputs)} image(s)")
    print(f"HTML preview: {output_dir / 'index.html'}")
    print(f"Manifest: {manifest_path}")
    if args.dry_run:
        print("Dry run only: no images were generated.")


if __name__ == "__main__":
    main()
