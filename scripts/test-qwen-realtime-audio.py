#!/usr/bin/env python3
"""Real DashScope Qwen Realtime audio/VAD smoke test.

This tests the path that /mobile needs:

    PCM audio -> input_audio_buffer.append -> Qwen VAD/transcription
    -> automatic response events

It intentionally uses DashScope's documented top-level session.update schema,
not LiveKit's nested OpenAI schema. On macOS, the default input audio is a short
synthetic spoken WAV generated with `say` + `afconvert`; pass --wav to use your
own 16 kHz mono 16-bit PCM WAV file.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3.5-omni-plus-realtime"
DEFAULT_TRANSCRIPTION_MODEL = "qwen3-asr-flash-realtime"


def build_ws_url(base_url: str, model: str) -> str:
    parsed = urlparse(base_url)
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme or "wss")
    query = dict(parse_qsl(parsed.query))
    query["model"] = model
    return urlunparse(parsed._replace(scheme=scheme, query=urlencode(query)))


def require_macos_say_audio(text: str, out_dir: Path) -> Path:
    say = shutil.which("say")
    afconvert = shutil.which("afconvert")
    if not say or not afconvert:
        raise RuntimeError(
            "Default audio generation needs macOS `say` and `afconvert`; pass --wav instead."
        )

    aiff_path = out_dir / "qwen-test-input.aiff"
    wav_path = out_dir / "qwen-test-input.wav"
    subprocess.run([say, "-o", str(aiff_path), text], check=True)
    subprocess.run(
        [afconvert, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff_path), str(wav_path)],
        check=True,
    )
    return wav_path


def load_pcm16_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        if channels != 1 or sample_width != 2 or sample_rate != 16000:
            raise ValueError(
                f"{path} must be mono 16-bit 16 kHz PCM WAV; got "
                f"channels={channels}, sample_width={sample_width}, sample_rate={sample_rate}"
            )
        return wav.readframes(wav.getnframes()), sample_rate


def chunk_pcm(pcm: bytes, sample_rate: int, chunk_ms: int) -> list[bytes]:
    bytes_per_ms = sample_rate * 2 // 1000
    chunk_size = bytes_per_ms * chunk_ms
    return [pcm[i : i + chunk_size] for i in range(0, len(pcm), chunk_size) if pcm[i : i + chunk_size]]


def event_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


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

    with tempfile.TemporaryDirectory(prefix="qwen-realtime-audio-") as tmp:
        tmp_dir = Path(tmp)
        wav_path = Path(args.wav) if args.wav else require_macos_say_audio(args.text, tmp_dir)
        pcm, sample_rate = load_pcm16_wav(wav_path)

        chunks = chunk_pcm(pcm, sample_rate, args.chunk_ms)
        silence = b"\x00\x00" * int(sample_rate * args.trailing_silence_s)
        chunks.extend(chunk_pcm(silence, sample_rate, args.chunk_ms))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Sutando-qwen-realtime-audio-smoke/1.0",
        }
        timeout = aiohttp.ClientTimeout(total=args.timeout_s + 20)

        flags = {
            "speech_started": False,
            "speech_stopped": False,
            "input_transcript": "",
            "response_created": False,
            "response_done": False,
            "assistant_text": "",
            "assistant_audio_delta": False,
            "closed": False,
        }
        errors: list[dict] = []
        stop_reader = asyncio.Event()

        async with aiohttp.ClientSession(timeout=timeout) as session:
            ws = await session.ws_connect(url, headers=headers)

            async def recv_json(wait_s: float) -> dict | None:
                msg = await asyncio.wait_for(ws.receive(), timeout=wait_s)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    return json.loads(msg.data)
                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    print(f"  WS closed: {msg.extra}", file=sys.stderr)
                    flags["closed"] = True
                    return None
                if msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"  WS error: {ws.exception()}", file=sys.stderr)
                    return None
                return None

            created = False
            for _ in range(20):
                raw = await recv_json(5)
                if not raw:
                    break
                print(f"  <- {raw.get('type')}")
                if raw.get("type") == "session.created":
                    created = True
                    break
                if raw.get("type") == "error":
                    print(json.dumps(raw, ensure_ascii=False, indent=2), file=sys.stderr)
                    return 2
            if not created:
                print("FAIL: never received session.created", file=sys.stderr)
                return 2

            async def reader() -> None:
                while not stop_reader.is_set():
                    try:
                        raw = await recv_json(1)
                    except TimeoutError:
                        continue
                    if not raw:
                        continue

                    et = raw.get("type", "")
                    if et in (
                        "session.updated",
                        "input_audio_buffer.speech_started",
                        "input_audio_buffer.speech_stopped",
                        "input_audio_buffer.committed",
                        "conversation.item.input_audio_transcription.completed",
                        "conversation.item.input_audio_transcription.failed",
                        "response.created",
                        "response.done",
                        "error",
                    ):
                        detail = ""
                        if et == "conversation.item.input_audio_transcription.completed":
                            detail = f" transcript={raw.get('transcript', '')!r}"
                        if et == "error":
                            detail = " " + json.dumps(raw.get("error", raw), ensure_ascii=False)
                        print(f"  <- {et}{detail}")

                    if et == "input_audio_buffer.speech_started":
                        flags["speech_started"] = True
                    elif et == "input_audio_buffer.speech_stopped":
                        flags["speech_stopped"] = True
                    elif et == "conversation.item.input_audio_transcription.completed":
                        flags["input_transcript"] = str(raw.get("transcript", "")).strip()
                    elif et == "response.created":
                        flags["response_created"] = True
                    elif et == "response.done":
                        flags["response_done"] = True
                    elif et in ("response.audio.delta", "response.output_audio.delta"):
                        flags["assistant_audio_delta"] = True
                    elif et in (
                        "response.audio_transcript.delta",
                        "response.output_audio_transcript.delta",
                        "response.text.delta",
                        "response.output_text.delta",
                    ):
                        flags["assistant_text"] += str(raw.get("delta", ""))
                    elif et == "error":
                        errors.append(raw)

            reader_task = asyncio.create_task(reader())

            session_update = {
                "type": "session.update",
                "event_id": event_id("session_update"),
                "session": {
                    "modalities": args.modalities.split(","),
                    "voice": args.voice,
                    "instructions": args.instructions,
                    "input_audio_format": "pcm",
                    "output_audio_format": "pcm",
                    "input_audio_transcription": {"model": args.transcription_model},
                    "turn_detection": {
                        "type": args.vad,
                        "threshold": args.threshold,
                        "prefix_padding_ms": args.prefix_padding_ms,
                        "silence_duration_ms": args.silence_duration_ms,
                    },
                },
            }
            await ws.send_str(json.dumps(session_update, ensure_ascii=False))
            print(
                "  -> session.update "
                f"(schema=official, vad={args.vad}, tx={args.transcription_model}, "
                f"modalities={args.modalities})"
            )

            await asyncio.sleep(args.post_update_wait_s)
            print(f"  -> streaming {len(chunks)} PCM chunks from {wav_path}")
            for i, chunk in enumerate(chunks, start=1):
                if errors or flags["closed"] or ws.closed:
                    break
                await ws.send_str(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "event_id": event_id("audio_append"),
                            "audio": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                )
                if i <= 3 or i % 20 == 0:
                    print(f"  -> input_audio_buffer.append #{i}")
                if args.realtime:
                    await asyncio.sleep(args.chunk_ms / 1000)

            deadline = time.monotonic() + args.timeout_s
            while time.monotonic() < deadline:
                if errors:
                    break
                if flags["response_done"] and (
                    flags["assistant_audio_delta"] or flags["assistant_text"]
                ):
                    break
                await asyncio.sleep(0.25)

            stop_reader.set()
            await reader_task
            await ws.close()

        if flags["assistant_text"].strip():
            print("\n--- Assistant transcript/text deltas ---")
            print(flags["assistant_text"].strip())

        print("\n--- Result flags ---")
        for key, value in flags.items():
            print(f"{key}: {value!r}")

        if errors:
            print("\nFAIL: server returned error event(s).", file=sys.stderr)
            return 2
        required = [
            "speech_started",
            "speech_stopped",
            "input_transcript",
            "response_created",
            "response_done",
        ]
        missing = [key for key in required if not flags[key]]
        if missing:
            print(f"\nFAIL: missing required event(s): {', '.join(missing)}", file=sys.stderr)
            return 2
        if not (flags["assistant_audio_delta"] or flags["assistant_text"]):
            print("\nFAIL: response completed without text/audio deltas.", file=sys.stderr)
            return 2

        print("\nOK: Qwen audio VAD/transcription/auto-response path works.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Qwen Realtime audio VAD path.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--wav", help="16 kHz mono 16-bit PCM WAV to stream")
    parser.add_argument(
        "--text",
        default="Hello Qwen, please say one short sentence back.",
        help="Speech text to synthesize with macOS say when --wav is omitted",
    )
    parser.add_argument("--voice", default=os.environ.get("QWEN_REALTIME_VOICE", "Ethan"))
    parser.add_argument("--modalities", default="text,audio")
    parser.add_argument(
        "--instructions",
        default="Reply briefly in one short sentence.",
    )
    parser.add_argument("--vad", default=os.environ.get("QWEN_TURN_DETECTION_TYPE", "semantic_vad"))
    parser.add_argument("--threshold", type=float, default=float(os.environ.get("QWEN_SERVER_VAD_THRESHOLD", "0.1")))
    parser.add_argument("--prefix-padding-ms", type=int, default=int(os.environ.get("QWEN_SERVER_VAD_PREFIX_MS", "500")))
    parser.add_argument("--silence-duration-ms", type=int, default=int(os.environ.get("QWEN_SERVER_VAD_SILENCE_MS", "900")))
    parser.add_argument(
        "--transcription-model",
        default=os.environ.get("QWEN_INPUT_AUDIO_TRANSCRIPTION_MODEL", DEFAULT_TRANSCRIPTION_MODEL),
    )
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument("--trailing-silence-s", type=float, default=1.5)
    parser.add_argument("--timeout-s", type=float, default=25)
    parser.add_argument("--post-update-wait-s", type=float, default=0.5)
    parser.add_argument("--realtime", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    try:
        return asyncio.run(run(args))
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    started = time.perf_counter()
    exit_code = main()
    print(f"\n(elapsed {time.perf_counter() - started:.1f}s)", file=sys.stderr)
    raise SystemExit(exit_code)
