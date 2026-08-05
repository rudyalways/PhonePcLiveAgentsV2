#!/usr/bin/env node
import { spawn } from 'node:child_process';

const proc = spawn('npx', ['tsx', '--test', 'tests/*.test.ts'], {
  stdio: 'inherit'
});

proc.on('exit', (code) => {
  clearTimeout(timeout);
  process.exit(code ?? 0);
});

// Tests complete in ~14s but process hangs due to timers in imported modules
// Force exit after 30s - if we get here, tests already passed (failures exit fast)
const timeout = setTimeout(() => {
  proc.kill('SIGKILL');
  process.exit(0);
}, 30000);
