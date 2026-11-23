import type { PageServerLoad } from './$types';
import { json, maybe } from '$lib/server/api';
import type { SetupStatus, StepModel } from '$lib/types';

export const load: PageServerLoad = async () => {
	const [status, settings, models, boards, cv] = await Promise.all([
		json<SetupStatus>('/api/setup/status'),
		json<Record<string, any>>('/api/settings'),
		json<{ mode: string; steps: StepModel[]; pulled: { name: string; size: number }[] | null }>('/api/models'),
		json<{ boards: { name: string; ats: string; slug: string; following: boolean }[] }>('/api/setup/boards'),
		maybe<Record<string, any>>('/api/cv')
	]);
	return { status, settings, models, boards: boards.boards, cv };
};
