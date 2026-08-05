/**
 * Realtime LLM provider — shared types for factory + migration.
 * See docs/realtime-provider-design.md
 */

import type { LLMTransport } from 'bodhi-realtime-agent';

export type RealtimeProviderId = 'gemini' | 'qwen' | 'openai' | 'minimax';

export type VisionInjectMode = 'sendFile' | 'input_image_buffer' | 'none';

export interface TurnDetectionConfig {
	type: string;
	threshold?: number;
	prefix_padding_ms?: number;
	silence_duration_ms?: number;
}

export interface RealtimeCapabilities {
	nativeAudio: boolean;
	vision: VisionInjectMode;
	toolCalling: boolean;
	googleSearch: boolean;
	builtinWebSearch: boolean;
	inputSampleRate: number;
	outputSampleRate: number;
	maxVisionFps: number;
	maxImageBytes: number;
	requiresAudioBeforeVision: boolean;
	maxSessionMinutes: number;
	maxVisionContextSeconds: number;
}

export interface RealtimeConfig {
	provider: RealtimeProviderId;
	model: string;
	voice: string;
	baseUrl?: string;
	apiKey: string;
	apiKeySource: string;
	capabilities: RealtimeCapabilities;
	turnDetection?: TurnDetectionConfig;
	transcriptionModel: string | null;
	googleSearch: boolean;
	useFactory: boolean;
	phaseFlags: RealtimePhaseFlags;
}

export interface RealtimePhaseFlags {
	/** Phase 2: route vision through vision-adapter.ts (default off). */
	visionAdapter: boolean;
}

export interface RealtimeSessionDescriptor {
	provider: RealtimeProviderId;
	telemetryProvider: string;
	model: string;
	voice: string;
	capabilities: RealtimeCapabilities;
	surface?: string;
}

export interface VisionInjectResult {
	ok: boolean;
	error?: string;
	bytesSent?: number;
}

export interface RealtimeTransportResult {
	/** Undefined → bodhi GeminiLiveTransport default path. */
	transport?: LLMTransport;
	config: RealtimeConfig;
	descriptor: RealtimeSessionDescriptor;
	/** Session apiKey bodhi expects on VoiceSession constructor. */
	sessionApiKey: string;
	geminiNativeAudioModel?: string;
	geminiSpeechVoice?: string;
}

export type PhaseStatus = 'pending' | 'in_progress' | 'complete' | 'skipped' | 'rolled_back';

export interface PhaseRecord {
	status: PhaseStatus;
	started_at?: string;
	completed_at?: string;
	notes?: string;
}

export interface RealtimeProviderMigrationState {
	schema_version: number;
	updated_at: string;
	current_phase: number;
	phases: Record<string, PhaseRecord>;
	rollback: {
		provider: RealtimeProviderId;
		use_factory: boolean;
		vision_adapter: boolean;
	};
}
