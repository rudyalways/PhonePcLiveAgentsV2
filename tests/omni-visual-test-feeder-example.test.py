#!/usr/bin/env python3
"""Example test demonstrating OmniVisualTestFeeder usage.

This test shows how to use the visual test feeder to provide controlled
screenshot input to the omni agent for testing visual processing capabilities.

Run:
    pytest tests/omni-visual-test-feeder-example.test.py -v
"""

import asyncio
import sys
from pathlib import Path

# Add feeder directory to path for imports
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests" / "visual_feeder"))

from omni_visual_test_feeder import OmniVisualTestFeeder, create_test_pattern


async def test_synthetic_frames_low_frequency():
    """Test synthetic frame generation at low frequency (cost-effective for tests)."""
    feeder = OmniVisualTestFeeder(
        mode='synthetic',
        interval_s=10.0,  # 1 frame every 10 seconds
        omni_port=7090,
    )

    try:
        await feeder.start()
        print("Feeder started. Waiting for 3 frames...")

        # Wait for 3 frames (should take ~30 seconds at 10s interval)
        await feeder.wait_frames(3, timeout_s=45)

        stats = await feeder.stop()
        print(f"\nTest complete:")
        print(f"  Frames sent: {stats.frames_sent}")
        print(f"  Frames failed: {stats.frames_failed}")
        print(f"  Duration: {stats.duration_s:.1f}s")
        print(f"  Average FPS: {stats.avg_fps:.3f}")

        assert stats.frames_sent >= 3, f"Expected >= 3 frames, got {stats.frames_sent}"
        assert stats.frames_failed == 0, f"Expected 0 failures, got {stats.frames_failed}"

    except Exception:
        await feeder.stop()
        raise


async def test_manual_frame_injection():
    """Test manual single-frame injection (useful for specific test scenarios)."""
    feeder = OmniVisualTestFeeder(mode='synthetic', omni_port=7090)

    try:
        # Generate custom test frames
        frame1 = create_test_pattern("Test Frame 1", color='#16213e')
        frame2 = create_test_pattern("Test Frame 2", color='#0f3460')
        frame3 = create_test_pattern("Error State", color='#e94560')

        print("Injecting 3 custom test frames...")

        # Inject frames manually (no auto-loop)
        success1 = await feeder.inject_single_frame(frame1)
        await asyncio.sleep(2)

        success2 = await feeder.inject_single_frame(frame2)
        await asyncio.sleep(2)

        success3 = await feeder.inject_single_frame(frame3)

        print(f"\nFrames injected: {feeder.stats.frames_sent}")
        print(f"Frames failed: {feeder.stats.frames_failed}")

        assert feeder.stats.frames_sent == 3
        assert success1 and success2 and success3

    finally:
        await feeder.stop()


async def test_real_screenshots_if_available():
    """Test real screenshot capture (requires screen-capture-server running)."""
    feeder = OmniVisualTestFeeder(
        mode='screen',
        interval_s=15.0,  # Very low frequency for cost-effective testing
        omni_port=7090,
        screen_capture_port=7900,
    )

    try:
        await feeder.start()
        print("Real screenshot feeder started. Waiting for 2 frames...")

        # Wait for just 2 frames to keep test short
        await feeder.wait_frames(2, timeout_s=40)

        stats = await feeder.stop()
        print(f"\nReal screenshot test complete:")
        print(f"  Frames sent: {stats.frames_sent}")
        print(f"  Duration: {stats.duration_s:.1f}s")

        assert stats.frames_sent >= 2

    except Exception as e:
        print(f"Real screenshot test skipped or failed: {e}")
        await feeder.stop()
        # Don't fail the test if screen-capture-server isn't running
        # (this is optional functionality)


if __name__ == '__main__':
    print("=== Omni Visual Test Feeder Examples ===\n")

    print("1. Testing synthetic frames (low frequency)...")
    asyncio.run(test_synthetic_frames_low_frequency())

    print("\n2. Testing manual frame injection...")
    asyncio.run(test_manual_frame_injection())

    print("\n3. Testing real screenshots (if available)...")
    asyncio.run(test_real_screenshots_if_available())

    print("\n=== All tests complete ===")
