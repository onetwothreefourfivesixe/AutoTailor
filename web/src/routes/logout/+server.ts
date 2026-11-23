import { redirect } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { COOKIE, end } from '$lib/server/session';

export const POST: RequestHandler = async ({ cookies }) => {
	end(cookies.get(COOKIE));
	cookies.delete(COOKIE, { path: '/' });
	redirect(303, '/login');
};
