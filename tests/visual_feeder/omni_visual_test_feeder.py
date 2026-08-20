#!/usr/bin/env python3
"""Test framework component for feeding visual input to omni agent.

Provides controlled screenshot capture for testing omni agent visual processing:
- Configurable frame rate (default: very low for tests)
- Mock frame generation (synthetic test images)
- Real screenshot capture via screen-capture-server
- Frame injection into omni agent's image queue

Wire protocol (matches src/omni-exp-agent.py ws_handler + src/omni-exp-client.html):
- The agent accepts ONLY WebSocket TEXT frames containing JSON. Binary frames
  are silently discarded by ws_handler (`if msg.type != WSMsgType.TEXT: continue`).
- The first message MUST be {"type": "session.start", "user": ..., "auth": ...};
  every other message is rejected with "send session.start first" until then.
- Frames are sent as {"type": "image", "mime": "image/jpeg", "data": "<base64>"}.
- Real screenshots via screen-capture-server require the
  X-Sutando-Capture-Token header (token at ~/.config/sutando/screen-capture-token).

Usage:
    # Real screenshots at 1 frame every 10 seconds
    feeder = OmniVisualTestFeeder(mode='screen', interval_s=10)
    await feeder.start()
    await feeder.wait_frames(5)  # Wait until 5 frames captured
    await feeder.stop()

    # Synthetic test frames (no screen capture needed)
    feeder = OmniVisualTestFeeder(mode='synthetic')
    await feeder.connect()
    await feeder.inject_single_frame(create_test_pattern())
"""

import asyncio
import base64
import io
import json
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

try:
    import aiohttp
    from aiohttp import ClientSession, ClientWebSocketResponse
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    aiohttp = None  # type: ignore[assignment]
    ClientSession = None  # type: ignore[assignment,misc]
    ClientWebSocketResponse = None  # type: ignore[assignment,misc]

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]

CAPTURE_TOKEN_PATH = Path.home() / ".config" / "sutando" / "screen-capture-token"

# Minimal valid 1x1 JPEG (PIL-generated, round-trip verified). Used when Pillow
# is unavailable. Must stay a valid base64 multiple-of-4 string AND decode to a
# JPEG that PIL/Qwen can open — the previous literal was truncated and raised
# binascii.Error on every use.
_FALLBACK_JPEG_B64 = (
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEP'
    'ERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4e'
    'Hh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAABAAEDASIA'
    'AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA'
    'AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3'
    'ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm'
    'p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA'
    'AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx'
    'BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK'
    'U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3'
    'uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD5nooo'
    'r2jiP//Z'
)


def read_capture_token() -> Optional[str]:
    """Read the screen-capture-server auth token (rotates on server restart)."""
    try:
        return CAPTURE_TOKEN_PATH.read_text().strip() or None
    except OSError:
        return None


@dataclass
class FrameStats:
    """Statistics for a test run."""
    frames_sent: int = 0
    frames_failed: int = 0
    errors_received: int = 0
    last_errors: list = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time if self.end_time > 0 else 0.0

    @property
    def avg_fps(self) -> float:
        if self.duration_s > 0:
            return self.frames_sent / self.duration_s
        return 0.0


