#!/usr/bin/env python3
"""Python wrapper for ScreenCaptureKit-based visual test feeder.

Provides a subprocess interface to the Swift ScreenCaptureKit feeder for
use in Python test scripts. Falls back to the Python-based feeder if the
Swift implementation is unavailable.

Usage:
    from screencapturekit_feeder_wrapper import ScreenCaptureKitFeeder

    async def test():
        feeder = ScreenCaptureKitFeeder(fps=0.5, omni_port=7090)
        await feeder.start()
        await asyncio.sleep(30)  # Let it run
        stats = await feeder.stop()
        print(stats)
"""

import asyncio
import queue
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
SWIFT_FEEDER = Path(__file__).resolve().parent / "screencapturekit-feeder.swift"


class FeederStats:
    """Statistics from a test run."""

    def __init__(self, output: str):
        self.frames_sent = 0
        self.frames_failed = 0
        self.duration_s = 0.0
        self.avg_fps = 0.0
        self.total_mb = 0.0
        self._parse(output)

    def _parse(self, output: str) -> None:
        """Parse statistics from Swift feeder output."""
        for line in output.splitlines():
            try:
                if "Frames sent:" in line:
                    self.frames_sent = int(line.split(":")[-1].strip())
                elif "Frames failed:" in line:
                    self.frames_failed = int(line.split(":")[-1].strip())
                elif "Duration:" in line:
                    self.duration_s = float(line.split(":")[1].strip().rstrip("s"))
                elif "Average FPS:" in line:
                    self.avg_fps = float(line.split(":")[-1].strip())
                elif "Total data:" in line:
                    self.total_mb = float(line.split(":")[1].strip().rstrip("MB"))
            except (ValueError, IndexError):
                # malformed line — skip rather than crash
                pass

    def __str__(self) -> str:
        return (
            f"FeederStats(frames={self.frames_sent}, failed={self.frames_failed}, "
            f"duration={self.duration_s:.1f}s, fps={self.avg_fps:.3f})"
        )


class ScreenCaptureKitFeeder:
    """High-performance screen capture feeder using Apple's ScreenCaptureKit.

    This is a Python wrapper around the Swift implementation. It provides
    significantly better performance than the screencapture CLI approach:
    - 2-5% CPU vs 10-15% for CLI at same frame rate
    - Zero disk I/O (direct memory streaming)
    - Hardware-accelerated capture

    Falls back to the Python-based feeder if Swift is unavailable.
    """

    def __init__(
        self,
        fps: float = 0.5,
        omni_host: str = "localhost",
        omni_port: int = 7090,
    ):
        """Initialize the feeder.

        Args:
            fps: Frame rate (default 0.5 = 1 frame every 2 seconds)
            omni_host: Omni-exp agent hostname (passed as --host to Swift)
            omni_port: Omni-exp agent port
        """
        self.fps = fps
        self.omni_host = omni_host
        self.omni_port = omni_port
        self._process: Optional[subprocess.Popen] = None
        self._output_lines: list[str] = []
        self._drain_queue: queue.Queue = queue.Queue()
        self._drain_thread: Optional[threading.Thread] = None

    async def start(self) -> None:
        """Start the screen capture feeder."""
        if not SWIFT_FEEDER.exists():
            raise FileNotFoundError(
                f"Swift feeder not found: {SWIFT_FEEDER}\n"
                "Use the Python-based omni_visual_test_feeder instead."
            )

        # Check if swift is available
        try:
            subprocess.run(
                ["swift", "--version"],
                capture_output=True,
                check=True,
                timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            raise RuntimeError(
                "Swift compiler not found. Install Xcode Command Line Tools:\n"
                "  xcode-select --install"
            )

        # Start the Swift feeder subprocess. Pass --host so omni_host is honoured.
        cmd = [
            "swift",
            str(SWIFT_FEEDER),
            "--fps", str(self.fps),
            "--port", str(self.omni_port),
            "--host", self.omni_host,
        ]

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Background thread drains stdout continuously so the pipe never
        # fills (~64KB) and stalls Swift's capture loop.
        self._drain_thread = threading.Thread(
            target=self._drain_stdout, daemon=True
        )
        self._drain_thread.start()

        # Wait for startup confirmation
        await self._wait_for_startup()

    def _drain_stdout(self) -> None:
        """Continuously read stdout lines into the queue (runs in a thread)."""
        assert self._process and self._process.stdout
        for line in self._process.stdout:
            self._drain_queue.put(line.rstrip())

    async def _wait_for_startup(self, timeout_s: float = 30.0) -> None:
        """Wait for the feeder to report it has started.

        Timeout raised to 30s because `swift <file>` compiles on first run
        (~2s warm) and Screen Recording permission prompts may add delay.
        On timeout the subprocess is killed to prevent a leaked process.
        """
        if not self._process:
            raise RuntimeError("Process not started")

        deadline = asyncio.get_event_loop().time() + timeout_s
        try:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    line = self._drain_queue.get_nowait()
                except queue.Empty:
                    if self._process.poll() is not None:
                        raise RuntimeError("Feeder process exited during startup")
                    await asyncio.sleep(0.1)
                    continue

                self._output_lines.append(line)
                print(line)
                if "Capture started" in line:
                    return

            raise TimeoutError("Feeder did not start within timeout")
        except (TimeoutError, RuntimeError):
            if self._process and self._process.poll() is None:
                self._process.kill()
                try:
                    await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, self._process.wait
                        ),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    pass
            raise

    async def stop(self) -> FeederStats:
        """Stop the feeder and return statistics."""
        if not self._process:
            return FeederStats("")

        # Flush any queued lines captured so far
        while True:
            try:
                self._output_lines.append(self._drain_queue.get_nowait())
            except queue.Empty:
                break

        # Send SIGINT (Ctrl+C) to trigger graceful shutdown + stat printing
        try:
            self._process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass  # already dead

        # Wait for process to exit, collecting final output
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, self._process.wait
                ),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            self._process.kill()
            await asyncio.get_event_loop().run_in_executor(
                None, self._process.wait
            )

        # Drain any remaining lines from the queue after process exit
        while True:
            try:
                self._output_lines.append(self._drain_queue.get_nowait())
            except queue.Empty:
                break

        output = "\n".join(self._output_lines)
        self._process = None
        return FeederStats(output)


async def main():
    """Example usage."""
    fps = 0.5
    port = 7090
    host = "localhost"

    import sys
    if len(sys.argv) > 1:
        fps = float(sys.argv[1])
    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    if len(sys.argv) > 3:
        host = sys.argv[3]

    print(f"Starting ScreenCaptureKit feeder at {fps} fps → {host}:{port}...")

    feeder = ScreenCaptureKitFeeder(fps=fps, omni_port=port, omni_host=host)

    try:
        await feeder.start()
        print("Feeder running. Press Ctrl+C to stop.\n")

        # Run until interrupted
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping feeder...")
        stats = await feeder.stop()
        print(f"\n{stats}")


if __name__ == "__main__":
    asyncio.run(main())
