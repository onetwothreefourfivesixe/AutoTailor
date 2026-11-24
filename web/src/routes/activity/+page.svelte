<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { get, send } from '$lib/client/api';
	import { taskLabel, when } from '$lib/format';
	import type { Task } from '$lib/types';
	import Tabs from '$lib/components/Tabs.svelte';

	let { data } = $props();
	let tab = $state('tasks');
	let logs = $state<Record<number, string>>({});
	let onlyFailed = $state(false);
	let onlyCloud = $state(false);
	const calls = $derived(data.calls.filter((c) => (!onlyFailed || c.error) && (!onlyCloud || !c.local)));

	async function output(t: Task) {
		if (logs[t.id] === undefined) logs[t.id] = (await get<Task>(`/tasks/${t.id}`)).log ?? '';
	}

	// /activity#task-<id> (from a notice or the task tray) opens that task's log.
	let opened = $state<number | null>(null);
	onMount(() => {
		const m = location.hash.match(/^#task-(\d+)$/);
		if (!m) return;
		opened = Number(m[1]);
		requestAnimationFrame(() => document.getElementById(`task-${opened}`)?.scrollIntoView({ block: 'start' }));
	});
</script>

<svelte:head><title>Activity · AutoTailor</title></svelte:head>

<h1 class="h1 mb-4">Activity</h1>
<Tabs tabs={[{ id: 'tasks', label: 'Tasks' }, { id: 'calls', label: 'Model calls' }]} bind:active={tab} />

<div class="mt-4">
	{#if tab === 'tasks'}
		<ul class="space-y-2">
			{#each data.tasks as t (t.id)}
				<li class="card scroll-mt-4" id="task-{t.id}">
					<details open={opened === t.id} ontoggle={(e) => e.currentTarget.open && output(t)}>
						<summary class="flex cursor-pointer flex-wrap items-center justify-between gap-2 p-3 text-sm">
							<span class="font-medium">{taskLabel(t.kind, t.args_json)}</span>
							<span class="flex items-center gap-3 text-xs text-muted">
								<span class={t.status === 'failed' ? 'text-danger' : t.status === 'done' ? 'text-good' : ''}>{t.status}</span>
								{when(t.created_at)}
								{#if t.status === 'running' || t.status === 'queued'}<button class="btn btn-sm" onclick={(e) => { e.preventDefault(); send('POST', `/tasks/${t.id}/cancel`); }}>Stop</button>{/if}
							</span>
						</summary>
						{#if t.status === 'failed' && t.message}<p class="border-t border-line px-3 py-2 text-xs text-danger">{t.message}</p>{/if}
						<pre class="max-h-96 overflow-auto border-t border-line bg-sunken p-3 text-xs whitespace-pre-wrap">{logs[t.id] ?? 'Loading…'}</pre>
					</details>
				</li>
			{:else}
				<li class="card p-5 text-sm text-muted">Nothing has run yet.</li>
			{/each}
		</ul>
	{:else}
		<div class="mb-3 flex flex-wrap items-center gap-3 text-sm">
			<select class="input w-auto" aria-label="Step" value={data.step ?? ''} onchange={(e) => goto(e.currentTarget.value ? `/activity?step=${e.currentTarget.value}` : '/activity', { keepFocus: true, noScroll: true })}>
				<option value="">All steps</option>
				{#each ['extract', 'score', 'tailor', 'humanize'] as s (s)}<option value={s}>{s}</option>{/each}
			</select>
			<label class="flex items-center gap-1.5"><input type="checkbox" bind:checked={onlyFailed} /> Failed only</label>
			<label class="flex items-center gap-1.5"><input type="checkbox" bind:checked={onlyCloud} /> Sent to the cloud only</label>
		</div>
		<ul class="space-y-2">
			{#each calls as c (c.id)}
				<li class="card">
					<details>
						<summary class="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
							{#if c.error}<span class="text-danger">failed</span>{/if}
							<span class="font-medium">{c.step}</span><span class="font-mono text-xs">{c.model}</span>
							<span class="text-xs {c.local ? 'text-muted' : 'text-cloud'}">{c.local ? 'on this machine' : `sent to ${c.provider}`}</span>
							{#if c.item}<a class="text-xs text-accent hover:underline" href={c.item.startsWith('question:') ? '#' : `/jobs/${encodeURIComponent(c.item)}?tab=log`}>{c.item}</a>{/if}
							<span class="text-xs text-muted">{(c.ms / 1000).toFixed(1)} s · {when(c.at)}</span>
						</summary>
						<div class="space-y-3 border-t border-line p-3 text-xs">
							{#if c.error}<p class="text-danger">{c.error}</p>{/if}
							{#each JSON.parse(c.messages ?? '[]') as msg, i (i)}<div><div class="mb-1 font-semibold">{msg.role}</div><pre class="max-h-72 overflow-auto rounded bg-sunken p-2 whitespace-pre-wrap">{msg.content}</pre></div>{/each}
							{#if c.response}<div><div class="mb-1 font-semibold">response</div><pre class="max-h-72 overflow-auto rounded bg-sunken p-2 whitespace-pre-wrap">{c.response}</pre></div>{/if}
						</div>
					</details>
				</li>
			{:else}
				<li class="card p-5 text-sm text-muted">No model calls logged.</li>
			{/each}
		</ul>
	{/if}
</div>
