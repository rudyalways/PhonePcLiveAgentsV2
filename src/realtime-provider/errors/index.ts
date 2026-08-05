/**
 * Provider-dispatch transport close classifier (Phase 2).
 */

import type { RealtimeProviderId } from '../types.js';
import type { ClassifiedClose } from '../types.js';
import { classifyGeminiTransportClose } from './gemini.js';
import { classifyQwenTransportClose } from './qwen.js';

export type { ClassifiedClose, FailureCategory } from '../types.js';
export { classifyGeminiTransportClose } from './gemini.js';
export { classifyQwenTransportClose } from './qwen.js';

export function classifyTransportClose(
	provider: RealtimeProviderId | string,
	code: number | undefined,
	reason: string | undefined,
): ClassifiedClose {
	const p = (provider || 'gemini').toLowerCase();
	if (p === 'qwen') return classifyQwenTransportClose(code, reason);
	// openai, minimax, gemini default → gemini patterns for now (OpenAI TBD)
	return classifyGeminiTransportClose(code, reason);
}
