#!/usr/bin/env python3
"""Combine multiple transcript segments into one multi-speaker demo video.

Reads a JSON manifest listing transcript files, speaker titles, and voices,
then builds a single MP4 using the same per-sentence rendering + TTS pipeline
as make-podcast-demo-video.py. Each segment is one speaker; voices change to
make the distinction clear.

Manifest format (one object per segment):
[
  {"text": "/tmp/karpathy.txt", "title": "Andrej Karpathy", "voice": "Daniel"},
  {"text": "/tmp/ng.txt", "title": "Andrew Ng", "voice": "Samantha"}
]

Usage:
    .venv/bin/python3 scripts/make-multi-speaker-demo.py /tmp/segments.json \
        --out state/omni-demo/omni-multi-speaker-demo.mp4
"""

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

WIDTH = 1280
HEIGHT = 720
FONT = "/System/Library/Fonts/Helvetica.ttc"
BG = "#0f3460"
HEADER_BG = "#1a1a2e"
BODY_COLOR = "#ffffff"
ACCENT = "#c4b5fd"
LABEL_COLOR = "#86efac"


def check_deps():
    for cmd in ["say", "ffmpeg"]:
        if not shutil.which(cmd):
            sys.exit(f"Required tool not found: {cmd}")
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Pillow not found in .venv")


def split_sentences(text: str) -> list[str]:
    import re
    text = re.sub(r"\s+", " ", text.strip())
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    out = []
    for s in raw:
        s = s.strip()
        if not s:
            continue
        if len(s) > 200:
            parts = re.split(r",\s+", s)
            out.extend(p.strip() for p in parts if p.strip())
        else:
            out.append(s)
    return out


def render_frame(text: str, n: int, total: int, title: str, subtitle: str, out: Path):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    try:
        f_head = ImageFont.truetype(FONT, 32)
        f_body = ImageFont.truetype(FONT, 40)
        f_small = ImageFont.truetype(FONT, 22)
    except Exception:
        f_head = f_body = f_small = ImageFont.load_default()

    d.rectangle([0, 0, WIDTH, 100], fill=HEADER_BG)
    d.text((40, 30), title, font=f_head, fill=ACCENT)
    d.text((WIDTH - 220, 34), f"{n}/{total}", font=f_head, fill=LABEL_COLOR)
    if subtitle:
        d.text((40, 68), subtitle, font=f_small, fill="#9a968c")

    lines = textwrap.wrap(text, width=38)
    y = 160
    for ln in lines[:9]:
        d.text((80, y), ln, font=f_body, fill=BODY_COLOR)
        y += 62

    img.save(str(out))


def synthesize_audio(text: str, out: Path, voice: str):
    subprocess.run(["say", "-v", voice, "-o", str(out), text], check=True, capture_output=True)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", help="JSON manifest of segments")
    parser.add_argument("--out", default="state/omni-demo/omni-multi-speaker-demo.mp4")
    args = parser.parse_args()

    check_deps()

    segments = json.loads(Path(args.manifest).read_text())
    if not segments:
        sys.exit("Empty manifest")

    print(f"{len(segments)} segments")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="multi-demo-") as tmp:
        seg_dir = Path(tmp) / "segs"
        seg_dir.mkdir()
        global_idx = 0
        global_total = sum(
            len(split_sentences(Path(s["text"]).read_text())) for s in segments
        )

        for seg in segments:
            sentences = split_sentences(Path(seg["text"]).read_text())
            title = seg.get("title", "Speaker")
            voice = seg.get("voice", "Samantha")
            print(f"\n=== {title} ({len(sentences)} sentences, voice={voice}) ===")
            for sent in sentences:
                global_idx += 1
                safe = f"{global_idx:03d}"
                aiff = seg_dir / f"s{safe}.aiff"
                png = seg_dir / f"s{safe}.png"
                mp4 = seg_dir / f"s{safe}.mp4"
                print(f"  [{global_idx}/{global_total}] {sent[:60]}{'…' if len(sent) > 60 else ''}")
                synthesize_audio(sent, aiff, voice)
                render_frame(sent, global_idx, global_total, title, "", png)
                subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-loop", "1", "-i", str(png), "-i", str(aiff),
                        "-filter_complex",
                        "[1:a]aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono,apad=pad_dur=0.5[a]",
                        "-map", "0:v", "-map", "[a]",
                        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "96k", "-shortest", str(mp4),
                    ],
                    check=True,
                )

        list_file = seg_dir / "list.txt"
        list_file.write_text(
            "\n".join(
                f"file '{seg_dir / f's{i:03d}.mp4'}'"
                for i in range(1, global_idx + 1)
            )
        )

        print(f"\nConcatenating {global_idx} segments → {out_path}")
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(out_path),
            ],
            check=True,
        )

    print(f"Done: {out_path}")
    subprocess.run(["ls", "-lh", str(out_path)])


if __name__ == "__main__":
    main()
