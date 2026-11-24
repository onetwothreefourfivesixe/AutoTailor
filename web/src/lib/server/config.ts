import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

// The variables `autotailor ui` sets for this server, read straight from the process: SvelteKit 3's
// `$env` modules only expose variables declared in a `src/env` schema, so these came back undefined
// and the server quietly fell back to port 8765 and the current folder.
const env = process.env;

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
