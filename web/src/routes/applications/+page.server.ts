import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { Application } from '$lib/types';

export const load: PageServerLoad = async ({ depends }) => {
	depends('app:live'); // reloaded when background work finishes
	return { apps: await json<Application[]>('/api/applications') };
};
