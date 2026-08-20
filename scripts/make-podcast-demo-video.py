#!/usr/bin/env python3
"""Generate a podcast-style demo video from a transcript.

Takes a plain-text transcript (one sentence per line, or natural paragraphs
split into sentences), synthesizes speech for each sentence with macOS `say`,
renders a simple title-card frame per sentence (Pillow), and concatenates
everything into a single MP4 with 16 kHz mono audio.

Designed for producing a realistic podcast demo clip for the omni agent: real
spoken content with visual cues, at the exact audio sample rate the client
sends (16 kHz mono PCM16).

Usage:
    .venv/bin/python3 scripts/make-podcast-demo-video.py /tmp/podcast_task_segment.txt \
        --out state/omni-demo/omni-podcast-demo.mp4

Requirements:
    - macOS `say` command
    - ffmpeg
    - Pillow in .venv
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VOICES = ["Samantha", "Alex", "Daniel", "Karen", "Moira"]
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
    """Split text into sentences. Handles abbreviations badly but is fine
    for podcast-style spoken text where most periods end sentences."""
    text = re.sub(r"\s+", " ", text.strip())
    # Split on sentence-ending punctuation followed by space+uppercase or end
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    # Further split very long sentences (> 200 chars) on commas
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


def render_frame(text: str, n: int, total: int, title: str, out: Path):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    try:
        f_head = ImageFont.truetype(FONT, 36)
        f_body = ImageFont.truetype(FONT, 44)
        f_small = ImageFont.truetype(FONT, 24)
    except Exception:
        f_head = f_body = f_small = ImageFont.load_default()

    # Header
    d.rectangle([0, 0, WIDTH, 100], fill=HEADER_BG)
    d.text((40, 28), title, font=f_head, fill=ACCENT)
    d.text((WIDTH - 180, 32), f"{n}/{total}", font=f_head, fill=LABEL_COLOR)

    # Sentence body, wrapped
    import textwrap
    lines = textwrap.wrap(text, width=36)
    y = 180
    for ln in lines[:10]:  # cap at 10 lines
        d.text((80, y), ln, font=f_body, fill=BODY_COLOR)
        y += 70

    img.save(str(out))


def synthesize_audio(text: str, out: Path, voice: str):
    subprocess.run(
        ["say", "-v", voice, "-o", str(out), text],
        check=True,
        capture_output=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", help="Plain text file (one or more paragraphs)")
    parser.add_argument("--out", default="state/omni-demo/omni-podcast-demo.mp4")
    parser.add_argument("--voice", default="Samantha", choices=VOICES)
    parser.add_argument("--title", default="Lenny's Podcast · Ian Silber")
    args = parser.parse_args()

    check_deps()

    text = Path(args.transcript).read_text()
    sentences = split_sentences(text)
    if not sentences:
        sys.exit("No sentences found in transcript")

    print(f"{len(sentences)} sentences from {args.transcript}")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="podcast-demo-") as tmp:
        seg_dir = Path(tmp) / "segs"
        seg_dir.mkdir()

        for i, sent in enumerate(sentences, 1):
            safe = f"{i:03d}"
            aiff = seg_dir / f"s{safe}.aiff"
            png = seg_dir / f"s{safe}.png"
            mp4 = seg_dir / f"s{safe}.mp4"

            print(f"  [{i}/{len(sentences)}] {sent[:70]}{'…' if len(sent) > 70 else ''}")
            synthesize_audio(sent, aiff, args.voice)
            render_frame(sent, i, len(sentences), args.title, png)

            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-loop", "1", "-i", str(png), "-i", str(aiff),
                    "-filter_complex",
                    "[1:a]aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono,apad=pad_dur=0.6[a]",
                    "-map", "0:v", "-map", "[a]",
                    "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "96k", "-shortest", str(mp4),
                ],
                check=True,
            )

        # Concat list
        list_file = seg_dir / "list.txt"
        list_file.write_text(
            "\n".join(f"file '{seg_dir / f's{i:03d}.mp4'}'" for i in range(1, len(sentences) + 1))
        )

        print(f"\nConcatenating {len(sentences)} segments → {out_path}")
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
