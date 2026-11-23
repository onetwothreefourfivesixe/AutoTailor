import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { JobRow } from '$lib/types';

interface Role {
	job_id: string;
	posting_id: string | null;
	company: string;
	title: string;
	location: string | null;
	match_score: number | null;
	status: 'open' | 'resolved' | 'skipped';
	seen_at: string;
}

export const load: PageServerLoad = async () => {
	const [roles, runwayJobs] = await Promise.all([json<Role[]>('/api/runway'), json<JobRow[]>('/api/jobs?source=runway&limit=500')]);
	const needsLink = new Set(runwayJobs.filter((j) => j.status === 'needs_link').map((j) => j.id));
	return { roles, needsLink: [...needsLink] };
};