class OmniVisualTestFeeder:
    """Test framework component for feeding visual input to omni agent.

    Provides controlled screenshot capture for testing omni agent visual processing.
    Separate from production vision-tools.ts — optimized for test scenarios.

    Features:
    - Real screenshots via screen-capture-server (port 7900, token-authenticated)
    - Synthetic test pattern generation (no screen capture needed)
    - Correct omni-exp wire protocol (session.start handshake, TEXT JSON frames)
    - Incoming error tracking — server-side rejections surface in stats
    - Configurable low frame rate for cost-effective testing
    - Test control primitives (wait for N frames, etc.)
    """

    def __init__(
        self,
        *,
        mode: Literal['screen', 'synthetic'] = 'synthetic',
        interval_s: float = 2.0,
        omni_host: str = 'localhost',
        omni_port: int = 7090,
        screen_capture_port: int = 7900,
        user: str = 'feeder-test',
        secret: str = '',
        ready_timeout_s: float = 15.0,
        max_width: int = 1024,
    ):
        """Initialize the test feeder.

        Args:
            mode: 'screen' for real screenshots, 'synthetic' for test patterns
            interval_s: Seconds between frames (default 2s = 1 frame every 2 seconds)
            omni_host: Omni-exp-agent hostname
            omni_port: Omni-exp-agent port
            screen_capture_port: Screen capture server port
            user: Username for session.start (checked against src/users.json
                  when OMNI_EXP_AUTH_REQUIRED=1)
            secret: Plaintext secret for session.start auth
        """
        self.mode = mode
        self.interval_s = interval_s
        self.omni_host = omni_host
        self.omni_port = omni_port
        self.screen_capture_port = screen_capture_port
        self.user = user
        self.secret = secret
        self.ready_timeout_s = ready_timeout_s
        self.max_width = max_width

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._ws: Optional[ClientWebSocketResponse] = None  # type: ignore
        self._session: Optional[ClientSession] = None  # type: ignore
        self._session_started = False
        self.stats = FrameStats()

    async def connect(self) -> None:
        """Connect and complete the session.start handshake (idempotent)."""
        await self._ensure_connection()

    async def start(self) -> None:
        """Start feeding frames to omni agent."""
        if not HAS_AIOHTTP:
            raise RuntimeError("aiohttp required: pip install aiohttp")
        if self._running:
            raise RuntimeError("Feeder already running")

        self._running = True
        self.stats = FrameStats(start_time=time.time())
        self._task = asyncio.create_task(self._feed_loop())

    async def stop(self) -> FrameStats:
        """Stop feeding frames and return statistics."""
        self._running = False
        for task in (self._task, self._reader_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = None
        self._reader_task = None

        await self._close_connection()
        self.stats.end_time = time.time()
        return self.stats

    async def wait_frames(self, count: int, timeout_s: float = 120.0) -> None:
        """Wait until at least `count` frames have been sent."""
        deadline = time.time() + timeout_s
        while self.stats.frames_sent < count:
            if time.time() > deadline:
                raise TimeoutError(f"Timeout waiting for {count} frames (got {self.stats.frames_sent})")
            await asyncio.sleep(0.5)

    async def inject_single_frame(self, frame_data: bytes) -> bool:
        """Inject a single JPEG frame into omni agent (one-shot, for manual test control)."""
        try:
            await self._ensure_connection()
            if not self._ws or self._ws.closed:
                self.stats.frames_failed += 1
                return False

            # omni-exp accepts only TEXT JSON frames; binary WS frames are
            # silently dropped by ws_handler. Match omni-exp-client.html.
            await self._ws.send_json({
                "type": "image",
                "mime": "image/jpeg",
                "data": base64.b64encode(frame_data).decode("ascii"),
            })
            self.stats.frames_sent += 1
            return True
        except Exception as e:
            print(f"Frame injection failed: {e}")
            self.stats.frames_failed += 1
            return False

    async def inject_audio(self, pcm16le_16k: bytes, chunk_ms: int = 100) -> bool:
        """Stream 16kHz mono PCM16 audio to the agent in real-time-ish chunks.

        Vision context note: Qwen (DashScope realtime) only retains an image
        frame if it arrives DURING an active VAD utterance. To ask a question
        about the screen, stream the spoken question via this method and inject
        the frame between audio chunks (or at ~1Hz like the production client)
        — a text prompt_manual turn does NOT see buffered images.
        """
        try:
            await self._ensure_connection()
            if not self._ws or self._ws.closed:
                return False
            chunk = int(16000 * 2 * chunk_ms / 1000)
            for i in range(0, len(pcm16le_16k), chunk):
                await self._ws.send_json({
                    "type": "audio",
                    "data": base64.b64encode(pcm16le_16k[i:i + chunk]).decode("ascii"),
                })
                await asyncio.sleep(chunk_ms / 1000 * 0.2)
            return True
        except Exception as e:
            print(f"Audio injection failed: {e}")
            return False

    async def ask_about_screen(
        self,
        question_pcm: bytes,
        frame_data: bytes,
        trailing_silence_ms: int = 1500,
    ) -> bool:
        """Ask a spoken question about a frame, using the working vision path.

        Interleaves: first half of the question audio → the frame → the rest
        of the audio → trailing silence (so server VAD closes the turn). The
        mid-utterance frame injection mirrors the production 1Hz ticker and is
        the only ordering DashScope reliably keeps the image for.
        """
        chunk = int(16000 * 2 * 0.1)  # 100ms
        chunks = [question_pcm[i:i + chunk] for i in range(0, len(question_pcm), chunk)]
        mid = max(1, len(chunks) // 2)
        try:
            await self._ensure_connection()
            if not self._ws or self._ws.closed:
                return False
            for i, c in enumerate(chunks):
                await self._ws.send_json({
                    "type": "audio",
                    "data": base64.b64encode(c).decode("ascii"),
                })
                if i == mid:
                    await self.inject_single_frame(frame_data)
                await asyncio.sleep(0.02)
            silence = b"\x00\x00" * 1600  # 100ms
            for _ in range(trailing_silence_ms // 100):
                await self._ws.send_json({
                    "type": "audio",
                    "data": base64.b64encode(silence).decode("ascii"),
                })
                await asyncio.sleep(0.02)
            return True
        except Exception as e:
            print(f"ask_about_screen failed: {e}")
            return False

    async def _ensure_connection(self) -> None:
        """Ensure WebSocket connection + session.start handshake are done."""
        if not HAS_AIOHTTP or not aiohttp:
            raise RuntimeError("aiohttp required")

        if self._ws and not self._ws.closed:
            return

        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        self._session_started = False

        # The agent serves wss:// only when state/server.crt+key exist,
        # plain ws:// otherwise. Try TLS first (self-signed → no verify),
        # fall back to plain ws.
        sslctx = ssl.create_default_context()
        sslctx.check_hostname = False
        sslctx.verify_mode = ssl.CERT_NONE
        last_err: Optional[Exception] = None
        for scheme, ssl_arg in (("wss", sslctx), ("ws", None)):
            url = f"{scheme}://{self.omni_host}:{self.omni_port}/ws"
            try:
                self._ws = await self._session.ws_connect(url, ssl=ssl_arg, max_msg_size=8 * 1024 * 1024)
                break
            except Exception as e:
                last_err = e
                self._ws = None
        if not self._ws:
            print(f"WebSocket connection failed: {last_err}")
            raise last_err  # type: ignore[misc]

        # Mandatory handshake — everything before session.start is rejected.
        await self._ws.send_json({
            "type": "session.start",
            "user": self.user,
            "auth": self.secret,
        })
        # Wait for session.ready. The agent connects to Qwen upstream inside
        # session.start handling (can take several seconds) and processes WS
        # messages sequentially — frames sent before session.ready would queue
        # unread and be lost if the upstream connect fails. An error frame
        # (e.g. "auth failed") is fatal.
        deadline = time.time() + self.ready_timeout_s
        ready = False
        while time.time() < deadline:
            try:
                msg = await self._ws.receive(timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise RuntimeError("connection closed during session.start handshake")
                continue
            data = json.loads(msg.data)
            if data.get("type") == "error":
                raise RuntimeError(f"session.start rejected: {data.get('message')}")
            if data.get("type") == "session.ready":
                ready = True
                break
            # status/activity frames before ready are fine — keep waiting
        if not ready:
            raise TimeoutError(
                f"no session.ready within {self.ready_timeout_s}s — Qwen upstream connect failed?"
            )
        self._session_started = True

        # Drain incoming messages so server-side rejections are visible in stats
        # instead of silently accumulating in the socket buffer.
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        """Track incoming messages; count error frames from the agent."""
        ws = self._ws
        if not ws:
            return
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "error":
                    self.stats.errors_received += 1
                    self.stats.last_errors.append(str(data.get("message", ""))[:200])
                    del self.stats.last_errors[:-5]
        except Exception:
            pass

    async def _close_connection(self) -> None:
        """Close WebSocket connection."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._session_started = False

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _feed_loop(self) -> None:
        """Main frame feeding loop."""
        while self._running:
            try:
                await self._ensure_connection()
                frame_data = await self._capture_frame()
                success = await self.inject_single_frame(frame_data)

                if not success:
                    print(f"Frame send failed (sent={self.stats.frames_sent}, failed={self.stats.frames_failed})")

                await asyncio.sleep(self.interval_s)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Feed loop error: {e}")
                self.stats.frames_failed += 1
                await asyncio.sleep(self.interval_s)

    async def _capture_frame(self) -> bytes:
        """Capture a single frame based on mode."""
        if self.mode == 'screen':
            return await self._capture_real_screenshot()
        else:
            return self._generate_synthetic_frame()

    async def _capture_real_screenshot(self) -> bytes:
        """Capture real screenshot via screen-capture-server.

        Downscales to `max_width` to match the production web client
        (omni-exp-client.html sends 640px-wide q0.7 JPEGs). Raw Retina
        captures are 1-2MB and have been observed to wedge the Qwen
        upstream; parity with the client keeps the test realistic.
        """
        if not HAS_AIOHTTP or not aiohttp:
            raise RuntimeError("aiohttp required for screen capture")

        url = f"http://localhost:{self.screen_capture_port}/capture?format=jpeg&silent=true"
        token = read_capture_token()
        if not token:
            raise RuntimeError(
                f"No capture token at {CAPTURE_TOKEN_PATH} — is screen-capture-server running?"
            )

        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        headers = {"X-Sutando-Capture-Token": token}
        async with self._session.get(url, headers=headers) as resp:
            data = await resp.json()
            if data.get('status') != 'ok' or not data.get('path'):
                raise RuntimeError(f"Screen capture failed: {data.get('error', 'unknown')}")

            # Read the captured file
            path = Path(data['path'])
            frame_bytes = path.read_bytes()

            # Clean up the temp file
            try:
                path.unlink()
            except Exception:
                pass

            return self._downscale(frame_bytes)

    def _downscale(self, jpeg: bytes) -> bytes:
        """Downscale a JPEG to `max_width` (no-op without PIL or if small)."""
        if not HAS_PIL or not Image:
            return jpeg
        try:
            img = Image.open(io.BytesIO(jpeg))
            if img.width <= self.max_width:
                return jpeg
            h = round(img.height * self.max_width / img.width)
            resample = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS')
            img = img.convert('RGB').resize((self.max_width, h), resample)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=70)
            return buf.getvalue()
        except Exception:
            return jpeg

    def _generate_synthetic_frame(self) -> bytes:
        """Generate a synthetic test pattern frame."""
        if not HAS_PIL or not Image or not ImageDraw or not ImageFont:
            return base64.b64decode(_FALLBACK_JPEG_B64)

        # Generate a test pattern with timestamp
        width, height = 640, 480
        img = Image.new('RGB', (width, height), color='#1a1a2e')
        draw = ImageDraw.Draw(img)

        # Draw colored blocks
        colors = ['#16213e', '#0f3460', '#533483', '#e94560']
        block_width = width // len(colors)
        for i, color in enumerate(colors):
            draw.rectangle(
                [i * block_width, 0, (i + 1) * block_width, height // 3],
                fill=color
            )

        # Add timestamp text
        timestamp_text = f"Test Frame #{self.stats.frames_sent + 1}\n{time.strftime('%H:%M:%S')}"
        try:
            # Try to use a default font
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
        except Exception:
            font = ImageFont.load_default()

        # Center the text
        bbox = draw.textbbox((0, 0), timestamp_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_x = (width - text_width) // 2
        text_y = height // 2

        draw.text((text_x, text_y), timestamp_text, fill='white', font=font)

        # Convert to JPEG bytes
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85)
        return buffer.getvalue()


def create_test_pattern(
    text: str = "Test",
    width: int = 640,
    height: int = 480,
    color: str = '#16213e'
) -> bytes:
    """Create a simple test pattern JPEG with custom text.

    Utility function for generating specific test frames in test cases.
    """
    if not HAS_PIL or not Image or not ImageDraw or not ImageFont:
        return base64.b64decode(_FALLBACK_JPEG_B64)

    img = Image.new('RGB', (width, height), color=color)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_x = (width - text_width) // 2
    text_y = height // 2

    draw.text((text_x, text_y), text, fill='white', font=font)

    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    return buffer.getvalue()
