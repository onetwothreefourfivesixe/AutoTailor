import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { JobRow, Need, Overview, Run, StepModel } from '$lib/types';

export const load: PageServerLoad = async () => {
	const setup = await json<{ complete: boolean; dismissed: boolean; checks: Record<string, boolean> }>('/api/setup/status');
	if (!setup.complete && !setup.dismissed) redirect(303, '/setup');
	const [overview, needs, models, runs, best] = await Promise.all([
		json<Overview>('/api/overview'),
		json<Need[]>('/api/needs'),
		json<{ mode: string; steps: StepModel[] }>('/api/models'),
		json<Run[]>('/api/runs'),
		json<JobRow[]>('/api/jobs?verdict=eligible&applied=false&limit=6')
	]);
	return { overview, needs, models, runs: runs.slice(0, 4), best, setup };
};
