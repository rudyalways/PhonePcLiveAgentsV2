#!/usr/bin/env python3
"""
Standalone smoke test for DashScope Qwen Realtime — same WebSocket URL and auth as
`create_realtime_model()` when REALTIME_PROVIDER=qwen in livekit-agent.py.

Uses `process_base_url` from livekit-plugins-openai so the endpoint matches the agent.

Usage (from repo root):
  .venv-livekit/bin/python scripts/test-qwen-realtime.py
  .venv-livekit/bin/python scripts/test-qwen-realtime.py --prompt "Say the word hello."

Env (from .env or shell):
  DASHSCOPE_API_KEY   — required
  REALTIME_MODEL      — optional, default qwen3.5-omni-plus-realtime
  REALTIME_BASE_URL   — optional, default https://dashscope.aliyuncs.com/api-ws/v1/realtime
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent

# Mirror livekit-agent qwen defaults (see src/livekit-agent.py).
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3.5-omni-plus-realtime"


def _import_process_base_url():
    try:
        from livekit.plugins.openai.realtime.realtime_model import process_base_url
    except ImportError as e:
        print(
            "Could not import livekit.plugins.openai. Install the LiveKit venv:\n"
            "  python3 -m venv .venv-livekit && .venv-livekit/bin/pip install -r requirements-livekit.txt\n"
            f"  .venv-livekit/bin/python {Path(__file__).name}",
            file=sys.stderr,
        )
        raise SystemExit(1) from e
    return process_base_url


async def _run(
    *,
    prompt: str,
    recv_timeout_s: float,
    max_events: int,
) -> int:
    load_dotenv(REPO / ".env")
    api_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        print("DASHSCOPE_API_KEY is not set (.env or environment).", file=sys.stderr)
        return 1

    model = (os.environ.get("REALTIME_MODEL") or DEFAULT_MODEL).strip()
    base_url = (os.environ.get("REALTIME_BASE_URL") or DEFAULT_BASE_URL).strip()

    process_base_url = _import_process_base_url()
    url = process_base_url(
        base_url,
        model,
        is_azure=False,
        azure_deployment=None,
        api_version=None,
    )
    safe_url = url.split("?")[0] + "?model=<redacted>"
    print(f"WS target: {safe_url}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Sutando-scripts-test-qwen-realtime/1.0",
    }

    timeout = aiohttp.ClientTimeout(total=max(60.0, recv_timeout_s * 4))
    got_assistant_text: list[str] = []
    errors: list[str] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            ws = await session.ws_connect(url, headers=headers)
        except aiohttp.ClientError as e:
            print(f"WebSocket connect failed: {e}", file=sys.stderr)
            return 1

        async def recv_one() -> dict | None:
            msg = await asyncio.wait_for(ws.receive(), timeout=recv_timeout_s)
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    return json.loads(msg.data)
                except json.JSONDecodeError:
                    print(f"[non-json] {msg.data[:200]!r}")
                    return None
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                print(f"WS closed: {msg.extra}", file=sys.stderr)
                return None
            if msg.type == aiohttp.WSMsgType.ERROR:
                print(f"WS error: {ws.exception()}", file=sys.stderr)
                return None
            return None

        async def send_obj(obj: dict) -> None:
            await ws.send_str(json.dumps(obj, ensure_ascii=False))

        try:
            # Wait for session.created
            created = False
            for _ in range(max_events):
                raw = await recv_one()
                if raw is None:
                    break
                et = raw.get("type", "")
                print(f"  ← {et}")
                if et == "session.created":
                    created = True
                    break
                if et == "error":
                    errors.append(json.dumps(raw, ensure_ascii=False))
                    print(f"  error payload: {raw}", file=sys.stderr)
                    return 2

            if not created:
                print("Never received session.created.", file=sys.stderr)
                return 2

            # Align with livekit-agent: realtime session + text output for a simple turn.
            await send_obj(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "model": model,
                        "output_modalities": ["text"],
                        "instructions": "Reply briefly (one short sentence).",
                    },
                }
            )
            print("  → session.update (text output)")

            await send_obj(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                        ],
                    },
                }
            )
            print(f"  → conversation.item.create (user text: {prompt!r})")

            await send_obj({"type": "response.create"})
            print("  → response.create")

            done = False
            for _ in range(max_events):
                raw = await recv_one()
                if raw is None:
                    break
                et = raw.get("type", "")
                # High-signal lines
                if et in (
                    "response.text.delta",
                    "response.output_text.delta",
                    "response.audio_transcript.delta",
                    "response.output_audio_transcript.delta",
                ):
                    delta = raw.get("delta", "")
                    if delta:
                        got_assistant_text.append(str(delta))
                elif et in ("response.done", "response.completed"):
                    print(f"  ← {et}")
                    done = True
                    break
                elif et == "error":
                    errors.append(json.dumps(raw, ensure_ascii=False))
                    print(f"  ← error: {raw}", file=sys.stderr)
                    break
                elif et.startswith("response.") and "delta" not in et:
                    print(f"  ← {et}")
                else:
                    # Still log uncommon types briefly
                    if et not in ("session.updated", "rate_limits.updated", "input_audio_buffer.committed"):
                        print(f"  ← {et}")

            reply = "".join(got_assistant_text).strip()
            if reply:
                print("\n--- Assistant text (deltas concatenated) ---")
                print(reply)
            if done and reply:
                print("\nOK: received response with text.")
                return 0
            if errors:
                print("\nFAIL: server returned error events.", file=sys.stderr)
                return 2
            if done and not reply:
                print(
                    "\nWARN: response completed but no text deltas captured "
                    "(provider may use different event names; check raw logs above).",
                    file=sys.stderr,
                )
                return 0
            print("\nFAIL: timed out or connection closed before response.done.", file=sys.stderr)
            return 2
        finally:
            await ws.close()

    return 2


def main() -> int:
    p = argparse.ArgumentParser(description="Test DashScope Qwen Realtime WebSocket (OpenAI-compatible).")
    p.add_argument(
        "--prompt",
        default="Say exactly the word hello, nothing else.",
        help="User message sent as input_text",
    )
    p.add_argument(
        "--recv-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for each WebSocket message",
    )
    p.add_argument(
        "--max-events",
        type=int,
        default=200,
        help="Max server events to read (safety bound)",
    )
    args = p.parse_args()
    try:
        return asyncio.run(
            _run(
                prompt=args.prompt,
                recv_timeout_s=args.recv_timeout,
                max_events=args.max_events,
            )
        )
    except TimeoutError:
        print("Timeout waiting for WebSocket data.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    t0 = time.perf_counter()
    code = main()
    print(f"\n(elapsed {time.perf_counter() - t0:.1f}s)", file=sys.stderr)
    raise SystemExit(code)
