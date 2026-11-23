import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// One throwaway data folder per test run, shared with the server script through E2E_HOME.
process.env.E2E_HOME ??= mkdtempSync(join(tmpdir(), 'autotailor-e2e-'));
export const SESSION = join(process.env.E2E_HOME, 'session.json');

export default defineConfig({
	testDir: 'e2e',
	timeout: 60_000,
	workers: 1,
	use: { baseURL: 'http://127.0.0.1:8696', viewport: { width: 1400, height: 900 } },
	projects: [
		{ name: 'sign-in', testMatch: /signin\.setup\.ts/ },
		{ name: 'app', testMatch: /\.spec\.ts/, dependencies: ['sign-in'], use: { storageState: SESSION } }
	],
	webServer: {
		command: 'uv run python ../tests/e2e_server.py',
		url: 'http://127.0.0.1:8696/login',
		timeout: 120_000,
		reuseExistingServer: false,
		stdout: 'pipe',
		stderr: 'pipe',
		env: { E2E_HOME: process.env.E2E_HOME }
	}
});
