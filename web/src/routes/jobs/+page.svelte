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
		{ label: 'Hidden', params: { hidden: 'true' } }
	];

	function setParams(changes: Record<string, string | null>, reset = false) {
		const p = reset ? new URLSearchParams() : new URLSearchParams(page.url.searchParams);
		for (const [k, v] of Object.entries(changes)) v ? p.set(k, v) : p.delete(k);
		if (!('page' in changes)) p.delete('page');
		selected = new Set();
		cursor = 0;
		goto(`/jobs?${p}`, { keepFocus: true, noScroll: true, replaceState: true });
	}

	const viewActive = (params: Record<string, string>) => {
		const keys = ['verdict', 'has_resume', 'applied', 'hidden'];
		return keys.every((k) => (page.url.searchParams.get(k) ?? '') === (params[k] ?? ''));
	};

	let timer: ReturnType<typeof setTimeout>;
	function onSearch() {
		clearTimeout(timer);
		timer = setTimeout(() => setParams({ q: search.trim() || null }), 250);
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
			toasts.show(r.task ? `Started: ${action === 'tailor' ? 'tailoring' : 'screening'} ${ids.length} job(s).` : `Updated ${r.updated} job(s).`);
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
		<kbd>j</kbd>/<kbd>k</kbd> move · <kbd>Enter</kbd> open · <kbd>x</kbd> select · <kbd>e</kbd>/<kbd>i</kbd> eligible/ineligible ·
		<kbd>t</kbd> tailor · <kbd>h</kbd> hide · <kbd>/</kbd> search
	</p>
</div>

<div class="mb-3 flex flex-wrap gap-1.5">
	{#each views as v (v.label)}
		<button class="btn btn-sm {viewActive(v.params) ? 'btn-primary' : ''}" onclick={() => setParams({ ...v.params, q: search.trim() || null, sort: page.url.searchParams.get('sort') }, true)}>{v.label}</button>
	{/each}
</div>

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
	<select class="input w-auto" aria-label="Sort" value={page.url.searchParams.get('sort') ?? 'fit'} onchange={(e) => setParams({ sort: e.currentTarget.value })}>
		<option value="fit">Best fit</option>
		<option value="posted">Newest</option>
		<option value="company">Company</option>
	</select>
</div>

{#if selected.size}
	<div class="sticky top-0 z-10 mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-accent bg-surface p-2 shadow-sm">
		<span class="px-1 text-sm font-medium">{selected.size} selected</span>
		<button class="btn btn-sm btn-primary" onclick={() => bulk('tailor')}>Tailor</button>
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
				<th class="p-2 font-medium">Fit</th>
				<th class="p-2 font-medium">Verdict</th>
				<th class="p-2 font-medium">Role</th>
				<th class="hidden p-2 font-medium md:table-cell">Location</th>
				<th class="hidden p-2 font-medium lg:table-cell">Source</th>
				<th class="hidden p-2 font-medium lg:table-cell">Posted</th>
				<th class="p-2 font-medium">Progress</th>
			</tr>
		</thead>
		<tbody>
			{#each data.rows as r, i (r.id)}
				<tr
					data-row={i}
					class="border-b border-line last:border-0 {i === cursor ? 'bg-sunken' : 'hover:bg-sunken/60'} {selected.has(r.id) ? 'outline-1 -outline-offset-1 outline-accent' : ''}"
					onclick={() => (cursor = i)}
				>
					<td class="p-2"><input type="checkbox" aria-label="Select {r.title}" checked={selected.has(r.id)} onchange={() => toggle(r.id)} /></td>
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
