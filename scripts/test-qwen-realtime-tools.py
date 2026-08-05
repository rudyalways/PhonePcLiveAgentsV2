#!/usr/bin/env python3
"""DashScope Qwen Realtime **tool calling** smoke test.

Proves the vendor supports function calling on the documented WebSocket path
(independent of bodhi / LiveKit). Mirrors the session.update schema used by
`test-qwen-realtime-audio.py` but registers one tool and drives a text turn
that should invoke it.

This does NOT prove Sutando web voice (bodhi OpenAIRealtimeTransport) handles
tool follow-up — LiveKit has `qwen_realtime_compat.py` patches that web voice
lacks. See docs/realtime-provider-design.md §4.4.

Usage (from repo root):
  python3 scripts/test-qwen-realtime-tools.py
  python3 scripts/test-qwen-realtime-tools.py --prompt "Call the echo tool with message ping."

Env:
  DASHSCOPE_API_KEY   — required
  REALTIME_MODEL      — default qwen3.5-omni-plus-realtime
  REALTIME_BASE_URL   — default https://dashscope.aliyuncs.com/api-ws/v1/realtime

Exit 0 on: tool call observed + function output accepted + follow-up response.done
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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

ECHO_TOOL = {
    "type": "function",
    "name": "echo",
    "description": "Echo back a short message verbatim. Always use this when the user asks to echo.",
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message to echo back",
            }
        },
        "required": ["message"],
    },
}


def build_ws_url(base_url: str, model: str) -> str:
    parsed = urlparse(base_url)
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme or "wss")
    query = dict(parse_qsl(parsed.query))
    query["model"] = model
    return urlunparse(parsed._replace(scheme=scheme, query=urlencode(query)))


def event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _extract_function_call(raw: dict) -> tuple[str | None, str | None, str | None]:
    """Return (call_id, name, arguments_json) from assorted event shapes."""
    et = raw.get("type", "")

    # OpenAI GA / LiveKit style
    if et == "response.function_call_arguments.done":
        return raw.get("call_id"), raw.get("name"), raw.get("arguments")

    item = raw.get("item") if isinstance(raw.get("item"), dict) else None
    if item and item.get("type") == "function_call":
        return item.get("call_id") or item.get("id"), item.get("name"), item.get("arguments")

    # Qwen may nest under response.output_item.*
    resp = raw.get("response") if isinstance(raw.get("response"), dict) else None
    if resp:
        for output in resp.get("output") or []:
            if not isinstance(output, dict):
                continue
            if output.get("type") == "function_call":
                return (
                    output.get("call_id") or output.get("id"),
                    output.get("name"),
                    output.get("arguments"),
                )

    return None, None, None


async def run(args: argparse.Namespace) -> int:
    load_dotenv(REPO / ".env")
    api_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        print("DASHSCOPE_API_KEY is not set (.env or environment).", file=sys.stderr)
        return 1

    model = args.model or os.environ.get("REALTIME_MODEL") or DEFAULT_MODEL
    base_url = args.base_url or os.environ.get("REALTIME_BASE_URL") or DEFAULT_BASE_URL
    url = build_ws_url(base_url, model)
    print(f"WS target: {url.split('?')[0]}?model=<redacted>")

    flags = {
        "session_created": False,
        "session_updated": False,
        "tool_call_seen": False,
        "tool_output_sent": False,
        "followup_response_done": False,
        "ws_closed": False,
    }
    tool_call: dict[str, str] = {}
    errors: list[dict] = []
    interesting_types: set[str] = set()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Sutando-qwen-realtime-tools-smoke/1.0",
    }
    timeout = aiohttp.ClientTimeout(total=args.timeout_s + 30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        ws = await session.ws_connect(url, headers=headers)

        async def recv_json(wait_s: float) -> dict | None:
            if flags["ws_closed"] or ws.closed:
                flags["ws_closed"] = True
                return None
            msg = await asyncio.wait_for(ws.receive(), timeout=wait_s)
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
            if msg.type == aiohttp.WSMsgType.ERROR:
                flags["ws_closed"] = True
                print(f"  WS error: {ws.exception()}", file=sys.stderr)
                return None
            return None

        stop_reader = asyncio.Event()

        async def reader() -> None:
            while not stop_reader.is_set() and not flags["ws_closed"]:
                try:
                    raw = await recv_json(1)
                except TimeoutError:
                    continue
                if not raw:
                    if flags["ws_closed"]:
                        break
                    continue

                et = str(raw.get("type", ""))
                interesting_types.add(et)

                if et in (
                    "session.created",
                    "session.updated",
                    "response.created",
                    "response.done",
                    "response.function_call_arguments.done",
                    "response.output_item.added",
                    "conversation.item.created",
                    "error",
                ) or "function_call" in et or "tool" in et:
                    snippet = json.dumps(
                        {k: v for k, v in raw.items() if k not in ("delta", "audio")},
                        ensure_ascii=False,
                    )
                    if len(snippet) > 400:
                        snippet = snippet[:400] + "…"
                    print(f"  <- {et} {snippet}")

                if et == "session.created":
                    flags["session_created"] = True
                elif et == "session.updated":
                    flags["session_updated"] = True
                elif et == "error":
                    errors.append(raw)

                if et == "response.function_call_arguments.done":
                    call_id, name, arguments = _extract_function_call(raw)
                    if name and call_id:
                        flags["tool_call_seen"] = True
                        tool_call.update(
                            {
                                "call_id": call_id,
                                "name": name,
                                "arguments": arguments or "{}",
                            }
                        )
                        print(
                            f"  ** tool call done: name={name!r} call_id={call_id!r} "
                            f"args={arguments!r}"
                        )
                        if not flags["tool_output_sent"]:
                            output_payload = {
                                "type": "conversation.item.create",
                                "event_id": event_id("tool_output"),
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": json.dumps(
                                        {"echoed": arguments, "ok": True},
                                        ensure_ascii=False,
                                    ),
                                },
                            }
                            await ws.send_str(json.dumps(output_payload, ensure_ascii=False))
                            flags["tool_output_sent"] = True
                            print(f"  -> function_call_output call_id={call_id!r}")
                            await asyncio.sleep(0.5)
                            await ws.send_str(
                                json.dumps(
                                    {
                                        "type": "response.create",
                                        "event_id": event_id("response_create_after_tool"),
                                        "response": {},
                                    },
                                    ensure_ascii=False,
                                )
                            )
                            print("  -> response.create (after tool output)")
                    continue

                call_id, name, arguments = _extract_function_call(raw)
                if name and not flags["tool_call_seen"]:
                    print(f"  .. function_call started: name={name!r} call_id={call_id!r}")

                if et == "response.done" and flags["tool_output_sent"]:
                    # Follow-up after we submitted function_call_output
                    resp = raw.get("response") if isinstance(raw.get("response"), dict) else {}
                    outputs = resp.get("output") or []
                    only_tool = (
                        len(outputs) == 1
                        and isinstance(outputs[0], dict)
                        and outputs[0].get("type") == "function_call"
                    )
                    if not only_tool or resp.get("status") == "completed":
                        flags["followup_response_done"] = True

        reader_task = asyncio.create_task(reader())

        # Wait for session.created (via reader)
        for _ in range(40):
            if flags["session_created"] or flags["ws_closed"] or errors:
                break
            await asyncio.sleep(0.25)

        if errors:
            print(json.dumps(errors[0], ensure_ascii=False, indent=2), file=sys.stderr)
            stop_reader.set()
            await reader_task
            return 2

        if not flags["session_created"]:
            print("FAIL: never received session.created (WS may have closed — check REALTIME_BASE_URL / key / region)", file=sys.stderr)
            stop_reader.set()
            await reader_task
            return 2

        session_update = {
            "type": "session.update",
            "event_id": event_id("session_update"),
            "session": {
                "modalities": ["text", "audio"],
                "voice": args.voice,
                "instructions": (
                    "You are a test assistant. When the user asks to echo something, "
                    "you MUST call the echo tool with their message. Do not answer without calling the tool."
                ),
                "input_audio_format": "pcm",
                "output_audio_format": "pcm",
                "tools": [ECHO_TOOL],
                "tool_choice": "auto",
                # enable_search must stay OFF — mutually exclusive with tools per Alibaba docs
            },
        }
        await ws.send_str(json.dumps(session_update, ensure_ascii=False))
        print("  -> session.update (tools=[echo], enable_search=off)")

        await asyncio.sleep(args.post_update_wait_s)

        user_text = args.prompt
        item_id = event_id("user_item")
        await ws.send_str(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "event_id": event_id("item_create"),
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_text}],
                    },
                },
                ensure_ascii=False,
            )
        )
        print(f"  -> conversation.item.create (text turn): {user_text!r}")

        await ws.send_str(
            json.dumps(
                {"type": "response.create", "event_id": event_id("response_create"), "response": {}},
                ensure_ascii=False,
            )
        )
        print("  -> response.create")

        deadline = time.monotonic() + args.timeout_s
        while time.monotonic() < deadline:
            if errors or flags["ws_closed"]:
                break
            if flags["followup_response_done"]:
                break
            await asyncio.sleep(0.25)

        stop_reader.set()
        await reader_task
        await ws.close()

    print("\n--- Result flags ---")
    for key, value in flags.items():
        print(f"{key}: {value!r}")
    if tool_call:
        print(f"tool_call: {tool_call!r}")
    print(f"event types seen ({len(interesting_types)}): {', '.join(sorted(interesting_types))}")

    if errors:
        print("\nFAIL: server returned error event(s):", file=sys.stderr)
        for err in errors:
            print(json.dumps(err, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    missing = [k for k, v in flags.items() if not v and k != "ws_closed"]
    if missing:
        print(f"\nFAIL: missing: {', '.join(missing)}", file=sys.stderr)
        if not flags["tool_call_seen"]:
            print(
                "Hint: vendor may use different function_call event names — inspect log above.",
                file=sys.stderr,
            )
        return 2

    print("\nOK: Qwen Realtime tool register → call → output → follow-up path works at vendor layer.")
    print(
        "Next: verify bodhi web voice path (no qwen_realtime_compat.py) — see design doc §4.4.",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Qwen Realtime tool calling path.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--prompt",
        default="Please call the echo tool with message hello-qwen-tools.",
        help="User text turn sent via conversation.item.create",
    )
    parser.add_argument("--voice", default=os.environ.get("QWEN_REALTIME_VOICE", "Ethan"))
    parser.add_argument("--timeout-s", type=float, default=45.0)
    parser.add_argument("--post-update-wait-s", type=float, default=1.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
