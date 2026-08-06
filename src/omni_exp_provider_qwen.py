"""Qwen Omni Realtime WebSocket adapter for omni-agent."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp

from qwen_realtime_compat import (
    qwen_default_output_voice,
    qwen_input_transcription_config,
    qwen_omni_realtime_model,
    qwen_turn_detection_config,
)

logger = logging.getLogger("omni-exp-agent.qwen")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api-ws/v1/realtime"

OnEvent = Callable[[dict[str, Any]], Awaitable[None] | None]


def _build_ws_url(base_url: str, model: str) -> str:
    parsed = urlparse(base_url)
    # Allow https://.../api-ws/v1 or full .../realtime
    path = parsed.path.rstrip("/")
    if not path.endswith("/realtime"):
        path = path + "/realtime"
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme or "wss")
    query = dict(parse_qsl(parsed.query))
    query["model"] = model
    return urlunparse(parsed._replace(scheme=scheme, path=path, query=urlencode(query)))


def _eid(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


class QwenOmniSession:
    def __init__(
        self,
        *,
        api_key: str,
        on_event: OnEvent,
        instructions: str,
        model: str | None = None,
        base_url: str | None = None,
        voice: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        self.api_key = api_key
        self.on_event = on_event
        self.instructions = instructions
        self.model = model or qwen_omni_realtime_model()
        raw_base = base_url or os.environ.get("REALTIME_BASE_URL") or DEFAULT_BASE_URL
        self.base_url = raw_base
        self.voice = voice or qwen_default_output_voice()
        self.tools = tools or []
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        # Session-level heuristic only — Qwen can still reject image-after-commit
        # unless audio lands immediately before the image (see append_image).
        self._audio_sent = False
        self._send_lock = asyncio.Lock()
        self.responding = False

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        url = _build_ws_url(self.base_url, self.model)
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Sutando-omni-exp-agent/0.1",
            },
        )
        # Drain session.created before starting the shared reader (avoid race).
        await self._wait_type("session.created", timeout_s=15)
        self._reader = asyncio.create_task(self._read_loop())
        await self._session_update()
        tool_names = [
            str(t.get("name") or (t.get("function") or {}).get("name") or "?")
            for t in self.tools
            if isinstance(t, dict)
        ]
        logger.info(
            "Qwen Omni session ready model=%s tools=%s",
            self.model,
            tool_names or "[]",
        )

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            self._reader = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("Qwen WS not connected")
        await self._ws.send_str(json.dumps(payload, ensure_ascii=False))

    async def _wait_type(self, typ: str, timeout_s: float) -> dict[str, Any]:
        assert self._ws
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            msg = await asyncio.wait_for(self._ws.receive(), timeout=max(0.1, deadline - time.time()))
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if data.get("type") == typ:
                return data
            if data.get("type") == "error":
                raise RuntimeError(f"Qwen error: {data}")
        raise TimeoutError(f"timed out waiting for {typ}")

    async def _session_update(self) -> None:
        td = qwen_turn_detection_config()
        session: dict[str, Any] = {
            "modalities": ["text", "audio"],
            "voice": self.voice,
            "instructions": self.instructions,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "turn_detection": dict(td),
        }
        tx = qwen_input_transcription_config()
        if tx:
            session["input_audio_transcription"] = dict(tx)
        if self.tools:
            session["tools"] = self.tools
            session["tool_choice"] = "auto"
        await self._send(
            {
                "type": "session.update",
                "event_id": _eid("session_update"),
                "session": session,
            }
        )

    async def send_function_output(self, call_id: str, output: dict[str, Any] | str) -> None:
        body = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        logger.info("TOOL_OUTPUT call_id=%s output=%s", call_id, body)
        await self._send(
            {
                "type": "conversation.item.create",
                "event_id": _eid("tool_out"),
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": body,
                },
            }
        )
        await self._send(
            {
                "type": "response.create",
                "event_id": _eid("response_after_tool"),
                "response": {},
            }
        )

    async def _read_loop(self) -> None:
        assert self._ws
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
                    continue
                data = json.loads(msg.data)
                await self._dispatch(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Qwen reader died: %s", e)

    async def _dispatch(self, data: dict[str, Any]) -> None:
        et = data.get("type", "")
        if et == "response.created":
            self.responding = True
        elif et == "response.done":
            self.responding = False
        elif et == "input_audio_buffer.speech_started" and self.responding:
            # Barge-in
            try:
                await self.cancel_response()
            except Exception as e:
                logger.warning("cancel on barge-in failed: %s", e)
        if self.on_event:
            result = self.on_event(data)
            if asyncio.iscoroutine(result):
                await result

    async def _append_audio_unlocked(self, pcm16le: bytes) -> None:
        if not pcm16le:
            return
        b64 = base64.b64encode(pcm16le).decode("ascii")
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "event_id": _eid("audio"),
                "audio": b64,
            }
        )
        self._audio_sent = True

    async def append_audio(self, pcm16le: bytes) -> None:
        if not pcm16le:
            return
        async with self._send_lock:
            await self._append_audio_unlocked(pcm16le)

    async def append_image(self, jpeg: bytes) -> None:
        """Append a camera JPEG. Always pads ~100ms silence first.

        DashScope rejects `input_image_buffer.append` with
        "Error append image before append audio" when the *current* input
        buffer has no audio yet — including after VAD commit / response
        boundaries, when a session-level `_audio_sent` flag would still be
        True. Concurrent mic + vision uploads without a lock can also
        interleave image ahead of the pad. Serialize and always pad.
        """
        if not jpeg:
            return
        # ~100ms of 16kHz mono PCM16 silence
        silence = b"\x00\x00" * 1600
        b64 = base64.b64encode(jpeg).decode("ascii")
        async with self._send_lock:
            await self._append_audio_unlocked(silence)
            await self._send(
                {
                    "type": "input_image_buffer.append",
                    "event_id": _eid("image"),
                    "image": b64,
                }
            )

    async def cancel_response(self) -> None:
        async with self._send_lock:
            await self._send({"type": "response.cancel", "event_id": _eid("cancel")})
            self.responding = False

    async def prompt_turn(self, text: str) -> None:
        """PromptTrigger: nudge model with text; ensure audio exists; force response."""
        silence = b"\x00\x00" * 1600
        async with self._send_lock:
            if not self._audio_sent:
                await self._append_audio_unlocked(silence)
            # Prefer conversation item + response.create (manual-style force)
            await self._send(
                {
                    "type": "conversation.item.create",
                    "event_id": _eid("item"),
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    },
                }
            )
            await self._send({"type": "response.create", "event_id": _eid("resp")})
