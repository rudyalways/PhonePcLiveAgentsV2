/**
 * DashScope / Qwen Omni transport close patterns.
 */

import type { ClassifiedClose } from '../types.js';

const PATTERNS: Array<{
	rx: RegExp;
	category: ClassifiedClose['category'];
	retryable: boolean;
	userMessage: string;
	userActionUrl: string;
}> = [
	{
		rx: /invalid.{0,20}api.?key|api.?key.{0,20}invalid|unauthorized|\b401\b|\b403\b|InvalidApiKey/i,
		category: 'auth_invalid',
		retryable: false,
		userMessage: 'Voice is offline — DashScope API key is invalid. Update DASHSCOPE_API_KEY in .env.',
		userActionUrl: 'https://www.alibabacloud.com/help/en/model-studio/get-api-key',
	},
	{
		rx: /quota|insufficient.{0,20}balance|Arrearage|exceeded.{0,20}limit/i,
		category: 'quota_exceeded',
		retryable: false,
		userMessage: 'Voice is offline — DashScope quota or balance exceeded. Check Alibaba Cloud billing.',
		userActionUrl: 'https://usercenter2.aliyun.com/home',
	},
	{
		rx: /model.{0,20}not.{0,20}found|invalid.{0,20}model|\b404\b/i,
		category: 'model_not_found',
		retryable: false,
		userMessage: 'Voice is offline — configured Qwen realtime model is unavailable. Update REALTIME_MODEL in .env.',
		userActionUrl: 'https://www.alibabacloud.com/help/en/model-studio/realtime',
	},
	{
		rx: /audio.{0,20}before.{0,20}image|image.{0,20}before.{0,20}audio|must.{0,20}send.{0,20}audio/i,
		category: 'unknown',
		retryable: true,
		userMessage: 'Vision frame rejected — speak first so audio reaches the model before Watch.',
		userActionUrl: '',
	},
	{
		rx: /image.{0,20}size|too.{0,20}large|256.{0,10}kb/i,
		category: 'unknown',
		retryable: true,
		userMessage: 'Vision frame too large for Qwen Omni — reduce Watch resolution or JPEG quality.',
		userActionUrl: '',
	},
	{
		rx: /rate.?limit|too many requests|\b429\b/i,
		category: 'rate_limit',
		retryable: true,
		userMessage: 'Voice is briefly rate-limited; reconnecting.',
		userActionUrl: '',
	},
];

export function classifyQwenTransportClose(
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
