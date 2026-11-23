import { redirect, type Handle } from '@sveltejs/kit';
import { COOKIE, valid } from '$lib/server/session';

const LOCAL = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * Every request: the Host must be this machine (blocks DNS rebinding), and outside /login
 * the session cookie must be valid. Pages redirect to /login; /api calls get 401.
 */
export const handle: Handle = async ({ event, resolve }) => {
	const host = (event.request.headers.get('host') ?? '').replace(/:\d+$/, '');
	if (!LOCAL.has(host)) return new Response('Only this machine may use AutoTailor.', { status: 403 });

	const path = event.url.pathname;
	event.locals.authed = valid(event.cookies.get(COOKIE));
	const open = path === '/login' || path.startsWith('/_app/') || path === '/favicon.svg';
	if (!event.locals.authed && !open) {
		if (path.startsWith('/api/')) return new Response('Not signed in.', { status: 401 });
		redirect(303, '/login');
	}
	return resolve(event);
};
