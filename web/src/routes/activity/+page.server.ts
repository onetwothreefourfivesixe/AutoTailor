import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { AiCall, Task } from '$lib/types';

export const load: PageServerLoad = async ({ url }) => {
	const step = url.searchParams.get('step');
	const [tasks, calls] = await Promise.all([
		json<Task[]>('/api/tasks?limit=50'),
		json<AiCall[]>(`/api/ai-calls?limit=200${step ? `&step=${encodeURIComponent(step)}` : ''}`)
	]);
	return { tasks, calls, step };
};
