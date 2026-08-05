/**
 * Durable migration checkpoint for realtime-provider rollout.
 * Survives restarts — lives under workspace/state/.
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { resolveWorkspace } from '../workspace_default.js';
import type {
	PhaseRecord,
	PhaseStatus,
	RealtimeProviderMigrationState,
	RealtimeProviderId,
} from './types.js';

export const MIGRATION_SCHEMA_VERSION = 1;
export const MIGRATION_FILENAME = 'realtime-provider-migration.json';

const PHASE_IDS = ['0', '1', '2', '3', '4'] as const;

export function migrationStatePath(workspace?: string): string {
	const ws = workspace || resolveWorkspace();
	return join(ws, 'state', MIGRATION_FILENAME);
}

function defaultState(): RealtimeProviderMigrationState {
	const phases: Record<string, PhaseRecord> = {};
	for (const id of PHASE_IDS) {
		phases[id] = { status: id === '0' ? 'complete' : 'pending' };
	}
	return {
		schema_version: MIGRATION_SCHEMA_VERSION,
		updated_at: new Date().toISOString(),
		current_phase: 1,
		phases,
		rollback: {
			provider: 'gemini',
			use_factory: true,
			vision_adapter: false,
		},
	};
}

export function readMigrationState(workspace?: string): RealtimeProviderMigrationState {
	const path = migrationStatePath(workspace);
	if (!existsSync(path)) {
		return defaultState();
	}
	try {
		const raw = JSON.parse(readFileSync(path, 'utf-8')) as RealtimeProviderMigrationState;
		if (raw.schema_version !== MIGRATION_SCHEMA_VERSION) {
			return { ...defaultState(), ...raw, schema_version: MIGRATION_SCHEMA_VERSION };
		}
		return raw;
	} catch {
		return defaultState();
	}
}

export function writeMigrationState(
	state: RealtimeProviderMigrationState,
	workspace?: string,
): void {
	const path = migrationStatePath(workspace);
	mkdirSync(join(path, '..'), { recursive: true });
	const next: RealtimeProviderMigrationState = {
		...state,
		updated_at: new Date().toISOString(),
	};
	const tmp = `${path}.tmp-${process.pid}`;
	writeFileSync(tmp, JSON.stringify(next, null, 2) + '\n');
	renameSync(tmp, path);
}

export function markPhase(
	phase: number | string,
	status: PhaseStatus,
	notes?: string,
	workspace?: string,
): RealtimeProviderMigrationState {
	const state = readMigrationState(workspace);
	const key = String(phase);
	const prev = state.phases[key] || { status: 'pending' as PhaseStatus };
	const now = new Date().toISOString();
	const record: PhaseRecord = {
		...prev,
		status,
		notes: notes ?? prev.notes,
	};
	if (status === 'in_progress' && !record.started_at) record.started_at = now;
	if (status === 'complete' || status === 'rolled_back') record.completed_at = now;
	state.phases[key] = record;
	if (status === 'complete') {
		const n = Number(phase);
		if (!Number.isNaN(n) && n >= state.current_phase) {
			state.current_phase = Math.min(n + 1, 4);
		}
	}
	writeMigrationState(state, workspace);
	return state;
}

export function recordRollbackSnapshot(
	provider: RealtimeProviderId,
	useFactory: boolean,
	visionAdapter: boolean,
	workspace?: string,
): void {
	const state = readMigrationState(workspace);
	state.rollback = { provider, use_factory: useFactory, vision_adapter: visionAdapter };
	writeMigrationState(state, workspace);
}

export function initMigrationStateIfMissing(workspace?: string): RealtimeProviderMigrationState {
	const path = migrationStatePath(workspace);
	if (existsSync(path)) return readMigrationState(workspace);
	const state = defaultState();
	state.phases['0'] = {
		status: 'complete',
		completed_at: new Date().toISOString(),
		notes: 'Vendor spikes: test-qwen-realtime-tools.py',
	};
	writeMigrationState(state, workspace);
	return state;
}
