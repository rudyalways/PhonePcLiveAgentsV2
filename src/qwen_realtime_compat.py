"""Qwen/DashScope realtime compatibility helpers.

This module is intentionally Qwen-only. Gemini uses LiveKit's Google realtime
plugin directly and should not inherit any DashScope protocol rewrites.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import math
import os
import struct
from typing import Any

from livekit import rtc

logger = logging.getLogger("sutando-agent")

_QWEN_REALTIME_PATCHED = False
QWEN_INPUT_SAMPLE_RATE = int(os.environ.get("QWEN_INPUT_SAMPLE_RATE", "16000"))


def qwen_turn_detection_config() -> dict[str, Any]:
    return {
        "type": os.environ.get("QWEN_TURN_DETECTION_TYPE", "semantic_vad").strip()
        or "semantic_vad",
        "threshold": float(os.environ.get("QWEN_SERVER_VAD_THRESHOLD", "0.1")),
        "prefix_padding_ms": int(os.environ.get("QWEN_SERVER_VAD_PREFIX_MS", "500")),
        "silence_duration_ms": int(os.environ.get("QWEN_SERVER_VAD_SILENCE_MS", "900")),
    }


def qwen_input_transcription_config() -> dict[str, str] | None:
    disabled = os.environ.get("QWEN_DISABLE_INPUT_TRANSCRIPTION", "").lower()
    if disabled in ("1", "true", "yes"):
        return None
    return {
        "model": os.environ.get(
            "QWEN_INPUT_AUDIO_TRANSCRIPTION_MODEL",
            "qwen3-asr-flash-realtime",
        )
    }


def qwen_default_realtime_model() -> str:
    return os.environ.get("REALTIME_MODEL", "qwen3.5-omni-plus-realtime")


def qwen_default_output_voice() -> str:
    return os.environ.get("QWEN_REALTIME_VOICE", "Ethan")


def qwen_finalize_dashscope_session(session: dict[str, Any]) -> None:
    """Ensure merged session has everything Qwen needs to detect turns and speak."""
    session.setdefault("type", "realtime")
    session.setdefault("model", qwen_default_realtime_model())
    session.setdefault("modalities", ["text", "audio"])
    session.setdefault("input_audio_format", "pcm")
    session.setdefault("output_audio_format", "pcm")
    # Explicit rate matches streamed PCM (see QWEN_INPUT_SAMPLE_RATE and room input rate for Qwen).
    session.setdefault("sample_rate", QWEN_INPUT_SAMPLE_RATE)
    session.setdefault("voice", qwen_default_output_voice())
    td = qwen_turn_detection_config()
    if "turn_detection" not in session:
        session["turn_detection"] = dict(td)
    else:
        existing = session["turn_detection"]
        if isinstance(existing, dict):
            for key, value in td.items():
                existing.setdefault(key, value)
    tx = qwen_input_transcription_config()
    if tx is not None:
        session.setdefault("input_audio_transcription", dict(tx))

    # Fields OpenAI sends but DashScope may not accept reliably
    session.pop("tracing", None)
    mot = session.get("max_output_tokens")
    if mot == "inf" or mot == float("inf"):
        session["max_output_tokens"] = int(os.environ.get("QWEN_MAX_OUTPUT_TOKENS", "16384"))


def _qwen_pcm16_le_rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n <= 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return math.sqrt(sum(s * s for s in samples) / n)


def _qwen_pcm16_le_peak_abs(pcm: bytes) -> int:
    n = len(pcm) // 2
    if n <= 0:
        return 0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return max(abs(s) for s in samples)


def _qwen_downmix_to_mono_frame(frame: rtc.AudioFrame) -> rtc.AudioFrame:
    """Stereo (or multi-channel) int16 interleaved -> mono int16, same sample rate."""
    n = int(frame.samples_per_channel)
    nc = int(frame.num_channels)
    if n <= 0 or nc <= 1:
        return frame
    raw = frame.data.tobytes()[: n * nc * 2]
    flat = struct.unpack(f"<{n * nc}h", raw)
    mono: list[int] = []
    for i in range(n):
        acc = sum(flat[i * nc + c] for c in range(nc))
        mono.append(int(round(acc / nc)))
    packed = struct.pack(f"<{n}h", *mono)
    return rtc.AudioFrame(packed, frame.sample_rate, 1, n)


def qwen_adapt_session_update_event(event: dict[str, Any]) -> None:
    """Adapt LiveKit/OpenAI GA session.update shape to DashScope's documented shape."""
    session = event.get("session")
    if not isinstance(session, dict):
        return

    changed = False
    output_modalities = event.get("output_modalities") or session.get("output_modalities")
    if output_modalities and "modalities" not in session:
        mods = list(output_modalities)
        # DashScope documents either ["text"] or ["text", "audio"]; LiveKit sends ["audio"].
        session["modalities"] = ["text", "audio"] if "audio" in mods else mods
        changed = True

    audio = session.get("audio")
    if isinstance(audio, dict):
        input_audio = audio.get("input")
        if isinstance(input_audio, dict):
            if "input_audio_format" not in session and input_audio.get("format") is not None:
                session["input_audio_format"] = "pcm"
                changed = True

            transcription = input_audio.get("transcription")
            if "input_audio_transcription" not in session and transcription is not None:
                session["input_audio_transcription"] = transcription
                changed = True

            turn_detection = input_audio.get("turn_detection")
            if "turn_detection" not in session and turn_detection is not None:
                td = dict(turn_detection)
                td["type"] = qwen_turn_detection_config()["type"]
                # DashScope docs do not list these OpenAI-specific fields.
                td.pop("create_response", None)
                td.pop("interrupt_response", None)
                session["turn_detection"] = td
                changed = True

        output_audio = audio.get("output")
        if isinstance(output_audio, dict):
            if "output_audio_format" not in session and output_audio.get("format") is not None:
                session["output_audio_format"] = "pcm"
                changed = True
            if "voice" not in session and output_audio.get("voice"):
                session["voice"] = output_audio["voice"]
                changed = True

        # DashScope's Realtime docs use the old/top-level schema. Keeping the
        # nested OpenAI GA audio object advertises 24 kHz input while we need to
        # stream Qwen's documented 16 kHz PCM input.
        if "audio" in session:
            session.pop("audio", None)
            changed = True
        if "output_modalities" in session:
            session.pop("output_modalities", None)
            changed = True

    if changed:
        logger.info(
            "[QWEN-PATCH] adapted session.update for DashScope: modalities=%s "
            "vad=%s input_audio_transcription=%s",
            session.get("modalities"),
            session.get("turn_detection"),
            session.get("input_audio_transcription"),
        )


