#!/usr/bin/env python3
"""Render storyboard stills and narration into an MP4 video using ffmpeg."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from storyboard_lib import load_storyboard, save_storyboard


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, capture_output=True)


def audio_duration(path: str) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ]
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def write_srt(rows: list[dict], out_path: Path) -> None:
    cursor = 0.0
    blocks = []
    for index, row in enumerate(rows, start=1):
        duration = float(row.get("duration_override") or row.get("audio_duration") or 0)
        start = cursor
        end = cursor + duration
        blocks.append(f"{index}\n{fmt_time(start)} --> {fmt_time(end)}\n{row.get('caption_text') or row.get('narration_text')}\n")
        cursor = end
    out_path.write_text("\n".join(blocks), encoding="utf-8")


def fmt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    h, rem = divmod(millis, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_clip(row: dict, clip_path: Path, width: int, height: int) -> None:
    image = row.get("image_path")
    audio = row.get("audio_path")
    if not image or not Path(image).exists():
        raise RuntimeError(f"{row['id']}: missing image_path")
    if not audio or not Path(audio).exists():
        raise RuntimeError(f"{row['id']}: missing audio_path")
    duration = float(row.get("duration_override") or row.get("audio_duration") or audio_duration(audio))
    row["audio_duration"] = audio_duration(audio)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,format=yuv420p"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-t",
        f"{duration:.3f}",
        "-i",
        image,
        "-i",
        audio,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(clip_path),
    ]
    run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("storyboard")
    parser.add_argument("--media-dir", default="media")
    parser.add_argument("--out", default="output/video-overview.mp4")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--captions", default="output/captions.srt")
    args = parser.parse_args()

    data = load_storyboard(args.storyboard)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    captions_path = Path(args.captions)
    captions_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vog-render-") as tmp_raw:
        tmp = Path(tmp_raw)
        concat_file = tmp / "concat.txt"
        clip_paths = []
        for row in data["rows"]:
            clip_path = tmp / f"{row['id']}.mp4"
            print(f"Rendering clip: {row['id']}")
            render_clip(row, clip_path, args.width, args.height)
            clip_paths.append(clip_path)
        concat_file.write_text(
            "".join(f"file {shlex.quote(str(path))}\n" for path in clip_paths),
            encoding="utf-8",
        )
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(out_path)])

    write_srt(data["rows"], captions_path)
    save_storyboard(data, args.storyboard)
    print(f"Wrote {out_path}")
    print(f"Wrote {captions_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
