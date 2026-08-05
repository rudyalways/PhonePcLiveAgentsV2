/**
 * Provider-dispatch error classifier tests (Phase 2).
 */
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { classifyTransportClose, classifyQwenTransportClose } from '../src/realtime-provider/errors/index.js';

test('gemini default path unchanged', () => {
	const r = classifyTransportClose('gemini', 1011, 'You exceeded your current quota');
	assert.equal(r.category, 'quota_exceeded');
	assert.equal(r.retryable, false);
});

test('qwen auth invalid', () => {
	const r = classifyQwenTransportClose(401, 'InvalidApiKey: API key is invalid');
	assert.equal(r.category, 'auth_invalid');
	assert.equal(r.retryable, false);
	assert.match(r.userMessage, /DashScope/i);
});

test('dispatch routes qwen separately', () => {
	const r = classifyTransportClose('qwen', 401, 'InvalidApiKey');
	assert.equal(r.category, 'auth_invalid');
});
