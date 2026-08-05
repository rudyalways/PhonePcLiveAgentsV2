/**
 * Phase 4 — phone transport injection tests.
 */
import { strict as assert } from 'node:assert';
import { afterEach, beforeEach, test } from 'node:test';
import { bootstrapPhoneRealtimeSession } from '../src/realtime-provider/index.js';
import type { VoiceConfig } from '../src/voice-config.js';

const ENV_KEYS = ['REALTIME_PROVIDER', 'DASHSCOPE_API_KEY', 'GEMINI_API_KEY', 'GEMINI_VOICE_API_KEY'] as const;
let saved: Record<string, string | undefined> = {};

const phoneConfig: VoiceConfig = {
	model: 'gemini-2.5-flash-native-audio-preview-12-2025',
	googleSearch: true,
	owner_mode: false,
	channels: {},
};

beforeEach(() => {
	saved = {};
	for (const k of ENV_KEYS) {
		saved[k] = process.env[k];
		delete process.env[k];
	}
	process.env.GEMINI_VOICE_API_KEY = 'a'.repeat(32);
});

afterEach(() => {
	for (const k of ENV_KEYS) {
		if (saved[k] === undefined) delete process.env[k];
		else process.env[k] = saved[k];
	}
});

test('phone bootstrap defaults to gemini transport', () => {
	const rt = bootstrapPhoneRealtimeSession({
		voiceConfig: phoneConfig,
		geminiNativeAudioModel: phoneConfig.model,
		geminiSpeechVoice: 'Aoede',
	});
	assert.equal(rt.config.provider, 'gemini');
	assert.equal(rt.transport, undefined);
	assert.equal(rt.descriptor.surface, 'phone');
	assert.equal(rt.config.googleSearch, true);
});

test('phone bootstrap qwen disables googleSearch', () => {
	process.env.REALTIME_PROVIDER = 'qwen';
	process.env.DASHSCOPE_API_KEY = 'sk-test-key-12345678901234567890';
	const rt = bootstrapPhoneRealtimeSession({
		voiceConfig: phoneConfig,
		geminiNativeAudioModel: phoneConfig.model,
	});
	assert.equal(rt.config.provider, 'qwen');
	assert.ok(rt.transport);
	assert.equal(rt.config.googleSearch, false);
	assert.equal(rt.config.capabilities.googleSearch, false);
});
