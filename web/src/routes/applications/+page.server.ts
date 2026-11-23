import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { Application } from '$lib/types';

export const load: PageServerLoad = async () => ({ apps: await json<Application[]>('/api/applications') });
