/**
 * Classify realtime transport close events into actionable categories.
 *
 * Provider dispatch lives in realtime-provider/errors/ (Phase 2).
 * This module preserves the voice-agent import path and Gemini-default
 * signature used by existing tests.
 */

export type { ClassifiedClose, FailureCategory } from './realtime-provider/errors/index.js';
export { classifyGeminiTransportClose, classifyQwenTransportClose } from './realtime-provider/errors/index.js';

import { classifyTransportClose as classifyByProvider } from './realtime-provider/errors/index.js';
import type { ClassifiedClose } from './realtime-provider/errors/index.js';
import type { RealtimeProviderId } from './realtime-provider/types.js';

export function classifyTransportClose(
	code: number | undefined,
	reason: string | undefined,
	provider: RealtimeProviderId | string = 'gemini',
): ClassifiedClose {
	return classifyByProvider(provider, code, reason);
}
