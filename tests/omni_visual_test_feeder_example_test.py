#!/usr/bin/env python3
"""Example test demonstrating OmniVisualTestFeeder usage.

This test shows how to use the visual test feeder to provide controlled
screenshot input to the omni agent for testing visual processing capabilities.

Requires a running omni-exp agent (port 7090). The 'screen' mode tests
additionally require screen-capture-server (port 7900). Tests skip cleanly
when the services are down.

Run:
    .venv/bin/python3 tests/omni-visual-test-feeder-example.test.py
"""

import asyncio
import socket
import sys
from pathlib import Path
import pytest

# Add feeder directory to path for imports
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests" / "visual_feeder"))

from omni_visual_test_feeder import (
    OmniVisualTestFeeder,
    create_test_pattern,
    read_capture_token,
)


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("localhost", port)) == 0


@pytest.mark.asyncio
async def test_synthetic_frames_low_frequency():
    """Test synthetic frame generation at low frequency (cost-effective for tests)."""
    feeder = OmniVisualTestFeeder(
        mode='synthetic',
        interval_s=5.0,
        omni_port=7090,
    )

    try:
        await feeder.start()
        print("Feeder started. Waiting for 3 frames...")

        await feeder.wait_frames(3, timeout_s=45)

        stats = await feeder.stop()
        print(f"\nTest complete:")
        print(f"  Frames sent: {stats.frames_sent}")
        print(f"  Frames failed: {stats.frames_failed}")
        print(f"  Server errors: {stats.errors_received}")
        print(f"  Duration: {stats.duration_s:.1f}s")
        print(f"  Average FPS: {stats.avg_fps:.3f}")

        assert stats.frames_sent >= 3, f"Expected >= 3 frames, got {stats.frames_sent}"
        assert stats.frames_failed == 0, f"Expected 0 failures, got {stats.frames_failed}"
        # The agent rejects protocol errors with error frames — a feeder that
        # sends the wrong wire format now FAILS here instead of passing green.
        assert stats.errors_received == 0, f"Agent rejected frames: {stats.last_errors}"

    except Exception:
        await feeder.stop()
        raise


@pytest.mark.asyncio
async def test_manual_frame_injection():
    """Test manual single-frame injection (useful for specific test scenarios)."""
    feeder = OmniVisualTestFeeder(mode='synthetic', omni_port=7090)

    try:
        # Generate custom test frames
        frame1 = create_test_pattern("Test Frame 1", color='#16213e')
        frame2 = create_test_pattern("Test Frame 2", color='#0f3460')
        frame3 = create_test_pattern("Error State", color='#e94560')

        print("Injecting 3 custom test frames...")

        success1 = await feeder.inject_single_frame(frame1)
        await asyncio.sleep(1.2)  # respect the agent's 1fps upload gate

        success2 = await feeder.inject_single_frame(frame2)
        await asyncio.sleep(1.2)

        success3 = await feeder.inject_single_frame(frame3)
        await asyncio.sleep(1.0)  # let any server error frames arrive

        print(f"\nFrames injected: {feeder.stats.frames_sent}")
        print(f"Frames failed: {feeder.stats.frames_failed}")
        print(f"Server errors: {feeder.stats.errors_received}")

        assert feeder.stats.frames_sent == 3
        assert success1 and success2 and success3
        assert feeder.stats.errors_received == 0, f"Agent rejected: {feeder.stats.last_errors}"

    finally:
        await feeder.stop()


@pytest.mark.asyncio
async def test_real_screenshots_if_available():
    """Test real screenshot capture (requires screen-capture-server running)."""
    if not _port_open(7900) or not read_capture_token():
        print("Real screenshot test skipped: screen-capture-server not running")
        return

    feeder = OmniVisualTestFeeder(
        mode='screen',
        interval_s=5.0,
        omni_port=7090,
        screen_capture_port=7900,
    )

    try:
        await feeder.start()
        print("Real screenshot feeder started. Waiting for 2 frames...")

        await feeder.wait_frames(2, timeout_s=40)

        stats = await feeder.stop()
        print(f"\nReal screenshot test complete:")
        print(f"  Frames sent: {stats.frames_sent}")
        print(f"  Duration: {stats.duration_s:.1f}s")

        assert stats.frames_sent >= 2
        assert stats.errors_received == 0, f"Agent rejected: {stats.last_errors}"

    except Exception as e:
        print(f"Real screenshot test failed: {e}")
        await feeder.stop()
        raise


if __name__ == '__main__':
    if not _port_open(7090):
        print("SKIP: omni-exp agent not running on port 7090")
        sys.exit(0)

    print("=== Omni Visual Test Feeder Examples ===\n")

    print("1. Testing synthetic frames (low frequency)...")
    asyncio.run(test_synthetic_frames_low_frequency())

    print("\n2. Testing manual frame injection...")
    asyncio.run(test_manual_frame_injection())

    print("\n3. Testing real screenshots (if available)...")
    asyncio.run(test_real_screenshots_if_available())

    print("\n=== All tests complete ===")
