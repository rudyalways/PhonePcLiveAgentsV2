/**
 * Gemini Live transport close patterns (extracted from voice-error-classifier).
 */

import type { ClassifiedClose, FailureCategory } from '../types.js';

export type { ClassifiedClose, FailureCategory };

const PATTERNS: Array<{
	rx: RegExp;
	category: FailureCategory;
	retryable: boolean;
	userMessage: string;
	userActionUrl: string;
}> = [
	{
		rx: /prepayment.{0,20}credits.{0,20}depleted|prepayment.{0,20}depleted/i,
		category: 'credits_depleted',
		retryable: false,
		userMessage: 'Voice is offline — Gemini prepayment credits are depleted. Top up to restore voice.',
		userActionUrl: 'https://ai.studio/projects',
	},
	{
		rx: /exceeded your current quota|quota.{0,20}exceeded|quota.{0,20}exhausted/i,
		category: 'quota_exceeded',
		retryable: false,
		userMessage: 'Voice is offline — Gemini quota exceeded. Enable billing on the linked GCP project to restore voice.',
		userActionUrl: 'https://console.cloud.google.com/billing',
	},
	{
		rx: /api.?key.{0,20}(not valid|invalid)|invalid.{0,20}api.?key|api_key_invalid|permission_denied|unauthorized|\b401\b|\b403\b/i,
		category: 'auth_invalid',
		retryable: false,
		userMessage: 'Voice is offline — Gemini API key is invalid or revoked. Update GEMINI_API_KEY in .env.',
		userActionUrl: 'https://aistudio.google.com/apikey',
	},
	{
		rx: /is not found for API version|not supported for|\bmodels?\/\S+\s+(is\s+)?not\s+found|\b404\b|\bdeprecated\b/i,
		category: 'model_not_found',
		retryable: false,
		userMessage: 'Voice is offline — configured Gemini voice model is unavailable. Update VOICE_NATIVE_AUDIO_MODEL in .env.',
		userActionUrl: 'https://ai.google.dev/gemini-api/docs/models',
	},
	{
		rx: /rate.?limit|too many requests|\b429\b/i,
		category: 'rate_limit',
		retryable: true,
		userMessage: 'Voice is briefly rate-limited; reconnecting.',
		userActionUrl: '',
	},
];

export function classifyGeminiTransportClose(
	code: number | undefined,
	reason: string | undefined,
): ClassifiedClose {
	const text = (reason ?? '').trim();
	for (const p of PATTERNS) {
		if (p.rx.test(text)) {
			return {
				category: p.category,
				retryable: p.retryable,
				userMessage: p.userMessage,
				userActionUrl: p.userActionUrl || undefined,
				rawCode: code,
				rawReason: text,
			};
		}
	}
	return {
		category: code === 1000 ? 'transient' : 'unknown',
		retryable: true,
		userMessage: '',
		rawCode: code,
		rawReason: text,
	};
}
