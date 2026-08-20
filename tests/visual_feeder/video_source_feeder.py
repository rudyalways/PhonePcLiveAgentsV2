#!/usr/bin/env python3
"""Video-driven visual input for omni agent tests.

Feeds real video content (a podcast, a screen recording, a live LiveKit
track) to the omni agent instead of synthetic test patterns. Useful when a
test needs realistic, changing visual content — scene-change detection,
board-ink OCR, and "describe what you see" behaviour all respond
differently to a real talking-head video than to a colour-block pattern.

Three sources, one output format. Every source yields JPEG bytes; delivery
reuses OmniVisualTestFeeder.inject_single_frame, so the wire protocol
({"type":"image","mime":"image/jpeg","data":"<base64>"} as WS TEXT after a
session.start handshake) has exactly one implementation.

    file    — local video file, decoded by ffmpeg
    url     — remote video / YouTube / podcast link, resolved by yt-dlp
    livekit — subscribe to a remote video track in a LiveKit room

Usage:
    # Feed a local podcast recording at 1 frame every 5 seconds
    async with VideoFeeder(FileVideoSource("podcast.mp4", fps=0.2)) as feeder:
        await feeder.wait_frames(10)
    print(feeder.stats)

    # Feed a YouTube podcast, real-time paced
    src = UrlVideoSource("https://www.youtube.com/watch?v=...", fps=0.2)
    async with VideoFeeder(src) as feeder:
        await feeder.run_for(60)

    # Feed whatever video a participant publishes into a LiveKit room
    src = LiveKitVideoSource(room="sutando-test", fps=0.5)
    async with VideoFeeder(src) as feeder:
        await feeder.wait_frames(5)

CLI:
    .venv/bin/python3 tests/visual_feeder/video_source_feeder.py \
        --url "https://www.youtube.com/watch?v=..." --fps 0.2 --duration 60

Requires ffmpeg (all modes except livekit) and yt-dlp (url mode).
"""

import asyncio
import io
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omni_visual_test_feeder import HAS_PIL, Image, OmniVisualTestFeeder

# JPEG frame delimiters — ffmpeg's image2pipe/mjpeg output is a bare
# concatenation of JPEGs with no length prefix, so frames are split on these.
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"

# Bound the read buffer so a non-JPEG stream (wrong ffmpeg args, HTML error
# page) fails loudly instead of growing until the process is OOM-killed.
_MAX_BUFFERED_BYTES = 32 * 1024 * 1024

DEFAULT_FPS = 0.2
DEFAULT_MAX_WIDTH = 640
DEFAULT_JPEG_QUALITY = 5  # ffmpeg -q:v scale, 2=best 31=worst


@dataclass
class VideoFeedStats:
    """Frame accounting for a video-driven run."""

    frames_decoded: int = 0
    frames_sent: int = 0
    frames_failed: int = 0
    errors_received: int = 0
    last_errors: list = field(default_factory=list)
    source_ended: bool = False
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_s(self) -> float:
        end = self.end_time if self.end_time > 0 else time.time()
        return end - self.start_time if self.start_time > 0 else 0.0

    @property
    def avg_fps(self) -> float:
        return self.frames_sent / self.duration_s if self.duration_s > 0 else 0.0

    def __str__(self) -> str:
        return (
            f"VideoFeedStats(decoded={self.frames_decoded}, sent={self.frames_sent}, "
            f"failed={self.frames_failed}, errors={self.errors_received}, "
            f"duration={self.duration_s:.1f}s, fps={self.avg_fps:.3f})"
        )


