/**
 * Realtime provider factory — single entry for voice / phone / observability.
 * Default: REALTIME_PROVIDER=gemini (unchanged behavior).
 * Rollback: REALTIME_PROVIDER=gemini REALTIME_USE_FACTORY=0
 */

import { capabilitiesForProvider, isOpenAICompatProvider, telemetryProviderId } from './capabilities.js';
import { resolveRealtimeConfig, validateRealtimeConfig, type ResolveRealtimeConfigOptions } from './config.js';
import { buildOpenAICompatTransport } from './openai-compat.js';
import {
	initMigrationStateIfMissing,
	markPhase,
	migrationStatePath,
	readMigrationState,
	recordRollbackSnapshot,
	writeMigrationState,
} from './migration-state.js';
import type {
	RealtimeConfig,
	RealtimeSessionDescriptor,
	RealtimeTransportResult,
} from './types.js';

export type {
	RealtimeCapabilities,
	RealtimeConfig,
	RealtimePhaseFlags,
	RealtimeProviderId,
	RealtimeProviderMigrationState,
	RealtimeSessionDescriptor,
	RealtimeTransportResult,
	PhaseRecord,
	PhaseStatus,
} from './types.js';

export {
	capabilitiesForProvider,
	isOpenAICompatProvider,
	telemetryProviderId,
} from './capabilities.js';
export { resolveRealtimeConfig, resolvePhaseFlags, useFactoryEnabled, validateRealtimeConfig } from './config.js';
export { buildOpenAICompatTransport } from './openai-compat.js';
export { injectVisionFrame } from './vision-adapter.js';
export {
	initMigrationStateIfMissing,
	markPhase,
	migrationStatePath,
	readMigrationState,
	recordRollbackSnapshot,
	writeMigrationState,
	MIGRATION_SCHEMA_VERSION,
	MIGRATION_FILENAME,
} from './migration-state.js';

export function describeRealtimeSession(
	config: RealtimeConfig,
	surface = 'web',
): RealtimeSessionDescriptor {
	return {
		provider: config.provider,
		telemetryProvider: telemetryProviderId(config.provider),
		model: config.model,
		voice: config.voice,
		capabilities: config.capabilities,
		surface,
	};
}

export interface CreateRealtimeSessionOptions extends ResolveRealtimeConfigOptions {
	surface?: string;
}

/**
 * Resolve config, validate keys, optionally build OpenAI-compat transport.
 * Gemini returns transport=undefined — caller passes geminiModel/speechConfig to bodhi.
 */
export function createRealtimeSession(opts: CreateRealtimeSessionOptions = {}): RealtimeTransportResult {
	const config = resolveRealtimeConfig(opts);
	const err = validateRealtimeConfig(config);
	if (err) {
		throw new Error(err);
	}

	const descriptor = describeRealtimeSession(config, opts.surface);

	if (config.provider === 'gemini') {
		return {
			transport: undefined,
			config,
			descriptor,
			sessionApiKey: config.apiKey,
			geminiNativeAudioModel: config.model,
			geminiSpeechVoice: config.voice,
		};
	}

	if (!config.useFactory) {
		throw new Error(
			`REALTIME_USE_FACTORY=0 with REALTIME_PROVIDER=${config.provider} is unsupported; ` +
			'set REALTIME_PROVIDER=gemini or REALTIME_USE_FACTORY=1',
		);
	}

	if (!isOpenAICompatProvider(config.provider)) {
		throw new Error(`Unknown realtime provider: ${config.provider}`);
	}

	return {
		transport: buildOpenAICompatTransport(config),
		config,
		descriptor,
		sessionApiKey: config.apiKey,
	};
}

/** Startup helper: init migration checkpoint + create session. */
export function bootstrapRealtimeProvider(
	workspace?: string,
	opts: CreateRealtimeSessionOptions = {},
): RealtimeTransportResult {
	initMigrationStateIfMissing(workspace);
	markPhase(1, 'in_progress', 'factory loaded at voice-agent startup', workspace);
	return createRealtimeSession(opts);
}

export function completePhase1Bootstrap(workspace?: string): void {
	recordRollbackSnapshot(
		(process.env.REALTIME_PROVIDER || 'gemini').toLowerCase() as RealtimeConfig['provider'],
		(process.env.REALTIME_USE_FACTORY || '1') !== '0',
		(process.env.REALTIME_VISION_ADAPTER || '').toLowerCase() === '1',
		workspace,
	);
	markPhase(1, 'complete', 'voice-agent bound transport via factory', workspace);
}
