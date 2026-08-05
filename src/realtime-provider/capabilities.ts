/**
 * Provider capability matrix — single source for prompts, vision, and limits.
 */

import type { RealtimeCapabilities, RealtimeProviderId } from './types.js';

const GEMINI_CAPS: RealtimeCapabilities = {
	nativeAudio: true,
	vision: 'sendFile',
	toolCalling: true,
	googleSearch: true,
	builtinWebSearch: false,
	inputSampleRate: 16000,
	outputSampleRate: 24000,
	maxVisionFps: 2,
	maxImageBytes: 512 * 1024,
	requiresAudioBeforeVision: false,
	maxSessionMinutes: 0,
	maxVisionContextSeconds: 0,
};

const QWEN_CAPS: RealtimeCapabilities = {
	nativeAudio: true,
	vision: 'input_image_buffer',
	toolCalling: true,
	googleSearch: false,
	builtinWebSearch: true,
	inputSampleRate: 16000,
	outputSampleRate: 24000,
	maxVisionFps: 1,
	maxImageBytes: 256 * 1024,
	requiresAudioBeforeVision: true,
	maxSessionMinutes: 120,
	maxVisionContextSeconds: 240,
};

const OPENAI_CAPS: RealtimeCapabilities = {
	...GEMINI_CAPS,
	vision: 'none',
	googleSearch: false,
};

const MINIMAX_CAPS: RealtimeCapabilities = {
	...OPENAI_CAPS,
};

export function capabilitiesForProvider(provider: RealtimeProviderId): RealtimeCapabilities {
	switch (provider) {
		case 'gemini':
			return { ...GEMINI_CAPS };
		case 'qwen':
			return { ...QWEN_CAPS };
		case 'openai':
			return { ...OPENAI_CAPS };
		case 'minimax':
			return { ...MINIMAX_CAPS };
		default:
			return { ...GEMINI_CAPS };
	}
}

export function telemetryProviderId(provider: RealtimeProviderId): string {
	switch (provider) {
		case 'gemini':
			return 'gemini-live';
		case 'qwen':
			return 'dashscope-omni';
		case 'openai':
			return 'openai-realtime';
		case 'minimax':
			return 'minimax-realtime';
		default:
			return provider;
	}
}

export function isOpenAICompatProvider(provider: RealtimeProviderId): boolean {
	return provider === 'qwen' || provider === 'openai' || provider === 'minimax';
}
