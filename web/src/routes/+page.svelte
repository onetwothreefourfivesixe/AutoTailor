<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { send, command, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { when } from '$lib/format';
	import FitBar from '$lib/components/FitBar.svelte';
	import ModelTag from '$lib/components/ModelTag.svelte';

	let { data } = $props();
	const o = $derived(data.overview);
	const NEEDS_SHOWN = 5;
	let allNeeds = $state(false);
	const needs = $derived(allNeeds ? data.needs : data.needs.slice(0, NEEDS_SHOWN));

	async function run(cmd: string, label: string) {
		try {
			await command(cmd);
			toasts.show(`Started: ${label}. Follow it in the task tray.`);
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'Could not start it.', 'error');
		}
	}

	async function override(ref: string, verdict: 'eligible' | 'ineligible') {
		await send('PUT', `/postings/${encodeURIComponent(ref)}/override`, { verdict, note: 'confirmed from Today' });
		invalidateAll();
	}

	const needLink = (n: { kind: string; ref: string; posting_id: string | null }) =>
		n.kind === 'runway_link' ? '/runway'
		: n.kind === 'interrupted_run' ? `/runs/${n.ref}`
		: n.posting_id ? `/jobs/${encodeURIComponent(n.posting_id)}${n.kind.startsWith('answer') ? '?tab=answers' : ''}`
		: `/jobs/${encodeURIComponent(n.ref)}`;
</script>

<svelte:head><title>Today · AutoTailor</title></svelte:head>

