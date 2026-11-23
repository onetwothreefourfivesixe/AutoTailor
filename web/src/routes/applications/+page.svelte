<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { send, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { date, when } from '$lib/format';
	import { APP_LABELS, APP_STATUSES, type AppStatus, type Application } from '$lib/types';

	let { data } = $props();
	const STALE_DAYS = 21;
	let dragging = $state<string | null>(null);
	let over = $state<AppStatus | null>(null);
	const columns = $derived(APP_STATUSES.map((s) => ({ status: s, items: data.apps.filter((a) => a.status === s) })));
	const stale = (a: Application) => a.status === 'applied' && (Date.now() - new Date(a.applied_at).getTime()) / 86400000 > STALE_DAYS;

	async function move(pid: string, status: AppStatus) {
		const app = data.apps.find((a) => a.posting_id === pid);
		if (!app || app.status === status) return;
		try {
			await send('PUT', `/applications/${encodeURIComponent(pid)}`, { status });
			toasts.show(`${app.company}: ${APP_LABELS[status].toLowerCase()}.`);
			invalidateAll();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'That failed.', 'error');
		}
	}

	async function saveNotes(a: Application, notes: string) {
		if (notes === a.notes) return;
		await send('PUT', `/applications/${encodeURIComponent(a.posting_id)}`, { status: a.status, notes });
		invalidateAll();
	}
</script>

<svelte:head><title>Applications · AutoTailor</title></svelte:head>

<h1 class="h1 mb-1">Applications</h1>
<p class="mb-6 text-sm text-muted">What you've sent and where it stands. AutoTailor never applies for you: mark a job applied from its page. Drag cards between columns, or use the menu on a card.</p>

{#if data.apps.length === 0}
	<p class="card p-5 text-sm text-muted">Nothing yet. On a job's page, choose “Applied” once you've sent it.</p>
{:else}
	<div class="grid gap-3 overflow-x-auto pb-2 md:grid-cols-3 xl:grid-cols-6">
		{#each columns as col (col.status)}
			<section
				aria-label={APP_LABELS[col.status]}
				class="min-h-40 rounded-lg border p-2 {over === col.status ? 'border-accent bg-sunken' : 'border-line bg-surface/50'}"
				ondragover={(e) => {
					e.preventDefault();
					over = col.status;
				}}
				ondragleave={() => (over = null)}
				ondrop={(e) => {
					e.preventDefault();
					over = null;
					if (dragging) move(dragging, col.status);
					dragging = null;
				}}
			>
				<h2 class="h2 mb-2 px-1">{APP_LABELS[col.status]} <span class="font-normal">{col.items.length}</span></h2>
				<div class="space-y-2">
					{#each col.items as a (a.posting_id)}
						<article draggable="true" ondragstart={() => (dragging = a.posting_id)} class="card cursor-grab p-3 text-sm active:cursor-grabbing">
							<a href="/jobs/{encodeURIComponent(a.posting_id)}" class="font-medium hover:underline">{a.company}</a>
							<div class="text-xs text-muted">{a.title}</div>
							<div class="mt-1 text-xs text-muted">Applied {date(a.applied_at)}{a.events.length > 1 ? ` · updated ${when(a.updated_at)}` : ''}</div>
							{#if stale(a)}<div class="mt-1 text-xs text-warn">No reply for {STALE_DAYS}+ days</div>{/if}
							<select class="input mt-2 py-1 text-xs" aria-label="Status" value={a.status} onchange={(e) => move(a.posting_id, e.currentTarget.value as AppStatus)}>
								{#each APP_STATUSES as s (s)}<option value={s}>{APP_LABELS[s]}</option>{/each}
							</select>
							<details class="mt-2">
								<summary class="cursor-pointer text-xs text-muted">Notes{a.notes ? ' ·' : ''}</summary>
								<textarea class="input mt-1 h-20 text-xs" value={a.notes} onblur={(e) => saveNotes(a, e.currentTarget.value)} aria-label="Notes"></textarea>
								<ol class="mt-1 space-y-0.5 text-xs text-muted">{#each a.events as ev, i (i)}<li>{date(ev.at)}: {APP_LABELS[ev.status as AppStatus] ?? ev.status}</li>{/each}</ol>
							</details>
						</article>
					{/each}
				</div>
			</section>
		{/each}
	</div>
{/if}
