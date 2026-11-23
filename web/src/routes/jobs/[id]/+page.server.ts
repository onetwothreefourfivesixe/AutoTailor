import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { JobDetail } from '$lib/types';

export const load: PageServerLoad = async ({ params }) => {
	const job = await json<JobDetail>(`/api/jobs/${encodeURIComponent(params.id)}`);
	return { job };
};
