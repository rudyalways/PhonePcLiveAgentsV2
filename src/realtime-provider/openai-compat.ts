/**
 * OpenAI-wire-compatible realtime transport (Qwen, OpenAI, MiniMax).
 */

import { OpenAIRealtimeTransport } from 'bodhi-realtime-agent';
import type { LLMTransport } from 'bodhi-realtime-agent';
import type { RealtimeConfig } from './types.js';

export function buildOpenAICompatTransport(config: RealtimeConfig): LLMTransport {
	// bodhi OpenAIRealtimeTransport reads these env vars internally.
	process.env.OPENAI_API_KEY = config.apiKey;
	if (config.baseUrl) {
		process.env.OPENAI_BASE_URL = config.baseUrl;
	}

	const transportOpts: ConstructorParameters<typeof OpenAIRealtimeTransport>[0] = {
		apiKey: config.apiKey,
		model: config.model,
		voice: config.voice,
	};

	if (config.provider === 'qwen') {
		transportOpts.transcriptionModel = config.transcriptionModel;
		transportOpts.turnDetection = config.turnDetection as Record<string, unknown> | undefined;
	}

	return new OpenAIRealtimeTransport(transportOpts);
}