def _require(binary: str, install_hint: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise RuntimeError(f"{binary} not found on PATH. Install: {install_hint}")
    return path


def _iter_jpegs(buffer: bytearray) -> list[bytes]:
    """Drain complete JPEGs from `buffer`, leaving any partial frame behind."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(_SOI)
        if start < 0:
            break
        end = buffer.find(_EOI, start + 2)
        if end < 0:
            del buffer[:start]  # discard leading garbage, keep partial frame
            break
        frames.append(bytes(buffer[start : end + 2]))
        del buffer[: end + 2]
    return frames


class FfmpegVideoSource:
    """Base for sources that decode JPEG frames out of an ffmpeg pipe.

    ffmpeg does the frame-rate decimation (`-vf fps=`) and the scaling, so
    only the frames actually wanted are ever encoded — decoding a 1-hour
    podcast at 0.2fps costs far less than decoding every frame and dropping
    most of them in Python.
    """

    def __init__(
        self,
        *,
        fps: float = DEFAULT_FPS,
        max_width: int = DEFAULT_MAX_WIDTH,
        quality: int = DEFAULT_JPEG_QUALITY,
        realtime: bool = False,
        start_at: Optional[str] = None,
        duration_s: Optional[float] = None,
    ):
        if fps <= 0:
            raise ValueError(f"fps must be > 0, got {fps}")
        self.fps = fps
        self.max_width = max_width
        self.quality = quality
        self.realtime = realtime
        self.start_at = start_at
        self.duration_s = duration_s
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stderr_tail: list[str] = []

    def describe(self) -> str:
        raise NotImplementedError

    def _input_args(self) -> list[str]:
        """ffmpeg args that precede -i, plus the input spec itself."""
        raise NotImplementedError

    def _ffmpeg_cmd(self) -> list[str]:
        cmd = [_require("ffmpeg", "brew install ffmpeg"), "-hide_banner", "-loglevel", "error"]
        if self.realtime:
            # Decode no faster than wall clock, so a 30-minute podcast feeds
            # over 30 minutes. Without this ffmpeg races through the file and
            # the agent sees the whole video in seconds.
            cmd += ["-re"]
        if self.start_at:
            cmd += ["-ss", self.start_at]
        cmd += self._input_args()
        if self.duration_s:
            cmd += ["-t", str(self.duration_s)]
        cmd += [
            "-an",
            "-vf", f"fps={self.fps},scale={self.max_width}:-2",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", str(self.quality),
            "-",
        ]
        return cmd

    async def frames(self) -> AsyncIterator[bytes]:
        cmd = self._ffmpeg_cmd()
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(self._drain_stderr())
        buffer = bytearray()
        try:
            assert self._proc.stdout
            while True:
                chunk = await self._proc.stdout.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > _MAX_BUFFERED_BYTES:
                    raise RuntimeError(
                        f"No complete JPEG in {_MAX_BUFFERED_BYTES // (1024 * 1024)}MB "
                        f"of ffmpeg output — is the input a decodable video? "
                        f"stderr: {self.stderr_text()}"
                    )
                for frame in _iter_jpegs(buffer):
                    yield frame
        finally:
            stderr_task.cancel()
            await self.aclose()

    async def _drain_stderr(self) -> None:
        """Keep the stderr pipe empty and retain the tail for error reports."""
        if not self._proc or not self._proc.stderr:
            return
        try:
            async for line in self._proc.stderr:
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self._stderr_tail.append(text)
                    del self._stderr_tail[:-20]
        except asyncio.CancelledError:
            pass

    def stderr_text(self) -> str:
        return " | ".join(self._stderr_tail[-5:]) or "(no ffmpeg stderr)"

    async def aclose(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


class FileVideoSource(FfmpegVideoSource):
    """Decode frames from a local video file."""

    def __init__(self, path: str | Path, **kwargs):
        super().__init__(**kwargs)
        self.path = Path(path).expanduser()
        if not self.path.exists():
            raise FileNotFoundError(f"Video file not found: {self.path}")

    def describe(self) -> str:
        return f"file:{self.path.name}"

    def _input_args(self) -> list[str]:
        return ["-i", str(self.path)]


class UrlVideoSource(FfmpegVideoSource):
    """Decode frames from a remote video URL, including YouTube podcasts.

    yt-dlp resolves the page URL to a direct media URL that ffmpeg can open,
    so nothing is downloaded to disk — frames stream straight through. Plain
    direct media URLs (an .mp4 on a CDN, an HLS .m3u8) skip yt-dlp entirely.
    """

    def __init__(
        self,
        url: str,
        *,
        prefer_height: int = 720,
        resolve_timeout_s: float = 60.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.url = url
        self.prefer_height = prefer_height
        self.resolve_timeout_s = resolve_timeout_s
        self._resolved: Optional[str] = None

    def describe(self) -> str:
        return f"url:{self.url[:60]}"

    def _needs_ytdlp(self) -> bool:
        direct = (".mp4", ".mov", ".mkv", ".webm", ".m3u8", ".mpd", ".ts")
        return not self.url.lower().split("?")[0].endswith(direct)

    def resolve(self) -> str:
        """Resolve the page URL to a direct media URL (cached)."""
        if self._resolved:
            return self._resolved
        if not self._needs_ytdlp():
            self._resolved = self.url
            return self._resolved

        ytdlp = _require("yt-dlp", "pip install yt-dlp")
        # Video-only stream capped at prefer_height — no audio track to fetch
        # since the feeder discards audio anyway (-an below).
        fmt = f"bestvideo[height<={self.prefer_height}]/best[height<={self.prefer_height}]/best"
        try:
            proc = subprocess.run(
                [ytdlp, "-f", fmt, "--get-url", "--no-warnings", self.url],
                capture_output=True,
                text=True,
                timeout=self.resolve_timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"yt-dlp timed out resolving {self.url}")
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp failed for {self.url}: {proc.stderr.strip()[:300]}")
        urls = [line for line in proc.stdout.splitlines() if line.startswith("http")]
        if not urls:
            raise RuntimeError(f"yt-dlp returned no media URL for {self.url}")
        self._resolved = urls[0]
        return self._resolved

    def _input_args(self) -> list[str]:
        return [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", self.resolve(),
        ]

    async def frames(self) -> AsyncIterator[bytes]:
        # Resolve off the event loop — yt-dlp does blocking network I/O.
        await asyncio.to_thread(self.resolve)
        async for frame in super().frames():
            yield frame


class LiveKitVideoSource:
    """Subscribe to a remote video track in a LiveKit room and yield JPEGs.

    Joins `room` as a subscriber-only participant, waits for any remote
    participant to publish a video track, then converts each received
    VideoFrame to JPEG at the requested rate. This lets a browser tab, a
    phone camera, or a screen share drive the omni agent's visual input.

    Note the project's own src/livekit-agent.py is audio-only — it logs
    video track events but never subscribes. This source is independent of
    it: it is a test client, not a change to the agent.

    Credentials come from LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
    (same env vars src/livekit-agent.py uses).
    """

    def __init__(
        self,
        *,
        room: str,
        fps: float = DEFAULT_FPS,
        max_width: int = DEFAULT_MAX_WIDTH,
        identity: str = "video-test-feeder",
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        track_timeout_s: float = 30.0,
    ):
        if fps <= 0:
            raise ValueError(f"fps must be > 0, got {fps}")
        self.room_name = room
        self.fps = fps
        self.max_width = max_width
        self.identity = identity
        self.url = url or os.environ.get("LIVEKIT_URL", "")
        self.api_key = api_key or os.environ.get("LIVEKIT_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("LIVEKIT_API_SECRET", "")
        self.track_timeout_s = track_timeout_s
        self._room = None

    def describe(self) -> str:
        return f"livekit:{self.room_name}"

    def _token(self) -> str:
        from livekit import api

        return (
            api.AccessToken(self.api_key, self.api_secret)
            .with_identity(self.identity)
            .with_name(self.identity)
            .with_grants(api.VideoGrants(room_join=True, room=self.room_name))
            .to_jwt()
        )

    async def frames(self) -> AsyncIterator[bytes]:
        if not HAS_PIL or not Image:
            raise RuntimeError("Pillow required for LiveKit mode: pip install pillow")
        try:
            from livekit import rtc
        except ImportError:
            raise RuntimeError(
                "livekit required for LiveKit mode: pip install -r requirements-livekit.txt"
            )
        missing = [
            name
            for name, val in (
                ("LIVEKIT_URL", self.url),
                ("LIVEKIT_API_KEY", self.api_key),
                ("LIVEKIT_API_SECRET", self.api_secret),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(f"LiveKit credentials missing: {', '.join(missing)}")

        track_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._room = rtc.Room()

        @self._room.on("track_subscribed")
        def _on_track(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_VIDEO:
                try:
                    track_queue.put_nowait((track, participant.identity))
                except asyncio.QueueFull:
                    pass  # already feeding from a track; ignore extras

        await self._room.connect(self.url, self._token())
        try:
            # A track published before we connected arrives via the existing
            # publications rather than the event, so check both.
            for participant in self._room.remote_participants.values():
                for pub in participant.track_publications.values():
                    if pub.kind == rtc.TrackKind.KIND_VIDEO and pub.track:
                        try:
                            track_queue.put_nowait((pub.track, participant.identity))
                        except asyncio.QueueFull:
                            pass

            try:
                track, publisher = await asyncio.wait_for(
                    track_queue.get(), timeout=self.track_timeout_s
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"No video track published to room '{self.room_name}' within "
                    f"{self.track_timeout_s:.0f}s — is a participant sharing video?"
                )
            print(f"LiveKit video track from {publisher}")

            min_gap = 1.0 / self.fps
            last_emit = 0.0
            stream = rtc.VideoStream(track)
            try:
                async for event in stream:
                    now = time.monotonic()
                    if now - last_emit < min_gap:
                        continue  # decimate to the requested rate
                    last_emit = now
                    jpeg = await asyncio.to_thread(self._frame_to_jpeg, event.frame)
                    if jpeg:
                        yield jpeg
            finally:
                await stream.aclose()
        finally:
            await self.aclose()

    def _frame_to_jpeg(self, frame) -> Optional[bytes]:
        """Convert a LiveKit VideoFrame to a downscaled JPEG."""
        from livekit import rtc

        try:
            rgb = frame.convert(rtc.VideoBufferType.RGB24)
            img = Image.frombytes("RGB", (rgb.width, rgb.height), bytes(rgb.data))
            if img.width > self.max_width:
                height = round(img.height * self.max_width / img.width)
                resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                img = img.resize((self.max_width, height), resample)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            return buf.getvalue()
        except Exception as e:
            print(f"LiveKit frame conversion failed: {e}")
            return None

    async def aclose(self) -> None:
        room = self._room
        self._room = None
        if room:
            await room.disconnect()


class VideoFeeder:
    """Pump frames from a video source into the omni agent over WebSocket.

    Delivery reuses OmniVisualTestFeeder so the session.start handshake,
    the TEXT-JSON image envelope, and server-error tracking all stay in one
    place. This class only owns the source loop.
    """

    def __init__(
        self,
        source,
        *,
        omni_host: str = "localhost",
        omni_port: int = 7090,
        user: str = "video-feeder-test",
        secret: str = "",
    ):
        self.source = source
        self.stats = VideoFeedStats()
        self._ws_feeder = OmniVisualTestFeeder(
            mode="synthetic",  # unused; frames come from the video source
            omni_host=omni_host,
            omni_port=omni_port,
            user=user,
            secret=secret,
            max_width=DEFAULT_MAX_WIDTH,
        )
        self._pump_task: Optional[asyncio.Task] = None
        self._running = False

    async def __aenter__(self) -> "VideoFeeder":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.stop()

    async def start(self) -> None:
        """Connect to the agent and begin pumping frames."""
        if self._running:
            raise RuntimeError("Feeder already running")
        await self._ws_feeder.connect()
        self._running = True
        self.stats = VideoFeedStats(start_time=time.time())
        print(f"Feeding {self.source.describe()} → omni agent")
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            async for jpeg in self.source.frames():
                if not self._running:
                    break
                self.stats.frames_decoded += 1
                if await self._ws_feeder.inject_single_frame(jpeg):
                    self.stats.frames_sent += 1
                else:
                    self.stats.frames_failed += 1
            self.stats.source_ended = True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.stats.frames_failed += 1
            self.stats.last_errors.append(str(e))
            print(f"Video feed error: {e}")

    async def wait_frames(self, count: int, timeout_s: float = 180.0) -> None:
        """Wait until `count` frames have been sent, or the source ends."""
        deadline = time.time() + timeout_s
        while self.stats.frames_sent < count:
            if self.stats.source_ended:
                raise RuntimeError(
                    f"Video source ended after {self.stats.frames_sent} frames "
                    f"(wanted {count})"
                )
            if self._pump_task and self._pump_task.done():
                await self._pump_task  # re-raise whatever killed the pump
            if time.time() > deadline:
                raise TimeoutError(
                    f"Timeout waiting for {count} frames (got {self.stats.frames_sent})"
                )
            await asyncio.sleep(0.25)

    async def run_for(self, seconds: float) -> VideoFeedStats:
        """Feed for a fixed wall-clock duration, then return stats."""
        try:
            await asyncio.wait_for(asyncio.shield(self._pump_task), timeout=seconds)
        except (asyncio.TimeoutError, TypeError):
            pass
        return self._snapshot()

    def _snapshot(self) -> VideoFeedStats:
        ws_stats = self._ws_feeder.stats
        self.stats.errors_received = ws_stats.errors_received
        if ws_stats.last_errors:
            self.stats.last_errors = list(ws_stats.last_errors)
        return self.stats

    async def stop(self) -> VideoFeedStats:
        """Stop the pump, close the source and the agent connection."""
        self._running = False
        if self._pump_task:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):
                pass
            self._pump_task = None

        close = getattr(self.source, "aclose", None)
        if close:
            try:
                await close()
            except Exception as e:
                print(f"Source close failed: {e}")

        await self._ws_feeder.stop()
        self.stats.end_time = time.time()
        return self._snapshot()


def build_source(args) -> object:
    """Construct the source the CLI flags selected."""
    common = dict(fps=args.fps, max_width=args.max_width)
    if args.file:
        return FileVideoSource(
            args.file, realtime=args.realtime, start_at=args.start_at, **common
        )
    if args.url:
        return UrlVideoSource(
            args.url, realtime=args.realtime, start_at=args.start_at, **common
        )
    return LiveKitVideoSource(room=args.livekit_room, **common)


async def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Feed video frames to the omni agent for visual testing.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="local video file to feed")
    src.add_argument("--url", help="video / YouTube / podcast URL to feed")
    src.add_argument(
        "--livekit-room",
        help="LiveKit room to subscribe to for a published video track",
    )
    parser.add_argument(
        "--fps", type=float, default=DEFAULT_FPS,
        help=f"frames per second to send (default {DEFAULT_FPS} = 1 per 5s)",
    )
    parser.add_argument(
        "--max-width", type=int, default=DEFAULT_MAX_WIDTH,
        help=f"downscale frames to this width (default {DEFAULT_MAX_WIDTH})",
    )
    parser.add_argument(
        "--duration", type=float, default=0,
        help="stop after N seconds (default: until the source ends)",
    )
    parser.add_argument(
        "--frames", type=int, default=0,
        help="stop after N frames have been sent",
    )
    parser.add_argument(
        "--realtime", action="store_true",
        help="pace decoding to wall clock so a 30min video feeds over 30min",
    )
    parser.add_argument("--start-at", help="seek before feeding, e.g. 00:05:00")
    parser.add_argument("--omni-host", default="localhost")
    parser.add_argument("--omni-port", type=int, default=7090)
    parser.add_argument("--user", default="video-feeder-test")
    parser.add_argument("--secret", default="")
    args = parser.parse_args()

    try:
        source = build_source(args)
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"Source setup failed: {e}")
        return 1

    feeder = VideoFeeder(
        source,
        omni_host=args.omni_host,
        omni_port=args.omni_port,
        user=args.user,
        secret=args.secret,
    )
    try:
        await feeder.start()
        if args.frames:
            timeout = args.duration or max(60.0, args.frames / args.fps * 3)
            await feeder.wait_frames(args.frames, timeout_s=timeout)
        elif args.duration:
            await feeder.run_for(args.duration)
        else:
            await feeder._pump_task
    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        print(f"Feed failed: {e}")
        stats = await feeder.stop()
        print(stats)
        return 1

    stats = await feeder.stop()
    print(stats)
    if stats.errors_received:
        print(f"Agent rejected frames: {stats.last_errors}")
        return 1
    return 0 if stats.frames_sent else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))





