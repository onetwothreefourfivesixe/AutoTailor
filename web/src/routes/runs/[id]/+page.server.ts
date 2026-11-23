import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';

interface RunDetail {
	run: { id: number; started_at: string; finished_at: string | null; status: string };
	counts: Record<string, number>;
	report: string | null;
	outputs: { index: number; posting_id: string; company: string; title: string; pdf: string | null; docx: string | null; model: string | null }[];
}

export const load: PageServerLoad = async ({ params }) => ({ detail: await json<RunDetail>(`/api/runs/${encodeURIComponent(params.id)}`) });
