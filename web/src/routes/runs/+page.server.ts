import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { Run } from '$lib/types';

export const load: PageServerLoad = async () => ({ runs: await json<Run[]>('/api/runs') });
