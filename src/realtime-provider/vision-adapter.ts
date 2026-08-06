/**
 * Provider-neutral vision frame injection (Phase 2).
 */

import type { LLMTransport } from 'bodhi-realtime-agent';
import type { RealtimeCapabilities, VisionInjectResult } from './types.js';

export interface VisionFrameInput {
	data: Buffer;
	mimeType: string;
}

export interface VisionInjectContext {
	audioSent: boolean;
	framesSent: number;
}

function sendTransportEvent(transport: LLMTransport, event: Record<string, unknown>): boolean {
	const t = transport as unknown as Record<string, unknown>;
	const ts = () => new Date().toISOString().slice(11, 23);

	console.log(`${ts()} [Vision-Adapter] Attempting to send event type: ${event.type}`);

	if (typeof t.sendEvent === 'function') {
		console.log(`${ts()} [Vision-Adapter] ✓ Using t.sendEvent()`);
		(t.sendEvent as (e: unknown) => void)(event);
		return true;
	}
	const session = t.session as Record<string, unknown> | undefined;
	if (session && typeof session.send === 'function') {
		console.log(`${ts()} [Vision-Adapter] ✓ Using session.send()`);
		(session.send as (e: unknown) => void)(event);
		return true;
	}
	if (typeof t.send === 'function') {
		console.log(`${ts()} [Vision-Adapter] ✓ Using t.send()`);
		(t.send as (e: unknown) => void)(event);
		return true;
	}
	const ws = t.ws as { send?: (data: string) => void } | undefined;
	if (ws && typeof ws.send === 'function') {
		console.log(`${ts()} [Vision-Adapter] ✓ Using ws.send()`);
		ws.send(JSON.stringify(event));
		return true;
	}
	console.error(`${ts()} [Vision-Adapter] ✗ No send method found! Transport keys: ${Object.keys(t).join(', ')}`);
	return false;
}

export function injectVisionFrame(
	transport: LLMTransport | null | undefined,
	capabilities: RealtimeCapabilities,
	frame: VisionFrameInput,
	ctx: VisionInjectContext,
	phaseVisionAdapter: boolean,
): VisionInjectResult {
	if (!transport) {
		return { ok: false, error: 'no active transport' };
	}

	if (capabilities.vision === 'none') {
		return { ok: false, error: 'vision unsupported for this provider' };
	}

	if (capabilities.vision === 'input_image_buffer') {
		if (!phaseVisionAdapter) {
			return {
				ok: false,
				error: 'Qwen vision requires REALTIME_VISION_ADAPTER=1 (Phase 2). Watch disabled on Omni until then.',
			};
		}

		// Qwen requires audio before vision. If no audio has been sent yet, send ~100ms silence first.
		if (capabilities.requiresAudioBeforeVision && !ctx.audioSent) {
			const ts = () => new Date().toISOString().slice(11, 23);
			console.log(`${ts()} [Vision-Adapter] Sending 100ms silence before first image frame (Qwen requirement)`);

			// 100ms at 16kHz = 1600 samples, PCM16 = 3200 bytes
			const silenceBuffer = Buffer.alloc(3200, 0);
			const silenceEvent = {
				type: 'input_audio_buffer.append',
				event_id: `audio_silence_${Date.now()}`,
				audio: silenceBuffer.toString('base64'),
			};

			if (!sendTransportEvent(transport, silenceEvent)) {
				return { ok: false, error: 'failed to send silence audio before vision frame' };
			}

			// Mark audio as sent so subsequent frames don't repeat this
			ctx.audioSent = true;
		}

		const rawLimit = capabilities.maxImageBytes;
		// Omni limit is base64 size; compare raw bytes conservatively (raw < limit/1.34).
		if (frame.data.length > Math.floor(rawLimit * 0.75)) {
			return { ok: false, error: `frame exceeds ~${rawLimit} bytes provider limit` };
		}
		const event = {
			type: 'input_image_buffer.append',
			event_id: `vision_${Date.now()}`,
			image: frame.data.toString('base64'),
		};
		if (!sendTransportEvent(transport, event)) {
			return { ok: false, error: 'transport lacks send path for input_image_buffer.append' };
		}

		// NOTE: Do NOT manually commit in VAD mode!
		// According to Qwen docs: "启用服务端VAD时，服务端会在检测到语音结束时自动提交数据并触发响应"
		// Manual commit is only needed in Manual mode (when VAD is disabled).
		// Since we use semantic_vad by default, the server will auto-commit when speech ends.

		return { ok: true, bytesSent: frame.data.length };
	}

	const t = transport as { sendFile?: (b64: string, mime: string) => void };
	if (typeof t.sendFile !== 'function') {
		return { ok: false, error: 'transport lacks sendFile' };
	}
	t.sendFile(frame.data.toString('base64'), frame.mimeType);
	return { ok: true, bytesSent: frame.data.length };
}
