<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { send, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { date, pct } from '$lib/format';
	import { APP_LABELS } from '$lib/types';
	import FitBar from '$lib/components/FitBar.svelte';
	import VerdictPill from '$lib/components/VerdictPill.svelte';

	let { data } = $props();
	let cursor = $state(0);
	let selected = $state<Set<string>>(new Set());
	let search = $state(page.url.searchParams.get('q') ?? '');
	let table: HTMLTableElement | undefined = $state();

	const views: { label: string; params: Record<string, string> }[] = [
		{ label: 'All', params: {} },
		{ label: 'Eligible', params: { verdict: 'eligible' } },
		{ label: 'Eligible, no resume', params: { verdict: 'eligible', has_resume: 'false', applied: 'false' } },
		{ label: 'Resume ready', params: { has_resume: 'true', applied: 'false' } },
		{ label: 'Unclear', params: { verdict: 'unclear' } },
		{ label: 'Applied', params: { applied: 'true' } },
		{ label: 'Hidden', params: { hidden: 'true' } },
		{ label: 'Cut', params: { cut: 'true' } }
	];

	function setParams(changes: Record<string, string | null>, reset = false) {
		const p = reset ? new URLSearchParams() : new URLSearchParams(page.url.search);
		for (const [k, v] of Object.entries(changes)) v ? p.set(k, v) : p.delete(k);
		if (!('page' in changes)) p.delete('page');
		selected = new Set();
		cursor = 0;
		goto(`/jobs?${p}`, { reset: false, replace: true });
	}

	const viewActive = (params: Record<string, string>) => {
		const keys = ['verdict', 'has_resume', 'applied', 'hidden', 'cut'];
		return keys.every((k) => (page.url.searchParams.get(k) ?? '') === (params[k] ?? ''));
	};

	let timer: ReturnType<typeof setTimeout>;
	function onSearch() {
		clearTimeout(timer);
		timer = setTimeout(() => setParams({ q: search.trim() || null }), 250);
	}

	// --- sorting by column: click a heading to sort, again to reverse --------------------------------
	const DEFAULT_DIR: Record<string, 'asc' | 'desc'> = { fit: 'desc', verdict: 'asc', title: 'asc', company: 'asc', location: 'asc', source: 'asc', posted: 'desc', progress: 'desc' };
	const sortKey = $derived(page.url.searchParams.get('sort') ?? 'fit');
	const sortDir = $derived((page.url.searchParams.get('dir') as 'asc' | 'desc' | null) ?? DEFAULT_DIR[sortKey] ?? 'desc');
	function sortBy(key: string) {
		if (key === sortKey) setParams({ sort: key, dir: sortDir === 'asc' ? 'desc' : 'asc' });
		else setParams({ sort: key, dir: null }); // a new column starts in its usual direction
	}
	const ariaSort = (key: string) => (key === sortKey ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none');

	// --- what's filtered, in words, each removable -------------------------------------------------
	const filters = $derived.by(() => {
		const q = page.url.searchParams;
		const out: { label: string; clear: Record<string, null> }[] = [];
		const view = views.find((v) => v.label !== 'All' && viewActive(v.params));
		if (view) out.push({ label: view.label, clear: { verdict: null, has_resume: null, applied: null, hidden: null, cut: null } });
		if (q.get('q')) out.push({ label: `“${q.get('q')}”`, clear: { q: null } });
		if (q.get('source')) out.push({ label: `from ${q.get('source')}`, clear: { source: null } });
		if (q.get('min_fit')) out.push({ label: `fit ≥ ${q.get('min_fit')}`, clear: { min_fit: null } });
		return out;
	});
	function clearFilter(changes: Record<string, null>) {
		if ('q' in changes) search = '';
		setParams(changes);
	}

	// --- selecting: Shift-click a checkbox to select (or clear) the whole range since the last one --
	let lastClicked = -1;
	function check(e: MouseEvent, i: number, id: string) {
		if (e.shiftKey && lastClicked >= 0 && lastClicked !== i) {
			e.preventDefault(); // the range is set below; a plain click toggles the box as usual
			const on = !selected.has(id);
			const s = new Set(selected);
			for (let k = Math.min(lastClicked, i); k <= Math.max(lastClicked, i); k++) on ? s.add(data.rows[k].id) : s.delete(data.rows[k].id);
			selected = s;
		} else toggle(id);
		lastClicked = i;
	}

	function toggle(id: string) {
		const s = new Set(selected);
		s.has(id) ? s.delete(id) : s.add(id);
		selected = s;
	}

	async function bulk(action: string, ids = [...selected]) {
		if (!ids.length) return;
		try {
			const r = await send<{ task?: unknown; updated?: number }>('POST', '/jobs/bulk', { ids, action });
			const doing: Record<string, string> = { tailor: 'tailoring', score: 'fit-scoring', rescreen: 'screening' };
			toasts.show(r.task ? `Started: ${doing[action] ?? action} ${ids.length} job(s). Follow it in the task tray.` : `Updated ${r.updated} job(s).`);
			selected = new Set();
			invalidateAll();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'That failed.', 'error');
		}
	}

	function keydown(e: KeyboardEvent) {
		const el = e.target as HTMLElement;
		if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
		const row = data.rows[cursor];
		const keys: Record<string, () => void> = {
			j: () => (cursor = Math.min(data.rows.length - 1, cursor + 1)),
			k: () => (cursor = Math.max(0, cursor - 1)),
			x: () => row && toggle(row.id),
			Enter: () => row && goto(`/jobs/${encodeURIComponent(row.id)}`),
			e: () => row && bulk('eligible', selected.size ? [...selected] : [row.id]),
			i: () => row && bulk('ineligible', selected.size ? [...selected] : [row.id]),
			t: () => row && bulk('tailor', selected.size ? [...selected] : [row.id]),
			s: () => row && bulk('score', selected.size ? [...selected] : [row.id]),
			h: () => row && bulk(page.url.searchParams.get('hidden') ? 'unhide' : 'hide', selected.size ? [...selected] : [row.id]),
			'/': () => document.getElementById('job-search')?.focus()
		};
		const f = keys[e.key];
		if (f) {
			e.preventDefault();
			f();
			table?.querySelector<HTMLElement>(`[data-row="${cursor}"]`)?.scrollIntoView({ block: 'nearest' });
		}
	}

	const allSelected = $derived(data.rows.length > 0 && data.rows.every((r) => selected.has(r.id)));
	const pages = $derived(Math.ceil(data.total / data.pageSize));
</script>

<svelte:window onkeydown={keydown} />
<svelte:head><title>Jobs · AutoTailor</title></svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
	<h1 class="h1">Jobs <span class="text-base font-normal text-muted">{data.total}</span></h1>
	<p class="hidden text-xs text-muted lg:block">
		<kbd>j</kbd>/<kbd>k</kbd> move · <kbd>Enter</kbd> open · <kbd>x</kbd> select (Shift-click a box: a range) · <kbd>e</kbd>/<kbd>i</kbd> eligible/ineligible ·
		<kbd>s</kbd> score fit · <kbd>t</kbd> tailor · <kbd>h</kbd> hide · <kbd>/</kbd> search
	</p>
</div>

<div class="mb-3 flex flex-wrap gap-1.5">
	{#each views as v (v.label)}
		<button class="btn btn-sm {viewActive(v.params) ? 'btn-primary' : ''}" onclick={() => setParams({ ...v.params, q: search.trim() || null, sort: page.url.searchParams.get('sort'), dir: page.url.searchParams.get('dir'), source: page.url.searchParams.get('source'), min_fit: page.url.searchParams.get('min_fit') }, true)} title={v.label === 'Cut' ? 'Fit-scored below the cut-off (Settings › Runs and limits); never tailored' : undefined}>{v.label}{v.label === 'Cut' && data.cutCount ? ` (${data.cutCount})` : ''}</button>
	{/each}
</div>
{#if page.url.searchParams.get('cut')}
	<p class="mb-3 text-xs text-muted">Postings cut for a fit score below your cut-off, or for being older than your date cutoff. Change both under <a class="text-accent hover:underline" href="/settings/runs">Settings › Runs and limits</a> and <a class="text-accent hover:underline" href="/settings/sources">Job sources</a>.</p>
	{#if data.deleted.length}
		<details class="card mb-3 p-3 text-sm" open={!data.rows.length}>
			<summary class="cursor-pointer font-medium">Deleted ({data.deleted.length}): only a small record is kept, so they aren't fetched again</summary>
			<ul class="mt-2 max-h-96 divide-y divide-line overflow-y-auto">
				{#each data.deleted as d (d.id)}
					<li class="flex items-center gap-3 py-1.5">
						<span class="min-w-0 flex-1 truncate"><b>{d.company}</b> · {d.title}{d.location ? ` · ${d.location}` : ''}</span>
						<span class="text-xs whitespace-nowrap text-muted">{d.reason === 'low_fit' ? `fit ${d.fit_score?.toFixed(2)}` : 'too old'} · {date(d.cut_at)}</span>
						<button class="btn btn-sm" title="The next fetch, or adding its link, stores it again" onclick={async () => { try { await send('DELETE', `/cut/${encodeURIComponent(d.id)}`); toasts.show('Allowed again: the next fetch can bring it back.'); await invalidateAll(); } catch (e) { toasts.show(e instanceof ApiError ? e.message : 'That failed.', 'error'); } }}>Allow again</button>
					</li>
				{/each}
			</ul>
		</details>
	{/if}
{/if}

<div class="mb-3 flex flex-wrap items-center gap-2">
	<input id="job-search" class="input max-w-xs" placeholder="Search company, role, place" bind:value={search} oninput={onSearch} aria-label="Search jobs" />
	<select class="input w-auto" aria-label="Source" value={page.url.searchParams.get('source') ?? ''} onchange={(e) => setParams({ source: e.currentTarget.value || null })}>
		<option value="">All sources</option>
		{#each ['greenhouse', 'lever', 'ashby', 'paste', 'runway'] as s (s)}<option value={s}>{s}</option>{/each}
	</select>
	<select class="input w-auto" aria-label="Minimum fit" value={page.url.searchParams.get('min_fit') ?? ''} onchange={(e) => setParams({ min_fit: e.currentTarget.value || null })}>
		<option value="">Any fit</option>
		{#each ['0.5', '0.6', '0.7', '0.8'] as f (f)}<option value={f}>Fit ≥ {f}</option>{/each}
	</select>
	<select class="input w-auto lg:hidden" aria-label="Sort" value={sortKey} onchange={(e) => setParams({ sort: e.currentTarget.value, dir: null })}>
		<option value="fit">Best fit</option>
		<option value="posted">Newest</option>
		<option value="company">Company</option>
		<option value="title">Role</option>
		<option value="verdict">Verdict</option>
		<option value="location">Location</option>
		<option value="progress">Progress</option>
	</select>
</div>
{#if filters.length}
	<div class="mb-3 flex flex-wrap items-center gap-1.5 text-sm" aria-label="Active filters">
		<span class="text-muted">{data.total} job{data.total === 1 ? '' : 's'}:</span>
		{#each filters as f (f.label)}
			<button class="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 hover:bg-sunken" onclick={() => clearFilter(f.clear)} aria-label="Remove filter {f.label}">{f.label} <span class="text-muted" aria-hidden="true">✕</span></button>
		{/each}
		<button class="text-accent hover:underline" onclick={() => { search = ''; setParams({ sort: page.url.searchParams.get('sort'), dir: page.url.searchParams.get('dir') }, true); }}>Clear all</button>
	</div>
{/if}

{#if selected.size}
	<div class="sticky top-0 z-10 mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-accent bg-surface p-2 shadow-sm">
		<span class="px-1 text-sm font-medium">{selected.size} selected</span>
		<button class="btn btn-sm btn-primary" onclick={() => bulk('tailor')}>Tailor</button>
		<button class="btn btn-sm" onclick={() => bulk('score')} title="Fit-score these jobs with the score step's model, even if scored before">Score fit</button>
		<button class="btn btn-sm" onclick={() => bulk('rescreen')}>Screen again</button>
		<button class="btn btn-sm" onclick={() => bulk('eligible')}>Mark eligible</button>
		<button class="btn btn-sm" onclick={() => bulk('ineligible')}>Mark ineligible</button>
		<button class="btn btn-sm" onclick={() => bulk('clear_override')}>Clear override</button>
		<button class="btn btn-sm" onclick={() => bulk(page.url.searchParams.get('hidden') ? 'unhide' : 'hide')}>{page.url.searchParams.get('hidden') ? 'Unhide' : 'Hide'}</button>
		<button class="btn btn-sm ml-auto" onclick={() => (selected = new Set())}>Clear</button>
	</div>
{/if}

<div class="card overflow-x-auto">
	<table bind:this={table} class="w-full text-sm">
		<thead class="border-b border-line text-left text-xs text-muted">
			<tr>
				<th class="w-8 p-2"><input type="checkbox" aria-label="Select all" checked={allSelected} onchange={() => (selected = allSelected ? new Set() : new Set(data.rows.map((r) => r.id)))} /></th>
				{#snippet heading(key: string, label: string, cls = '')}
					<th class="p-2 font-medium {cls}" aria-sort={ariaSort(key)}>
						<button class="inline-flex items-center gap-1 hover:text-fg {key === sortKey ? 'text-fg' : ''}" onclick={() => sortBy(key)} title="Sort by {label.toLowerCase()}{key === sortKey ? ` (${sortDir === 'asc' ? 'ascending' : 'descending'}; click to reverse)` : ''}">
							{label}{#if key === sortKey}<span class="text-[10px]" aria-hidden="true">{sortDir === 'asc' ? '▲' : '▼'}</span>{/if}
						</button>
					</th>
				{/snippet}
				{@render heading('fit', 'Fit')}
				{@render heading('verdict', 'Verdict')}
				<th class="p-2 font-medium" aria-sort={sortKey === 'title' || sortKey === 'company' ? ariaSort(sortKey) : 'none'}>
					<span class="inline-flex items-center gap-1">
						{#each [['title', 'Role'], ['company', 'Company']] as [key, label], n (key)}
							{#if n}<span class="text-muted">·</span>{/if}
							<button class="inline-flex items-center gap-1 hover:text-fg {key === sortKey ? 'text-fg' : ''}" onclick={() => sortBy(key)} title="Sort by {label.toLowerCase()}">{label}{#if key === sortKey}<span class="text-[10px]" aria-hidden="true">{sortDir === 'asc' ? '▲' : '▼'}</span>{/if}</button>
						{/each}
					</span>
				</th>
				{@render heading('location', 'Location', 'hidden md:table-cell')}
				{@render heading('source', 'Source', 'hidden lg:table-cell')}
				{@render heading('posted', 'Posted', 'hidden lg:table-cell')}
				{@render heading('progress', 'Progress')}
			</tr>
		</thead>
		<tbody>
			{#each data.rows as r, i (r.id)}
				<tr
					data-row={i}
					class="border-b border-line last:border-0 {i === cursor ? 'bg-sunken' : 'hover:bg-sunken/60'} {selected.has(r.id) ? 'outline-1 -outline-offset-1 outline-accent' : ''}"
					onclick={() => (cursor = i)}
				>
					<td class="p-2"><input type="checkbox" aria-label="Select {r.title}" checked={selected.has(r.id)} onclick={(e) => check(e, i, r.id)} /></td>
					<td class="p-2"><FitBar value={r.fit} /></td>
					<td class="p-2"><VerdictPill verdict={r.verdict} overridden={r.overridden} /></td>
					<td class="max-w-[28rem] p-2">
						<a href="/jobs/{encodeURIComponent(r.id)}" class="block truncate font-medium hover:underline">{r.title}</a>
						<div class="truncate text-xs text-muted">{r.company}</div>
					</td>
					<td class="hidden max-w-[12rem] truncate p-2 text-muted md:table-cell">{r.location ?? ''}</td>
					<td class="hidden p-2 text-muted lg:table-cell">{r.source}{r.runway_score != null ? ` · ${pct(r.runway_score)}` : ''}</td>
					<td class="hidden p-2 whitespace-nowrap text-muted lg:table-cell">{date(r.posted_at)}</td>
					<td class="p-2 text-xs whitespace-nowrap">
						{#if r.application}<span class="text-accent">{APP_LABELS[r.application]}</span>
						{:else if r.tailored}<span class="text-good">resume ready</span>
						{:else if r.status === 'needs_link'}<span class="text-warn">needs link</span>
						{:else}<span class="text-muted">new</span>{/if}
					</td>
				</tr>
			{:else}
				<tr><td colspan="8" class="p-6 text-center text-muted">No jobs match. Change the filters, or add jobs with <kbd>A</kbd>.</td></tr>
			{/each}
		</tbody>
	</table>
</div>

{#if pages > 1}
	<div class="mt-3 flex items-center justify-center gap-2">
		<button class="btn btn-sm" disabled={data.page === 0} onclick={() => setParams({ page: String(data.page - 1) })}>Previous</button>
		<span class="text-sm text-muted">Page {data.page + 1} of {pages}</span>
		<button class="btn btn-sm" disabled={data.page + 1 >= pages} onclick={() => setParams({ page: String(data.page + 1) })}>Next</button>
	</div>
{/if}
