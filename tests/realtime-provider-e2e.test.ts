/**
 * Phase 3 — Qwen config resolution E2E (no live voice session).
 */
import { strict as assert } from 'node:assert';
import { afterEach, beforeEach, test } from 'node:test';
import { resolveRealtimeConfig, validateRealtimeConfig } from '../src/realtime-provider/index.js';

const SAVED = process.env.REALTIME_PROVIDER;
const KEY = process.env.DASHSCOPE_API_KEY;

beforeEach(() => {
	process.env.REALTIME_PROVIDER = 'qwen';
	process.env.DASHSCOPE_API_KEY = process.env.DASHSCOPE_API_KEY || 'sk-test-key-12345678901234567890';
});

afterEach(() => {
	if (SAVED === undefined) delete process.env.REALTIME_PROVIDER;
	else process.env.REALTIME_PROVIDER = SAVED;
	if (KEY === undefined) delete process.env.DASHSCOPE_API_KEY;
	else process.env.DASHSCOPE_API_KEY = KEY;
});

test('qwen config resolves with valid key', () => {
	const c = resolveRealtimeConfig();
	assert.equal(c.provider, 'qwen');
	assert.equal(c.capabilities.vision, 'input_image_buffer');
	assert.equal(validateRealtimeConfig(c), null);
});
