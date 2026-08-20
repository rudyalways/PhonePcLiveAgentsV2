#!/usr/bin/env python3
"""Tests for the video-driven visual feeder (video_source_feeder.py).

Covers three things, each independent of the others:

1. JPEG stream splitting (_iter_jpegs) — pure, no ffmpeg needed.
2. ffmpeg decode path — synthesizes a short test clip with ffmpeg and checks
   that FileVideoSource yields the expected number of valid JPEGs at the
   requested rate. Skips cleanly if ffmpeg is absent.
3. Live feed to the omni agent — feeds the clip to omni-exp on :7090 and
   asserts frames land without agent rejections. Skips cleanly when the
   agent is not running.

Run:
    .venv/bin/python3 tests/omni-visual-video-feeder.test.py
"""

import asyncio
import io
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests" / "visual_feeder"))

from video_source_feeder import (  # noqa: E402
    DEFAULT_FPS,
    FileVideoSource,
    VideoFeeder,
    _iter_jpegs,
)

OMNI_PORT = 7090


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("localhost", port)) == 0


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found")
    return path


def _make_test_video(duration_s: int = 12, size: str = "1280x720") -> Path:
    """Synthesize a small test clip (testsrc2 + a sine tone).

    Keyed by duration so different tests don't collide on a stale cache.
    """
    path = Path(f"/tmp/feeder-test-video-{duration_s}s.mp4")
    if path.exists():
        return path
    subprocess.run(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration={duration_s}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )
    return path


def test_splitter_drops_garbage_keeps_partial():
    buf = bytearray(
        b"GARBAGE"
        b"\xff\xd8AAA\xff\xd9"
        b"\xff\xd8BBB\xff\xd9"
        b"\xff\xd8PARTIAL"
    )
    frames = _iter_jpegs(buf)
    assert frames == [b"\xff\xd8AAA\xff\xd9", b"\xff\xd8BBB\xff\xd9"]
    assert buf == b"\xff\xd8PARTIAL"  # partial frame retained for next chunk


def test_splitter_handles_empty():
    assert _iter_jpegs(bytearray()) == []


def test_decode_path_yields_expected_frames():
    if not shutil.which("ffmpeg"):
        print("SKIP: ffmpeg not installed")
        return
    video = _make_test_video(duration_s=12)

    async def run():
        src = FileVideoSource(video, fps=1.0, max_width=640)
        frames = []
        async for jpeg in src.frames():
            from PIL import Image

            img = Image.open(io.BytesIO(jpeg))
            img.load()
            assert img.format == "JPEG", "decoded frame is not JPEG"
            assert img.width <= 640, "frame not downscaled"
            frames.append(jpeg)
        assert len(frames) == 12, f"expected 12 frames at 1fps from 12s, got {len(frames)}"

    asyncio.run(run())


def test_live_feed_to_agent():
    if not _port_open(OMNI_PORT):
        print("SKIP: omni-exp agent not running on :7090")
        return
    if not shutil.which("ffmpeg"):
        print("SKIP: ffmpeg not installed")
        return
    video = _make_test_video(duration_s=12)

    async def run():
        src = FileVideoSource(video, fps=0.5)  # 1 frame every 2s
        feeder = VideoFeeder(src)
        await feeder.start()
        try:
            await feeder.wait_frames(4, timeout_s=60)
        finally:
            stats = await feeder.stop()

        assert stats.frames_sent >= 4, f"sent {stats.frames_sent}, wanted >= 4"
        assert stats.frames_failed == 0, f"{stats.frames_failed} frames failed"
        assert stats.errors_received == 0, (
            f"agent rejected frames: {stats.last_errors}"
        )

    asyncio.run(run())


if __name__ == "__main__":
    for name in ("splitter", "decode", "live"):
        fn = {
            "splitter": test_splitter_drops_garbage_keeps_partial,
            "decode": test_decode_path_yields_expected_frames,
            "live": test_live_feed_to_agent,
        }[name]
        print(f"\n=== {name} ===")
        fn()
    print("\nAll video feeder tests passed (or skipped cleanly).")
