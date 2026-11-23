/** Browser-side calls to the local API, relayed (with the token) by this app's server. */

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string,
		public detail: unknown
	) {
		super(message);
	}
}

async function handle<T>(res: Response): Promise<T> {
	if (!res.ok) {
		let detail: unknown = null;
		let message = `Request failed (${res.status})`;
		try {
			const body = await res.json();
			detail = body.detail;
			if (typeof body.detail === 'string') message = body.detail;
			else if (body.detail?.message) message = body.detail.message;
			else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg?: string; loc?: string[] }) => `${(d.loc ?? []).join('.')}: ${d.msg}`).join('; ');
		} catch {
			/* not JSON */
		}
		throw new ApiError(res.status, message, detail);
	}
	const type = res.headers.get('content-type') ?? '';
	return (type.includes('json') ? res.json() : res.text()) as Promise<T>;
}

export function get<T>(path: string): Promise<T> {
	return fetch(`/api${path}`).then((r) => handle<T>(r));
}

export function send<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
	return fetch(`/api${path}`, {
		method,
		headers: body === undefined ? {} : { 'content-type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	}).then((r) => handle<T>(r));
}

export function upload<T>(path: string, form: FormData): Promise<T> {
	return fetch(`/api${path}`, { method: 'POST', body: form }).then((r) => handle<T>(r));
}

/** Start a background task by typed command (same allowlist as the CLI box). */
export function command(text: string) {
	return send<{ id: number }>('POST', '/tasks', { command: text });
}
