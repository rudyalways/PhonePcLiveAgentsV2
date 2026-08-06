/**
 * realtime-provider config resolution + migration state (Phase 1).
 */
import { strict as assert } from 'node:assert';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, test } from 'node:test';
import {
	capabilitiesForProvider,
	isOpenAICompatProvider,
	resolveRealtimeConfig,
	resolvePhaseFlags,
	useFactoryEnabled,
	validateRealtimeConfig,
	telemetryProviderId,
	initMigrationStateIfMissing,
	markPhase,
	readMigrationState,
	migrationStatePath,
} from '../src/realtime-provider/index.js';

const ENV_KEYS = [
	'REALTIME_PROVIDER',
	'REALTIME_USE_FACTORY',
	'REALTIME_VISION_ADAPTER',
	'DASHSCOPE_API_KEY',
	'REALTIME_MODEL',
] as const;

let savedEnv: Record<string, string | undefined> = {};
let tmpWs: string;

beforeEach(() => {
	savedEnv = {};
	for (const k of ENV_KEYS) {
		savedEnv[k] = process.env[k];
		delete process.env[k];
	}
	tmpWs = mkdtempSync(join(tmpdir(), 'rt-provider-'));
});

afterEach(() => {
	for (const k of ENV_KEYS) {
		if (savedEnv[k] === undefined) delete process.env[k];
		else process.env[k] = savedEnv[k];
	}
});

describe('resolveRealtimeConfig', () => {
	test('defaults to gemini with factory enabled', () => {
		const cfg = resolveRealtimeConfig();
		assert.equal(cfg.provider, 'gemini');
		assert.equal(cfg.useFactory, true);
		assert.equal(cfg.capabilities.vision, 'sendFile');
	});

	test('qwen resolves dashscope caps', () => {
		process.env.REALTIME_PROVIDER = 'qwen';
		process.env.DASHSCOPE_API_KEY = 'sk-test-key-1234567890';
		const cfg = resolveRealtimeConfig();
		assert.equal(cfg.provider, 'qwen');
		assert.equal(cfg.model, 'qwen3.5-omni-plus-realtime');
		assert.equal(cfg.capabilities.vision, 'input_image_buffer');
		assert.equal(cfg.capabilities.requiresAudioBeforeVision, true);
		assert.equal(cfg.transcriptionModel, 'qwen3-asr-flash-realtime');
	});

	test('REALTIME_USE_FACTORY=0 disables factory', () => {
		process.env.REALTIME_USE_FACTORY = '0';
		assert.equal(useFactoryEnabled(), false);
	});

	test('validate rejects missing dashscope key for qwen', () => {
		process.env.REALTIME_PROVIDER = 'qwen';
		const cfg = resolveRealtimeConfig();
		assert.ok(validateRealtimeConfig(cfg));
	});

	test('phase flags default vision adapter off', () => {
		assert.equal(resolvePhaseFlags().visionAdapter, false);
		process.env.REALTIME_VISION_ADAPTER = '1';
		assert.equal(resolvePhaseFlags().visionAdapter, true);
	});
});

describe('capabilities helpers', () => {
	test('telemetry ids', () => {
		assert.equal(telemetryProviderId('gemini'), 'gemini-live');
		assert.equal(telemetryProviderId('qwen'), 'dashscope-omni');
	});

	test('openai compat providers', () => {
		assert.equal(isOpenAICompatProvider('qwen'), true);
		assert.equal(isOpenAICompatProvider('gemini'), false);
		assert.equal(capabilitiesForProvider('qwen').maxSessionMinutes, 120);
	});
});

describe('migration state (continuable)', () => {
	test('init and mark phase survives read', () => {
		initMigrationStateIfMissing(tmpWs);
		const path = migrationStatePath(tmpWs);
		assert.ok(existsSync(path));
		markPhase(1, 'complete', 'test', tmpWs);
		const state = readMigrationState(tmpWs);
		assert.equal(state.phases['1'].status, 'complete');
		assert.equal(state.current_phase, 2);
		const raw = JSON.parse(readFileSync(path, 'utf-8'));
		assert.equal(raw.schema_version, 1);
	});
});
