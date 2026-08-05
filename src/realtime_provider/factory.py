"""Realtime model factory — shared by LiveKit and future Python surfaces."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from realtime_provider.migration_state import mark_phase, read_migration_state

if TYPE_CHECKING:
    from livekit.plugins import llm as lk_llm

logger = logging.getLogger("sutando-agent")

REALTIME_PROVIDER = os.environ.get("REALTIME_PROVIDER", "gemini").lower()


def use_factory_enabled() -> bool:
    v = os.environ.get("REALTIME_USE_FACTORY", "1").lower()
    return v in ("1", "true", "yes", "on", "")


def create_realtime_model() -> "lk_llm.RealtimeModel":
    if not use_factory_enabled() and REALTIME_PROVIDER not in ("gemini", "qwen", "openai", "minimax"):
        raise ValueError(
            f"REALTIME_USE_FACTORY=0 with REALTIME_PROVIDER={REALTIME_PROVIDER} unsupported; "
            "set REALTIME_PROVIDER=gemini or REALTIME_USE_FACTORY=1"
        )

    mark_phase(1, "in_progress", "livekit-agent loading factory", None)

    if REALTIME_PROVIDER == "gemini":
        from livekit.plugins.google.beta import realtime as google_realtime

        model = google_realtime.RealtimeModel(
            model=os.environ.get(
                "VOICE_NATIVE_AUDIO_MODEL",
                "gemini-2.5-flash-native-audio-preview-12-2025",
            ),
            voice=os.environ.get("REALTIME_VOICE", "Puck"),
            api_key=os.environ.get("GEMINI_VOICE_API_KEY") or os.environ.get("GEMINI_API_KEY", ""),
        )
        mark_phase(1, "complete", "LiveKit gemini model via factory")
        return model

    if REALTIME_PROVIDER in ("openai", "qwen", "minimax"):
        from livekit.plugins.openai import realtime as openai_realtime

        provider_defaults = {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "model": "gpt-4o-realtime-preview",
                "voice": "alloy",
            },
            "qwen": {
                "base_url": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "api_key_env": "DASHSCOPE_API_KEY",
                "model": "qwen3.5-omni-plus-realtime",
                "voice": "Ethan",
            },
            "minimax": {
                "base_url": "https://api.minimax.chat/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "model": "minimax-realtime",
                "voice": "default",
            },
        }
        cfg = provider_defaults[REALTIME_PROVIDER]

        if REALTIME_PROVIDER == "qwen":
            from qwen_realtime_compat import (
                patch_qwen_realtime,
                qwen_input_transcription_config,
                qwen_turn_detection_config,
            )

            patch_qwen_realtime()

        extra_kwargs: dict = {}
        if REALTIME_PROVIDER == "qwen":
            extra_kwargs["turn_detection"] = qwen_turn_detection_config()
            extra_kwargs["input_audio_noise_reduction"] = None
            extra_kwargs["input_audio_transcription"] = qwen_input_transcription_config()

        model = openai_realtime.RealtimeModel(
            model=os.environ.get("REALTIME_MODEL", cfg["model"]),
            voice=(
                os.environ.get("QWEN_REALTIME_VOICE", cfg["voice"])
                if REALTIME_PROVIDER == "qwen"
                else os.environ.get("REALTIME_VOICE", cfg["voice"])
            ),
            api_key=os.environ.get(cfg["api_key_env"], ""),
            base_url=os.environ.get("REALTIME_BASE_URL", cfg["base_url"]),
            **extra_kwargs,
        )

        try:
            opts = getattr(model, "_opts", None)
            raw_base = (opts.base_url if opts else "") or cfg["base_url"]
            parsed = urlparse(raw_base)
            api_host = parsed.netloc or raw_base[:80]
            td = extra_kwargs.get("turn_detection")
            if td is not None and REALTIME_PROVIDER == "qwen":
                vad_info = (
                    f"{td.get('type', '?') if isinstance(td, dict) else getattr(td, 'type', '?')}"
                    f"(thr={td.get('threshold', '?') if isinstance(td, dict) else getattr(td, 'threshold', '?')},"
                    f"prefix_ms={td.get('prefix_padding_ms', '?') if isinstance(td, dict) else getattr(td, 'prefix_padding_ms', '?')},"
                    f"silence_ms={td.get('silence_duration_ms', '?') if isinstance(td, dict) else getattr(td, 'silence_duration_ms', '?')})"
                )
            elif td is not None:
                vad_info = "server_vad(configured)"
            else:
                vad_info = "sdk-default"
            it = extra_kwargs.get("input_audio_transcription")
            tx_info = "disabled" if it is None else getattr(it, "model", None) or str(it)
            logger.info(
                "[REALTIME] provider=%s model=%s host=%s vad=%s input_user_transcription=%s",
                REALTIME_PROVIDER,
                getattr(model, "model", cfg["model"]),
                api_host,
                vad_info,
                tx_info,
            )
        except Exception as ex:
            logger.debug("[REALTIME] config summary log failed: %s", ex)

        if REALTIME_PROVIDER == "qwen":
            model._capabilities.auto_tool_reply_generation = True
            logger.info("Qwen: set auto_tool_reply_generation=True")

        mark_phase(1, "complete", f"LiveKit {REALTIME_PROVIDER} model via factory")
        return model

    raise ValueError(
        f"Unknown REALTIME_PROVIDER: {REALTIME_PROVIDER}. "
        f"Supported: gemini, openai, qwen, minimax"
    )


def migration_status() -> dict:
    return read_migration_state()
