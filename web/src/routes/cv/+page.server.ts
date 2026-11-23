import type { PageServerLoad } from './$types';
import { json, maybe } from '$lib/server/api';

export type BulletUse = { run_id: number; posting_id: string; company: string; title: string };

export const load: PageServerLoad = async () => {
	// No CV yet is a normal state (the page points to setup), not an error.
	const cv = await maybe<Record<string, any>>('/api/cv');
	const usage = cv ? await json<Record<string, BulletUse[]>>('/api/cv/usage') : {};
	return { cv, usage };
};
