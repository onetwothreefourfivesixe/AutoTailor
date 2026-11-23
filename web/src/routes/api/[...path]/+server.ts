import type { RequestHandler } from './$types';
import { api } from '$lib/server/api';

/**
 * Relay browser calls to the local API, adding the token. The browser never sees the
 * token; hooks.server.ts has already checked the session. Streams (server-sent events)
 * pass through as they arrive.
 */
const relay: RequestHandler = async ({ request, params, url }) => {
	const headers = new Headers();
	for (const h of ['content-type', 'accept']) {
		const v = request.headers.get(h);
		if (v) headers.set(h, v);
	}
	const hasBody = !['GET', 'HEAD'].includes(request.method);
	const res = await api(`/api/${params.path}${url.search}`, {
		method: request.method,
		headers,
		body: hasBody ? await request.arrayBuffer() : undefined,
		signal: request.signal
	});
	const out = new Headers();
	for (const h of ['content-type', 'content-disposition', 'x-total-count', 'x-pages', 'x-template', 'x-warning', 'cache-control']) {
		const v = res.headers.get(h);
		if (v) out.set(h, v);
	}
	return new Response(res.body, { status: res.status, headers: out });
};

export const GET = relay;
export const POST = relay;
export const PUT = relay;
export const PATCH = relay;
export const DELETE = relay;
