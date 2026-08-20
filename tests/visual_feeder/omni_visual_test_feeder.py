#!/usr/bin/env python3
"""Test framework component for feeding visual input to omni agent.

Provides controlled screenshot capture for testing omni agent visual processing:
- Configurable frame rate (default: very low for tests)
- Mock frame generation (synthetic test images)
- Real screenshot capture via screen-capture-server
- Frame injection into omni agent's image queue

Usage:
    # Real screenshots at 1 frame every 10 seconds
    feeder = OmniVisualTestFeeder(mode='screen', interval_s=10)
    await feeder.start(omni_agent_session)
    await feeder.wait_frames(5)  # Wait until 5 frames captured
    await feeder.stop()

    # Synthetic test frames (no screen capture needed)
    feeder = OmniVisualTestFeeder(mode='synthetic')
    await feeder.inject_frame(session, create_test_pattern())
"""

import asyncio
import base64
import io
import time
from dataclasses import dataclass
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


@dataclass
class FrameStats:
    """Statistics for a test run."""
    frames_sent: int = 0
    frames_failed: int = 0
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
    - Real screenshots via screen-capture-server (port 7900)
    - Synthetic test pattern generation (no screen capture needed)
    - Frame injection into omni-exp WebSocket
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
    ):
        """Initialize the test feeder.

        Args:
            mode: 'screen' for real screenshots, 'synthetic' for test patterns
            interval_s: Seconds between frames (default 2s = 1 frame every 2 seconds)
            omni_host: Omni-exp-agent hostname
            omni_port: Omni-exp-agent port
            screen_capture_port: Screen capture server port
        """
        self.mode = mode
        self.interval_s = interval_s
        self.omni_host = omni_host
        self.omni_port = omni_port
        self.screen_capture_port = screen_capture_port

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._ws: Optional[ClientWebSocketResponse] = None  # type: ignore
        self._session: Optional[ClientSession] = None  # type: ignore
        self.stats = FrameStats()

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
        if not self._running:
            return self.stats

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

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
        """Inject a single frame into omni agent (one-shot, for manual test control)."""
        try:
            await self._ensure_connection()
            if not self._ws:
                return False

            # Send as WebSocket binary message (JPEG bytes)
            await self._ws.send_bytes(frame_data)
            self.stats.frames_sent += 1
            return True
        except Exception as e:
            print(f"Frame injection failed: {e}")
            self.stats.frames_failed += 1
            return False

    async def _ensure_connection(self) -> None:
        """Ensure WebSocket connection to omni-exp-agent is established."""
        if not HAS_AIOHTTP or not aiohttp:
            raise RuntimeError("aiohttp required")

        if self._ws and not self._ws.closed:
            return

        if not self._session:
            self._session = aiohttp.ClientSession()

        # Connect to omni-exp WebSocket endpoint
        # Note: omni-exp uses WSS (TLS). For test, may need to disable cert verification.
        ws_url = f"wss://{self.omni_host}:{self.omni_port}/ws"
        try:
            self._ws = await self._session.ws_connect(
                ws_url,
                ssl=False,  # Test-only: skip cert verification
            )
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            raise

    async def _close_connection(self) -> None:
        """Close WebSocket connection."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        if self._session:
            await self._session.close()
        self._session = None

    async def _feed_loop(self) -> None:
        """Main frame feeding loop."""
        await self._ensure_connection()

        while self._running:
            try:
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
        """Capture real screenshot via screen-capture-server."""
        if not HAS_AIOHTTP or not aiohttp:
            raise RuntimeError("aiohttp required for screen capture")

        url = f"http://localhost:{self.screen_capture_port}/capture?format=jpeg&silent=true"

        if not self._session:
            self._session = aiohttp.ClientSession()

        async with self._session.get(url) as resp:
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

            return frame_bytes

    def _generate_synthetic_frame(self) -> bytes:
        """Generate a synthetic test pattern frame."""
        if not HAS_PIL or not Image or not ImageDraw or not ImageFont:
            # Fallback: minimal 1x1 JPEG (smallest valid JPEG)
            return base64.b64decode(
                '/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB'
                'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wBDAQEBAQEBAQEBAQEBAQEBAQEBAQEB'
                'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAARCAABAAEDASIA'
                'AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEB'
                'AQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwA/wAA'
            )

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
        text_height = bbox[3] - bbox[1]
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
        # Return minimal 1x1 JPEG
        return base64.b64decode(
            '/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB'
            'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wBDAQEBAQEBAQEBAQEBAQEBAQEBAQEB'
            'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAARCAABAAEDASIA'
            'AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEB'
            'AQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwA/wAA'
        )

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
