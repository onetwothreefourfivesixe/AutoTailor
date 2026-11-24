// Read at run time from the server's environment (set by `autotailor ui`); server-only module.
const env = process.env;
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

/** The AutoTailor data folder and the local API this server talks to. */
export function home(): string {
	return resolve(env.AUTOTAILOR_HOME || process.cwd());
}

export function apiUrl(): string {
	return (env.AUTOTAILOR_API_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
}

let cached: string | undefined;

/** The API token (data/api_token). Read on the server only; it never reaches the browser. */
export function apiToken(): string {
	cached ??= readFileSync(join(home(), 'data', 'api_token'), 'utf-8').trim();
	return cached;
}
