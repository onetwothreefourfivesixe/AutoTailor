import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { json } from '$lib/server/api';
import type { StepModel } from '$lib/types';

const SECTIONS = ['profile', 'eligibility', 'runs', 'sources', 'mail', 'models', 'templates', 'log', 'api'];

export const load: PageServerLoad = async ({ params }) => {
	if (!SECTIONS.includes(params.section)) error(404, 'No such settings section.');
	const settings = await json<Record<string, any>>('/api/settings');
	type Models = { mode: string; steps: StepModel[]; pulled: { name: string; size: number; parameters: string | null }[] | null };
	type Templates = { pdf: { name: string; label: string; builtin: boolean }[]; word: { name: string; label: string }[] };
	const models = params.section === 'models' ? await json<Models>('/api/models') : null;
	const comparisons = params.section === 'models' ? await json<{ id: string; step: string }[]>('/api/comparisons') : [];
	const templates = params.section === 'templates' ? await json<Templates>('/api/templates') : null;
	const keys = params.section === 'sources' ? (await json<{ keys: Record<string, boolean> }>('/api/setup/status')).keys : {};
	return { section: params.section, settings, models, comparisons, templates, keys };
};