def patch_qwen_realtime() -> None:
    """Monkey-patch livekit-plugins-openai for Qwen Realtime API compatibility.

    Qwen deviates from OpenAI's Realtime protocol in three ways:
    1. Shorter event names (response.audio.delta vs response.output_audio.delta)
    2. Missing fields (output_index, content_index, item_id)
    3. Out-of-order events (response done/delta before response.created)
    """
    global _QWEN_REALTIME_PATCHED
    if _QWEN_REALTIME_PATCHED:
        logger.info("Qwen compat patch already active (skipped re-apply)")
        return

    import uuid

    try:
        from livekit.agents import utils
        from livekit.plugins.openai.realtime import realtime_model as rm
    except ImportError:
        return

    qwen_event_map = {
        "response.audio.delta": "response.output_audio.delta",
        "response.audio.done": "response.output_audio.done",
        "response.audio_transcript.delta": "response.output_audio_transcript.delta",
        "response.audio_transcript.done": "response.output_audio_transcript.done",
        "response.text.delta": "response.output_text.delta",
        "response.text.done": "response.output_text.done",
    }

    original_init = rm.RealtimeSession.__init__
    original_resample_audio = rm.RealtimeSession._resample_audio
    event_counts: dict[str, int] = {}

    def qwen_short_event_detail(event: dict[str, Any], limit: int = 480) -> str:
        try:
            skip_keys = {"delta", "audio", "output_audio", "args"}
            data = {k: v for k, v in event.items() if k not in skip_keys}
            text = json.dumps(data, ensure_ascii=False, default=str)
            return text[:limit] + ("..." if len(text) > limit else "")
        except Exception:
            return str(event)[:limit]

    def qwen_always_log_server_event(evt_type: str) -> bool:
        if evt_type == "error":
            return True
        if evt_type in ("response.created", "response.done"):
            return True
        if "input_audio_buffer" in evt_type:
            return True
        if evt_type == "conversation.item.input_audio_transcription.completed":
            return True
        return "transcription" in evt_type and evt_type.endswith(".completed")

    def patched_init(self, *args, **kwargs):
        self._qwen_item_ids = {}
        self._qwen_type_rename_count = 0
        self._qwen_input_sample_rate = QWEN_INPUT_SAMPLE_RATE
        # DashScope appears to replace session on each update; merge into one snapshot.
        self._qwen_dashscope_session: dict[str, Any] = {}
        original_init(self, *args, **kwargs)
        self._bstream = utils.audio.AudioByteStream(
            QWEN_INPUT_SAMPLE_RATE,
            1,
            samples_per_channel=QWEN_INPUT_SAMPLE_RATE // 10,
        )
        logger.info("[QWEN-PATCH] input audio stream set to %d Hz PCM", QWEN_INPUT_SAMPLE_RATE)

        def ensure_generation(session):
            if session._current_generation is not None:
                return
            synth_id = f"qwen_synth_{uuid.uuid4().hex[:12]}"
            session._current_generation = rm._ResponseGeneration(
                message_ch=utils.aio.Chan(),
                function_ch=utils.aio.Chan(),
                messages={},
                _created_timestamp=__import__("time").time(),
                _done_fut=asyncio.Future(),
            )
            generation_ev = __import__("livekit.agents", fromlist=["llm"]).llm.GenerationCreatedEvent(
                message_stream=session._current_generation.message_ch,
                function_stream=session._current_generation.function_ch,
                user_initiated=False,
                response_id=synth_id,
            )

            if session._response_created_futures:
                oldest_key = next(iter(session._response_created_futures))
                fut = session._response_created_futures.pop(oldest_key)
                if not fut.done():
                    generation_ev.user_initiated = True
                    fut.set_result(generation_ev)
                    logger.info("[QWEN-PATCH] resolved pending generate_reply future %s", oldest_key)

            session.emit("generation_created", generation_ev)
            logger.info("[QWEN-PATCH] auto-created _current_generation id=%s", synth_id)

        def ensure_message(session, item_id):
            gen = session._current_generation
            if gen is None or item_id in gen.messages:
                return
            gen.messages[item_id] = rm._MessageGeneration(
                message_id=item_id,
                text_ch=utils.aio.Chan(),
                audio_ch=utils.aio.Chan(),
                modalities=asyncio.Future(),
            )
            llm_mod = __import__("livekit.agents", fromlist=["llm"]).llm
            gen.message_ch.send_nowait(
                llm_mod.MessageGeneration(
                    message_id=item_id,
                    text_stream=gen.messages[item_id].text_ch,
                    audio_stream=gen.messages[item_id].audio_ch,
                    modalities=gen.messages[item_id].modalities,
                )
            )

        def on_event(event):
            raw_type = event.get("type", "unknown")
            evt_type = raw_type

            mapped = qwen_event_map.get(evt_type)
            if mapped:
                event["type"] = mapped
                evt_type = mapped
                count = getattr(self, "_qwen_type_rename_count", 0)
                if count < 20:
                    logger.info("[QWEN-WS] type remap: %s -> %s", raw_type, mapped)
                    self._qwen_type_rename_count = count + 1

            qwen_fix_fields(self, event, evt_type)

            needs_gen = (
                evt_type.startswith("response.")
                and evt_type not in ("response.created", "response.done")
            )
            if needs_gen:
                ensure_generation(self)
                item_id = event.get("item_id")
                if item_id and evt_type in (
                    "response.output_audio.delta",
                    "response.output_audio.done",
                    "response.output_audio_transcript.delta",
                    "response.output_audio_transcript.done",
                    "response.output_text.delta",
                    "response.output_text.done",
                    "response.content_part.added",
                    "response.content_part.done",
                ):
                    ensure_message(self, item_id)

            if evt_type == "error":
                error_msg = str(event.get("error", {}).get("message", ""))
                if "active response" in error_msg.lower():
                    self._close_current_generation(
                        reason="qwen: server reported active response conflict"
                    )
                    logger.info("[QWEN-PATCH] cleared generation after active-response error")

            event_counts[evt_type] = event_counts.get(evt_type, 0) + 1
            count = event_counts[evt_type]
            if qwen_always_log_server_event(evt_type):
                logger.info(
                    "[QWEN-WS] <- %s (#%d) %s",
                    evt_type,
                    count,
                    qwen_short_event_detail(event),
                )
            elif count <= 3 or count % 50 == 0:
                logger.info("[QWEN-WS] <- %s (#%d)", evt_type, count)

        def on_client_event(event):
            evt_type = event.get("type", "unknown")

            if evt_type == "session.update":
                qwen_adapt_session_update_event(event)
                sess = event.get("session")
                if isinstance(sess, dict) and sess.get("type") == "realtime":
                    snap = self._qwen_dashscope_session
                    for key, value in sess.items():
                        if value is not None:
                            snap[key] = value
                    qwen_finalize_dashscope_session(snap)
                    event["session"] = copy.deepcopy(snap)
                    logger.info(
                        "[QWEN-PATCH] merged session.update delta_keys=%s snapshot_keys=%s",
                        list(sess.keys()),
                        sorted(snap.keys()),
                    )

            if evt_type == "conversation.item.create":
                item = event.get("item", {})
                item_id = item.get("id")
                if item_id and hasattr(self, "_item_create_future"):
                    fut = self._item_create_future.pop(item_id, None)
                    if fut and not fut.done():
                        fut.set_result(None)
                        logger.info("[QWEN-PATCH] auto-resolved item_create future for %s", item_id)

                if item.get("type") == "function_call_output":
                    def nudge_response():
                        if self._current_generation is None:
                            logger.info("[QWEN-PATCH] nudging Qwen with response.create after tool output")
                            self.send_event({"type": "response.create", "response": {}})

                    asyncio.get_event_loop().call_later(0.5, nudge_response)

            key = f"out:{evt_type}"
            event_counts[key] = event_counts.get(key, 0) + 1
            count = event_counts[key]
            always_out = evt_type in (
                "input_audio_buffer.commit",
                "response.create",
                "response.cancel",
                "conversation.item.create",
            )
            if always_out:
                logger.info(
                    "[QWEN-WS] -> %s (#%d) %s",
                    evt_type,
                    count,
                    qwen_short_event_detail(event),
                )
            elif count <= 3 or count % 50 == 0:
                if evt_type == "input_audio_buffer.append":
                    audio_b64 = event.get("audio")
                    if isinstance(audio_b64, str) and audio_b64 and count in (1, 2, 3, 50, 100, 200):
                        try:
                            pcm = base64.b64decode(audio_b64, validate=False)
                            rms = _qwen_pcm16_le_rms(pcm)
                            logger.info(
                                "[QWEN-WS] -> %s (#%d) pcm_bytes=%d rms=%.1f (int16 le)",
                                evt_type,
                                count,
                                len(pcm),
                                rms,
                            )
                        except Exception:
                            logger.info("[QWEN-WS] -> %s (#%d) [audio chunk]", evt_type, count)
                    else:
                        logger.info("[QWEN-WS] -> %s (#%d) [audio chunk]", evt_type, count)
                else:
                    logger.info(
                        "[QWEN-WS] -> %s (#%d) %s",
                        evt_type,
                        count,
                        {k: v for k, v in event.items() if k != "type"},
                    )

        self.on("openai_server_event_received", on_event)
        self.on("openai_client_event_queued", on_client_event)

    def patched_resample_audio(self, frame):
        input_sample_rate = getattr(self, "_qwen_input_sample_rate", None)
        if input_sample_rate is None:
            yield from original_resample_audio(self, frame)
            return

        ch = frame.num_channels or 1
        if self._input_resampler:
            same_rate = frame.sample_rate == self._input_resampler._input_rate
            same_ch = ch == getattr(self._input_resampler, "_num_channels", ch)
            if not same_rate or not same_ch:
                self._input_resampler = None

        if self._input_resampler is None and (
            frame.sample_rate != input_sample_rate or ch != 1
        ):
            self._input_resampler = rm.rtc.AudioResampler(
                input_rate=frame.sample_rate,
                output_rate=input_sample_rate,
                num_channels=ch,
            )

        if self._input_resampler:
            for out_frame in self._input_resampler.push(frame):
                if out_frame.num_channels == 1:
                    yield out_frame
                else:
                    yield _qwen_downmix_to_mono_frame(out_frame)
        else:
            yield frame

    def qwen_fix_fields(session, event: dict[str, Any], evt_type: str) -> None:
        import uuid as uuid_mod

        if evt_type == "response.created":
            resp = event.get("response")
            if isinstance(resp, dict):
                if not resp.get("id"):
                    resp["id"] = f"qwen_resp_{uuid_mod.uuid4().hex[:12]}"
                if not resp.get("status"):
                    resp["status"] = "in_progress"
            else:
                event["response"] = {
                    "id": f"qwen_resp_{uuid_mod.uuid4().hex[:12]}",
                    "status": "in_progress",
                }

        output_index = event.get("output_index")
        if output_index is None:
            event["output_index"] = 0
            output_index = 0
        if event.get("content_index") is None:
            event["content_index"] = 0

        resp_id = event.get("response_id", "default")
        key = (resp_id, output_index)

        item = event.get("item")
        if isinstance(item, dict) and not item.get("id"):
            if key not in session._qwen_item_ids:
                session._qwen_item_ids[key] = f"qwen_{uuid_mod.uuid4().hex[:12]}"
            item["id"] = session._qwen_item_ids[key]

        if not event.get("item_id"):
            if key not in session._qwen_item_ids:
                session._qwen_item_ids[key] = f"qwen_{uuid_mod.uuid4().hex[:12]}"
            event["item_id"] = session._qwen_item_ids[key]

    rm.RealtimeSession.__init__ = patched_init
    rm.RealtimeSession._resample_audio = patched_resample_audio

    original_push_audio = rm.RealtimeSession.push_audio
    _mic_log_first = int(os.environ.get("QWEN_MIC_LOG_FIRST_N", "20"))
    _mic_log_every = int(os.environ.get("QWEN_MIC_LOG_EVERY_N", "25"))

    def patched_push_audio(self, frame: rtc.AudioFrame) -> None:
        cnt = getattr(self, "_qwen_mic_frame_count", 0) + 1
        self._qwen_mic_frame_count = cnt
        if cnt <= _mic_log_first or (_mic_log_every > 0 and cnt % _mic_log_every == 0):
            raw = frame.data.tobytes()
            rms = _qwen_pcm16_le_rms(raw)
            peak = _qwen_pcm16_le_peak_abs(raw)
            logger.info(
                "[QWEN-MIC] frame #%d from_room rate=%dHz ch=%d samples/ch=%d bytes=%d "
                "rms=%.1f peak=%d (int16 LE; quiet/noise ~rms<30; speech often rms 200–8000+ peak 1000–25000+)",
                cnt,
                frame.sample_rate,
                frame.num_channels,
                frame.samples_per_channel,
                len(raw),
                rms,
                peak,
            )
        original_push_audio(self, frame)

    rm.RealtimeSession.push_audio = patched_push_audio

    original_generate_reply = rm.RealtimeSession.generate_reply

    def patched_generate_reply(self, **kwargs):
        gen = self._current_generation
        has_gen = gen is not None
        done_fut_done = gen._done_fut.done() if gen else "N/A"
        pending_futs = len(self._response_created_futures)
        logger.info(
            "[QWEN-PATCH] generate_reply called: has_gen=%s, done_fut_done=%s, pending_response_futs=%d",
            has_gen,
            done_fut_done,
            pending_futs,
        )
        if gen is not None and not gen._done_fut.done():
            from livekit.plugins.openai._oai_api import ResponseCancelEvent

            self.send_event(ResponseCancelEvent(type="response.cancel"))
            logger.info("[QWEN-PATCH] sent response.cancel, chaining generate_reply after done_fut")

            result_fut = asyncio.Future()
            timeout_handle = asyncio.get_event_loop().call_later(
                3.0,
                lambda: force_proceed(self, result_fut, kwargs),
            )

            def on_old_done(_fut):
                timeout_handle.cancel()
                try:
                    new_fut = original_generate_reply(self, **kwargs)

                    def chain(f):
                        if result_fut.done():
                            return
                        try:
                            result_fut.set_result(f.result())
                        except Exception as exc:
                            result_fut.set_exception(exc)

                    new_fut.add_done_callback(chain)
                except Exception as exc:
                    if not result_fut.done():
                        result_fut.set_exception(exc)

            gen._done_fut.add_done_callback(on_old_done)
            return result_fut

        return original_generate_reply(self, **kwargs)

    def force_proceed(session, result_fut, kwargs):
        if result_fut.done():
            return
        logger.warning("[QWEN-PATCH] 3s timeout waiting for response.done, force-closing")
        session._close_current_generation(reason="qwen: timeout waiting for server cancel ack")
        try:
            new_fut = original_generate_reply(session, **kwargs)

            def chain(f):
                if result_fut.done():
                    return
                try:
                    result_fut.set_result(f.result())
                except Exception as exc:
                    result_fut.set_exception(exc)

            new_fut.add_done_callback(chain)
        except Exception as exc:
            if not result_fut.done():
                result_fut.set_exception(exc)

    rm.RealtimeSession.generate_reply = patched_generate_reply

    _QWEN_REALTIME_PATCHED = True
    logger.info("Qwen compatibility patch applied to livekit-plugins-openai")
