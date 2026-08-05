/**
 * Provider-neutral vision frame injection (Phase 2).
 * Default: Gemini sendFile passthrough. Qwen path gated on REALTIME_VISION_ADAPTER.
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
		if (frame.data.length > capabilities.maxImageBytes) {
			return { ok: false, error: `frame exceeds ${capabilities.maxImageBytes} bytes` };
		}
		// Phase 2 full implementation: transport.sendEvent({ type: 'input_image_buffer.append', ... })
		const sendEvent = (transport as { sendEvent?: (e: unknown) => void }).sendEvent;
		if (typeof sendEvent !== 'function') {
			return { ok: false, error: 'transport lacks sendEvent for input_image_buffer.append' };
		}
		sendEvent({
			type: 'input_image_buffer.append',
			image: frame.data.toString('base64'),
		});
		return { ok: true, bytesSent: frame.data.length };
	}

	// Gemini / default: bodhi sendFile
	const t = transport as { sendFile?: (b64: string, mime: string) => void };
	if (typeof t.sendFile !== 'function') {
		return { ok: false, error: 'transport lacks sendFile' };
	}
	t.sendFile(frame.data.toString('base64'), frame.mimeType);
	return { ok: true, bytesSent: frame.data.length };
}
