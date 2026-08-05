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
	if (typeof t.sendEvent === 'function') {
		(t.sendEvent as (e: unknown) => void)(event);
		return true;
	}
	const session = t.session as Record<string, unknown> | undefined;
	if (session && typeof session.send === 'function') {
		(session.send as (e: unknown) => void)(event);
		return true;
	}
	if (typeof t.send === 'function') {
		(t.send as (e: unknown) => void)(event);
		return true;
	}
	const ws = t.ws as { send?: (data: string) => void } | undefined;
	if (ws && typeof ws.send === 'function') {
		ws.send(JSON.stringify(event));
		return true;
	}
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
		if (capabilities.requiresAudioBeforeVision && !ctx.audioSent) {
			return { ok: false, error: 'audio must be sent before vision frames (Omni requirement)' };
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
		return { ok: true, bytesSent: frame.data.length };
	}

	const t = transport as { sendFile?: (b64: string, mime: string) => void };
	if (typeof t.sendFile !== 'function') {
		return { ok: false, error: 'transport lacks sendFile' };
	}
	t.sendFile(frame.data.toString('base64'), frame.mimeType);
	return { ok: true, bytesSent: frame.data.length };
}