{#if !data.setup.complete}
	<a href="/setup" class="card mb-4 flex items-center justify-between gap-3 border-accent p-3 text-sm hover:bg-sunken">
		<span>Setup isn't finished: {Object.entries(data.setup.checks).filter(([, ok]) => !ok).map(([k]) => ({ settings: 'settings', cv: 'your CV', profile: 'your education', models: 'an API key', sources: 'job boards' } as Record<string, string>)[k]).join(', ')} still to do.</span>
		<span class="font-medium text-accent">Continue setup →</span>
	</a>
{/if}
<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
	<h1 class="h1">Today</h1>
	<div class="flex gap-2">
		<button class="btn" onclick={() => run('tailor', 'tailoring the shortlist')}>Tailor shortlist</button>
		<button class="btn btn-primary" onclick={() => run('run', 'the whole pipeline')}>Run pipeline</button>
	</div>
</div>

<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
	<div class="min-w-0 space-y-6">
		<section>
			<h2 class="h2 mb-2">Needs you {data.needs.length ? `(${data.needs.length})` : ''}</h2>
			{#if data.needs.length === 0}
				<div class="card p-4 text-sm text-muted">Nothing is waiting on you.</div>
			{:else}
				<ul class="card divide-y divide-line">
					{#each needs as n (n.kind + n.ref)}
						<li class="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
							<div class="min-w-0">
								<div class="text-xs text-muted">{({ runway_link: 'Runway', answer_choice: 'Short answer', answer_problems: 'Short answer', unclear: 'Unclear', resume_pages: 'Resume', interrupted_run: 'Run' } as Record<string, string>)[n.kind] ?? n.kind}</div>
								<div class="font-medium break-words">{n.title}</div>
								<div class="text-sm text-muted">{n.detail}</div>
							</div>
							<div class="flex shrink-0 gap-2">
								{#if n.kind === 'unclear'}
									<button class="btn btn-sm" onclick={() => override(n.ref, 'eligible')}>Eligible</button>
									<button class="btn btn-sm" onclick={() => override(n.ref, 'ineligible')}>Ineligible</button>
								{:else if n.kind === 'interrupted_run'}
									<button class="btn btn-sm" onclick={() => run(`resume ${n.ref}`, `resuming run ${n.ref}`)}>Resume</button>
								{/if}
								<a class="btn btn-sm" href={needLink(n)}>Open</a>
							</div>
						</li>
					{/each}
				</ul>
				{#if data.needs.length > NEEDS_SHOWN}
					<button class="mt-2 text-sm text-accent hover:underline" onclick={() => (allNeeds = !allNeeds)}>{allNeeds ? 'Show fewer' : `Show all ${data.needs.length}`}</button>
				{/if}
			{/if}
		</section>

		<section>
			<div class="mb-2 flex items-center justify-between">
				<h2 class="h2">Best eligible matches</h2>
				<a href="/jobs?verdict=eligible" class="text-sm text-accent hover:underline">All jobs</a>
			</div>
			{#if data.best.length === 0}
				<div class="card p-4 text-sm text-muted">No eligible postings yet. Fetch job boards, then screen and rank.</div>
			{:else}
				<ul class="card divide-y divide-line">
					{#each data.best as j (j.id)}
						<li>
							<a href="/jobs/{encodeURIComponent(j.id)}" class="flex items-center justify-between gap-3 p-3 hover:bg-sunken">
								<div class="min-w-0">
									<div class="truncate font-medium">{j.title}</div>
									<div class="truncate text-sm text-muted">{j.company}{j.location ? ` · ${j.location}` : ''}</div>
								</div>
								<div class="flex shrink-0 items-center gap-3">
									{#if j.tailored}<span class="text-xs text-good">resume ready</span>{/if}
									<FitBar value={j.fit} />
								</div>
							</a>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	</div>

	<aside class="min-w-0 space-y-6">
		<section class="card p-4">
			<h2 class="h2 mb-3">Pipeline</h2>
			<dl class="grid grid-cols-2 gap-y-1.5 text-sm">
				<dt class="text-muted">Postings</dt><dd class="text-right tabular-nums">{o.counts.postings}</dd>
				<dt class="text-muted">Eligible</dt><dd class="text-right tabular-nums">{o.counts.eligible}</dd>
				<dt class="text-muted">Unclear</dt><dd class="text-right tabular-nums">{o.counts.unclear}</dd>
				<dt class="text-muted">Not screened</dt><dd class="text-right tabular-nums">{o.counts.not_screened}</dd>
				<dt class="text-muted">Shortlist</dt><dd class="text-right tabular-nums">{o.shortlist.length}</dd>
			</dl>
		</section>

		<section class="card p-4">
			<div class="mb-3 flex items-center justify-between">
				<h2 class="h2">Models</h2>
				<a href="/settings/models" class="text-xs text-accent hover:underline">Change</a>
			</div>
			{#await data.models}
				<p class="text-sm text-muted">Checking models…</p>
			{:then models}
				<ul class="space-y-2 text-sm">
					{#each models.steps as s (s.step)}
						<li class="flex items-center justify-between gap-2">
							<span class="w-20 shrink-0 text-muted">{s.step}</span>
							<span class="min-w-0 flex-1 truncate font-mono text-xs" title={s.model}>{s.model}</span>
							{#if s.pulled === false}
								<span class="rounded bg-danger-bg px-1.5 py-0.5 text-xs text-danger" title="ollama pull {s.model}">not pulled</span>
							{:else}
								<ModelTag local={s.local} name={s.provider} />
							{/if}
						</li>
					{/each}
				</ul>
			{:catch}
				<p class="text-sm text-muted">Couldn't check the models.</p>
			{/await}
		</section>

		<section class="card p-4">
			<div class="mb-3 flex items-center justify-between">
				<h2 class="h2">Recent runs</h2>
				<a href="/runs" class="text-xs text-accent hover:underline">All runs</a>
			</div>
			{#if data.runs.length === 0}
				<p class="text-sm text-muted">No runs yet.</p>
			{/if}
			<ul class="space-y-1.5 text-sm">
				{#each data.runs as r (r.id)}
					<li><a class="flex justify-between hover:underline" href="/runs/{r.id}"><span>Run {r.id} · {r.counts.tailored ?? 0} tailored</span><span class="text-muted">{r.status === 'done' ? when(r.started_at) : r.status}</span></a></li>
				{/each}
			</ul>
		</section>
		{#if !o.worker_running}
			<p class="text-xs text-muted">The background worker starts when you run something.</p>
		{/if}
	</aside>
</div>
