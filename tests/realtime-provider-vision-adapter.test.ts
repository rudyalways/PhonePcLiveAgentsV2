/**
 * Vision adapter unit tests (Phase 2).
 */
import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';
import { injectVisionFrame } from '../src/realtime-provider/vision-adapter.js';
import { capabilitiesForProvider } from '../src/realtime-provider/capabilities.js';

describe('injectVisionFrame', () => {
	test('gemini sendFile path', () => {
		let sent = false;
		const transport = {
			sendFile: () => { sent = true; },
		};
		const caps = capabilitiesForProvider('gemini');
		const r = injectVisionFrame(
			transport as never,
			caps,
			{ data: Buffer.from('abc'), mimeType: 'image/jpeg' },
			{ audioSent: false, framesSent: 0 },
			false,
		);
		assert.equal(r.ok, true);
		assert.equal(sent, true);
	});

	test('qwen blocked without phase flag', () => {
		const caps = capabilitiesForProvider('qwen');
		const r = injectVisionFrame(
			{ sendEvent: () => {} } as never,
			caps,
			{ data: Buffer.alloc(100), mimeType: 'image/jpeg' },
			{ audioSent: true, framesSent: 0 },
			false,
		);
		assert.equal(r.ok, false);
		assert.match(r.error ?? '', /REALTIME_VISION_ADAPTER/);
	});

	test('qwen requires audio first when adapter enabled', () => {
		const caps = capabilitiesForProvider('qwen');
		const r = injectVisionFrame(
			{ sendEvent: () => {} } as never,
			caps,
			{ data: Buffer.alloc(100), mimeType: 'image/jpeg' },
			{ audioSent: false, framesSent: 0 },
			true,
		);
		assert.equal(r.ok, false);
		assert.match(r.error ?? '', /audio must be sent/);
	});

	test('qwen sendEvent when adapter enabled', () => {
		let ev: Record<string, unknown> | null = null;
		const transport = { sendEvent: (e: Record<string, unknown>) => { ev = e; } };
		const caps = capabilitiesForProvider('qwen');
		const r = injectVisionFrame(
			transport as never,
			caps,
			{ data: Buffer.alloc(200), mimeType: 'image/jpeg' },
			{ audioSent: true, framesSent: 1 },
			true,
		);
		assert.equal(r.ok, true);
		assert.equal(ev?.type, 'input_image_buffer.append');
	});
});
