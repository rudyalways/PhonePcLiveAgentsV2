#!/usr/bin/env python3
"""Qwen append_image must send audio immediately before every image (DashScope)."""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import unittest
from unittest.mock import AsyncMock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
from omni_exp_provider_qwen import QwenOmniSession  # noqa: E402


class TestImageAudioOrder(unittest.TestCase):
    def test_append_image_always_pads_audio_first(self):
        async def run():
            s = QwenOmniSession(api_key="k", on_event=lambda _e: None, instructions="x")
            sent: list[str] = []

            async def capture(payload):
                sent.append(payload["type"])

            s._send = capture  # type: ignore[method-assign]
            s._audio_sent = True  # session already had audio — still must pad
            jpeg = b"\xff\xd8\xff" + b"\x00" * 32
            await s.append_image(jpeg)
            self.assertEqual(
                sent,
                ["input_audio_buffer.append", "input_image_buffer.append"],
            )

        asyncio.run(run())

    def test_concurrent_images_serialize_audio_before_each(self):
        async def run():
            s = QwenOmniSession(api_key="k", on_event=lambda _e: None, instructions="x")
            sent: list[str] = []
            gate = asyncio.Event()
            in_flight = 0
            max_in_flight = 0

            async def capture(payload):
                nonlocal in_flight, max_in_flight
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                sent.append(payload["type"])
                if payload["type"] == "input_audio_buffer.append":
                    await gate.wait()
                in_flight -= 1

            s._send = capture  # type: ignore[method-assign]
            jpeg = b"\xff\xd8\xff" + b"\x00" * 8
            t1 = asyncio.create_task(s.append_image(jpeg))
            t2 = asyncio.create_task(s.append_image(jpeg))
            await asyncio.sleep(0.01)
            gate.set()
            await asyncio.gather(t1, t2)
            # Lock keeps only one append_* critical section active.
            self.assertEqual(max_in_flight, 1)
            self.assertEqual(sent.count("input_audio_buffer.append"), 2)
            self.assertEqual(sent.count("input_image_buffer.append"), 2)
            for i in range(0, 4, 2):
                self.assertEqual(sent[i], "input_audio_buffer.append")
                self.assertEqual(sent[i + 1], "input_image_buffer.append")

        asyncio.run(run())

    def test_append_audio_sets_flag(self):
        async def run():
            s = QwenOmniSession(api_key="k", on_event=lambda _e: None, instructions="x")
            s._send = AsyncMock()
            await s.append_audio(b"\x00\x00" * 10)
            self.assertTrue(s._audio_sent)
            # payload audio is base64 of the pcm
            args = s._send.await_args.args[0]
            self.assertEqual(args["type"], "input_audio_buffer.append")
            self.assertEqual(
                base64.b64decode(args["audio"]),
                b"\x00\x00" * 10,
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
