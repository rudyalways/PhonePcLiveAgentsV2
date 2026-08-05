/**
 * Resolve realtime provider config from env (+ workspace voice JSON for Gemini).
 */

import type { VoiceConfig } from '../voice-config.js';
import { resolveCredential } from '../credential-resolver.js';
import { capabilitiesForProvider, isOpenAICompatProvider } from './capabilities.js';
import type { RealtimeConfig, RealtimePhaseFlags, RealtimeProviderId } from './types.js';

const PROVIDER_DEFAULTS: Record<
	RealtimeProviderId,
	{ model: string; voice: string; baseUrl?: string; apiKeyEnv: string }
> = {
	gemini: {
		model: 'gemini-2.5-flash-native-audio-preview-12-2025',
		voice: 'Puck',
		apiKeyEnv: 'GEMINI_VOICE_API_KEY',
	},
	qwen: {
		model: 'qwen3.5-omni-plus-realtime',
		voice: 'Ethan',
		baseUrl: 'https://dashscope.aliyuncs.com/api-ws/v1',
		apiKeyEnv: 'DASHSCOPE_API_KEY',
	},
	openai: {
		model: 'gpt-4o-realtime-preview',
		voice: 'alloy',
		baseUrl: 'https://api.openai.com/v1',
		apiKeyEnv: 'OPENAI_API_KEY',
	},
	minimax: {
		model: 'minimax-realtime',
		voice: 'default',
		baseUrl: 'https://api.minimax.chat/v1',
		apiKeyEnv: 'MINIMAX_API_KEY',
	},
};

function parseProvider(raw: string | undefined): RealtimeProviderId {
	const p = (raw || 'gemini').toLowerCase();
	if (p === 'gemini' || p === 'qwen' || p === 'openai' || p === 'minimax') return p;
	return 'gemini';
}

function envFlag(name: string, defaultValue = false): boolean {
	const v = (process.env[name] || '').toLowerCase();
	if (!v) return defaultValue;
	return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

export function resolvePhaseFlags(): RealtimePhaseFlags {
	return {
		visionAdapter: envFlag('REALTIME_VISION_ADAPTER'),
	};
}

/** Factory enabled by default; set REALTIME_USE_FACTORY=0 to revert to legacy inline transport. */
export function useFactoryEnabled(): boolean {
	const v = (process.env.REALTIME_USE_FACTORY || '').toLowerCase();
	if (!v) return true;
	return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

function resolveGeminiKey(): { key: string; source: string } {
	const voiceCredential = resolveCredential('gemini-voice');
	return { key: voiceCredential.key, source: voiceCredential.source };
}

function resolveApiKey(provider: RealtimeProviderId): { key: string; source: string } {
	if (provider === 'gemini') {
		return resolveGeminiKey();
	}
	const envName = PROVIDER_DEFAULTS[provider].apiKeyEnv;
	return { key: process.env[envName] || '', source: envName };
}

function qwenTurnDetection() {
	return {
		type: process.env.QWEN_TURN_DETECTION_TYPE || 'semantic_vad',
		threshold: Number(process.env.QWEN_SERVER_VAD_THRESHOLD || '0.1'),
		prefix_padding_ms: Number(process.env.QWEN_SERVER_VAD_PREFIX_MS || '500'),
		silence_duration_ms: Number(process.env.QWEN_SERVER_VAD_SILENCE_MS || '900'),
	};
}

function qwenTranscriptionModel(): string | null {
	const disableTx = (process.env.QWEN_DISABLE_INPUT_TRANSCRIPTION || '').toLowerCase();
	if (disableTx === '1' || disableTx === 'true' || disableTx === 'yes') return null;
	return process.env.QWEN_INPUT_AUDIO_TRANSCRIPTION_MODEL || 'qwen3-asr-flash-realtime';
}

export interface ResolveRealtimeConfigOptions {
	voiceConfig?: VoiceConfig;
	geminiNativeAudioModel?: string;
	geminiSpeechVoice?: string;
}

export function resolveRealtimeConfig(opts: ResolveRealtimeConfigOptions = {}): RealtimeConfig {
	const provider = parseProvider(process.env.REALTIME_PROVIDER);
	const defaults = PROVIDER_DEFAULTS[provider];
	const caps = capabilitiesForProvider(provider);
	const { key, source } = resolveApiKey(provider);

	const voiceConfig = opts.voiceConfig;
	const googleSearch = provider === 'gemini' ? (voiceConfig?.googleSearch ?? false) : false;

	let model = process.env.REALTIME_MODEL || defaults.model;
	let voice = defaults.voice;

	if (provider === 'gemini') {
		model = opts.geminiNativeAudioModel || voiceConfig?.model || model;
		voice = opts.geminiSpeechVoice || process.env.VOICE_NAME || defaults.voice;
	} else if (provider === 'qwen') {
		voice = process.env.QWEN_REALTIME_VOICE || defaults.voice;
	} else {
		voice = process.env.REALTIME_VOICE || defaults.voice;
	}

	const baseUrl =
		process.env.REALTIME_BASE_URL ||
		(provider === 'qwen'
			? process.env.REALTIME_BASE_URL || defaults.baseUrl
			: defaults.baseUrl);

	return {
		provider,
		model,
		voice,
		baseUrl,
		apiKey: key,
		apiKeySource: source,
		capabilities: {
			...caps,
			googleSearch: provider === 'gemini' ? googleSearch : false,
		},
		turnDetection: provider === 'qwen' ? qwenTurnDetection() : undefined,
		transcriptionModel: provider === 'qwen' ? qwenTranscriptionModel() : null,
		googleSearch,
		useFactory: useFactoryEnabled(),
		phaseFlags: resolvePhaseFlags(),
	};
}

export function validateRealtimeConfig(config: RealtimeConfig): string | null {
	if (config.provider === 'gemini') {
		const k = config.apiKey.trim();
		if (!k || k.length < 20 || k.length > 200 || /\s/.test(k) || k === 'your-gemini-key') {
			return `Invalid Gemini API key (${config.apiKeySource}). Rotate at https://ai.google.dev`;
		}
		return null;
	}
	if (isOpenAICompatProvider(config.provider)) {
		if (!config.apiKey || config.apiKey.trim().length < 10) {
			return `${PROVIDER_DEFAULTS[config.provider].apiKeyEnv} is required when REALTIME_PROVIDER=${config.provider}`;
		}
	}
	return null;
}
