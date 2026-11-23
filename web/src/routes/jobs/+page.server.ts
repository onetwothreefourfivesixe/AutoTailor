import type { PageServerLoad } from './$types';
import { api } from '$lib/server/api';
import type { JobRow } from '$lib/types';

const PAGE_SIZE = 100;

export const load: PageServerLoad = async ({ url }) => {
	const p = new URLSearchParams();
	for (const k of ['verdict', 'q', 'source', 'min_fit', 'has_resume', 'applied', 'hidden', 'sort']) {
		const v = url.searchParams.get(k);
		if (v) p.set(k, v);
	}
	const page = Math.max(0, Number(url.searchParams.get('page') ?? 0));
	p.set('offset', String(page * PAGE_SIZE));
	p.set('limit', String(PAGE_SIZE));
	const res = await api(`/api/jobs?${p}`);
	const rows = (await res.json()) as JobRow[];
	return { rows, total: Number(res.headers.get('x-total-count') ?? rows.length), page, pageSize: PAGE_SIZE };
};
