import { error } from '@sveltejs/kit';
import { apiToken, apiUrl } from './config';

/** Call the local API from the server, with the token. */
export async function api(path: string, init: RequestInit = {}): Promise<Response> {
	const headers = new Headers(init.headers);
	headers.set('X-AutoTailor-Token', apiToken());
	try {
		return await fetch(`${apiUrl()}${path}`, { ...init, headers });
	} catch {
		error(503, 'The AutoTailor API is not running. Start everything with `autotailor ui`.');
	}
}

/** GET JSON from the API; HTTP errors become SvelteKit errors with the API's message. */
export async function json<T>(path: string): Promise<T> {
	const res = await api(path);
	if (!res.ok) {
		let message = `${res.status}`;
		try {
			const body = await res.json();
			message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
		} catch {
			/* not JSON */
		}
		error(res.status, message);
	}
	return res.json() as Promise<T>;
}

/** Like json(), but a 404 gives null. */
export async function maybe<T>(path: string): Promise<T | null> {
	const res = await api(path);
	if (res.status === 404) return null;
	if (!res.ok) error(res.status, `${res.status}`);
	return res.json() as Promise<T>;
}
