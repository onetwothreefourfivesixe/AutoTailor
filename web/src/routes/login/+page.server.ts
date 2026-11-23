import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { COOKIE, redeem } from '$lib/server/session';

export const load: PageServerLoad = async ({ url, cookies, locals }) => {
	if (locals.authed) redirect(303, '/');
	const code = url.searchParams.get('code');
	if (!code) return { failed: false };
	const id = redeem(code);
	if (!id) return { failed: true };
	cookies.set(COOKIE, id, { path: '/', httpOnly: true, sameSite: 'strict', secure: false, maxAge: 60 * 60 * 24 * 30 });
	redirect(303, '/');
};
