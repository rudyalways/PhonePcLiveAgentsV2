#!/usr/bin/env python3
"""DashScope Qwen Realtime vision smoke test (Phase 0).

Proves audio-first → input_image_buffer.append on the vendor WebSocket.
Independent of bodhi / voice-agent vision-adapter.

Usage:
  python3 scripts/test-qwen-realtime-vision.py

Exit 0 when: session created → audio append → image append → response.done
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import struct
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3.5-omni-plus-realtime"


def build_ws_url(base_url: str, model: str) -> str:
    parsed = urlparse(base_url)
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme or "wss")
    query = dict(parse_qsl(parsed.query))
    query["model"] = model
    return urlunparse(parsed._replace(scheme=scheme, query=urlencode(query)))


def event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def minimal_jpeg_bytes() -> bytes:
    """Tiny valid JPEG (~600 bytes) for vendor vision append."""
    # 8x8 red pixel — generated offline, embedded for spike portability.
    return base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxISEhUQEhIVFhUVFRUVFRUVFRUWFhUV"
        "FRUYHSggGBolGxUVITEhJSkrLi4uFx8zODMsNygtLisBCgoKDg0OGxAQGy0lICUtLS0t"
        "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEI"
        "AAgACAMBIgACEQEDEQH/xAAXAAADAQAAAAAAAAAAAAAAAAAAAQID/8QAFhEBAQEAAAAA"
        "AAAAAAAAAAAAAAAB/9oADAMBAAIQAxAAAAGwP//EABQQAQAAAAAAAAAAAAAAAAAAABD"
        "/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAQE/AKf/xAAUEQEAAAAAAAAAAAAA"
        "AAAAAAAAAAD/2gAIAQIBAT8Af//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQMBAT8A"
        "f//Z"
    )


def silence_pcm16_ms(ms: int, sample_rate: int = 16000) -> bytes:
    n = int(sample_rate * ms / 1000)
    return struct.pack(f"<{n}h", *([0] * n))


async def run(args: argparse.Namespace) -> int:
    load_dotenv(REPO / ".env")
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("FAIL: DASHSCOPE_API_KEY required", file=sys.stderr)
        return 2

    base = args.base_url or os.environ.get("REALTIME_BASE_URL", DEFAULT_BASE_URL)
    if not base.rstrip("/").endswith("/realtime"):
        base = base.rstrip("/") + "/realtime"
    model = (
        args.model
        or os.environ.get("QWEN_OMNI_REALTIME_MODEL")
        or os.environ.get("REALTIME_MODEL")
        or DEFAULT_MODEL
    )
    url = build_ws_url(base, model)
    print(f"model: {model}")
    print(f"Connecting: {url.split('?')[0]}?model=<redacted>")

    flags = {
        "session_created": False,
        "audio_appended": False,
        "image_appended": False,
        "response_done": False,
        "ws_closed": False,
    }
    errors: list[dict] = []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Sutando-qwen-realtime-vision-smoke/1.0",
    }

    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(url, headers=headers)

        async def recv_json(wait_s: float) -> dict | None:
            if flags["ws_closed"] or ws.closed:
                flags["ws_closed"] = True
                return None
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=wait_s)
            except TimeoutError:
                return None
            if msg.type == aiohttp.WSMsgType.TEXT:
                return json.loads(msg.data)
            if msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            ):
                flags["ws_closed"] = True
                print(f"  WS closed: {msg.extra}", file=sys.stderr)
                return None
            return None

        stop = asyncio.Event()

        async def reader() -> None:
            while not stop.is_set() and not flags["ws_closed"]:
                raw = await recv_json(1)
                if not raw:
                    if flags["ws_closed"]:
                        break
                    continue
                et = raw.get("type", "")
                if et == "error":
                    errors.append(raw)
                if et == "session.created":
                    flags["session_created"] = True
                    print("  <- session.created")
                if et == "response.done":
                    flags["response_done"] = True
                    print("  <- response.done")

        reader_task = asyncio.create_task(reader())

        for _ in range(40):
            if flags["session_created"] or flags["ws_closed"] or errors:
                break
            await asyncio.sleep(0.25)

        if not flags["session_created"]:
            print("FAIL: no session.created", file=sys.stderr)
            stop.set()
            await reader_task
            return 2

        await ws.send_str(
            json.dumps(
                {
                    "type": "session.update",
                    "event_id": event_id("session_update"),
                    "session": {
                        "modalities": ["text", "audio"],
                        "voice": args.voice,
                        "instructions": "Describe briefly what you see in the image.",
                        "input_audio_format": "pcm",
                        "output_audio_format": "pcm",
                    },
                }
            )
        )
        print("  -> session.update")
        await asyncio.sleep(args.post_update_wait_s)

        pcm = silence_pcm16_ms(200)
        await ws.send_str(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "event_id": event_id("audio_append"),
                    "audio": base64.b64encode(pcm).decode("ascii"),
                }
            )
        )
        flags["audio_appended"] = True
        print("  -> input_audio_buffer.append (200ms silence)")

        jpeg = minimal_jpeg_bytes()
        await ws.send_str(
            json.dumps(
                {
                    "type": "input_image_buffer.append",
                    "event_id": event_id("image_append"),
                    "image": base64.b64encode(jpeg).decode("ascii"),
                }
            )
        )
        flags["image_appended"] = True
        print(f"  -> input_image_buffer.append ({len(jpeg)} bytes JPEG)")

        await ws.send_str(
            json.dumps(
                {
                    "type": "response.create",
                    "event_id": event_id("response_create"),
                    "response": {},
                }
            )
        )
        print("  -> response.create")

        deadline = time.monotonic() + args.timeout_s
        while time.monotonic() < deadline:
            if errors or flags["ws_closed"]:
                break
            if flags["response_done"]:
                break
            await asyncio.sleep(0.25)

        stop.set()
        await reader_task
        await ws.close()

    print("\n--- Result flags ---")
    for k, v in flags.items():
        print(f"{k}: {v!r}")

    if errors:
        print("FAIL: server errors:", json.dumps(errors, indent=2), file=sys.stderr)
        return 2

    missing = [k for k, v in flags.items() if not v and k != "ws_closed"]
    if missing:
        print(f"FAIL: missing {missing}", file=sys.stderr)
        return 2

    print("\nOK: Qwen audio-first → image append → response path works at vendor layer.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Test Qwen Realtime vision append path.")
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--voice", default=os.environ.get("QWEN_REALTIME_VOICE", "Ethan"))
    p.add_argument("--timeout-s", type=float, default=45.0)
    p.add_argument("--post-update-wait-s", type=float, default=1.0)
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
