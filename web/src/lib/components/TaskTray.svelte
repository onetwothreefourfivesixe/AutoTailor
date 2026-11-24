<script lang="ts">
	import { onMount } from 'svelte';
	import { live } from '$lib/client/live.svelte';
	import { send } from '$lib/client/api';
	import { taskLabel, when } from '$lib/format';
	import type { Task } from '$lib/types';

	let open = $state(false);
	const recent = $derived(live.tasks.slice(0, 6));
	const busy = $derived(live.active.length);
	const unseenFailed = $derived(live.unseen.filter((t) => t.status === 'failed').length);

	// A clock for "no output for N min" on running tasks.
	let now = $state(Date.now());
	onMount(() => {
		const timer = setInterval(() => (now = Date.now()), 15_000);
		return () => clearInterval(timer);
	});
	const QUIET_MIN = 3; // minutes without output before a running task is flagged
	function quietFor(t: Task): number {
		const last = t.updated_at ?? t.started_at;
		return last ? Math.floor((now - new Date(last).getTime()) / 60_000) : 0;
	}

	function toggle() {
		open = !open;
		if (open) live.seen();
	}
</script>

<div class="fixed right-4 bottom-4 z-40 w-[min(24rem,calc(100vw-2rem))]">
	{#if open}
		<div class="card mb-2 max-h-[60vh] overflow-y-auto shadow-xl" aria-label="Background tasks">
			<div class="flex items-center justify-between border-b border-line px-3 py-2">
				<span class="text-sm font-semibold">Background tasks</span>
				<a href="/activity" class="text-xs text-accent hover:underline">All activity</a>
			</div>
			{#if recent.length === 0}
				<p class="p-3 text-sm text-muted">Nothing has run yet.</p>
			{/if}
			{#each recent as t (t.id)}
				{@const quiet = t.status === 'running' ? quietFor(t) : 0}
				<div class="border-b border-line px-3 py-2 last:border-0">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<a class="block truncate text-sm font-medium hover:underline" href="/activity#task-{t.id}" title={taskLabel(t.kind, t.args_json)}>{taskLabel(t.kind, t.args_json)}</a>
							<div class="text-xs {t.status === 'failed' ? 'text-danger' : t.status === 'done' ? 'text-good' : 'text-muted'}">
								{t.status === 'done' ? '✓ done' : t.status === 'failed' ? '✕ failed' : t.status} · {when(t.finished_at ?? t.started_at ?? t.created_at)}
							</div>
						</div>
						{#if t.status === 'running' || t.status === 'queued'}
							<button class="btn btn-sm" onclick={() => send('POST', `/tasks/${t.id}/cancel`)}>{t.status === 'running' ? 'Stop' : 'Cancel'}</button>
						{/if}
					</div>
					{#if t.status === 'running'}
						{#if t.total}
							<div class="mt-1.5 h-1 overflow-hidden rounded bg-sunken">
								<div class="h-full bg-accent transition-all" style="width: {Math.min(100, ((t.done ?? 0) / t.total) * 100)}%"></div>
							</div>
						{:else}
							<div class="mt-1.5 h-1 overflow-hidden rounded bg-sunken"><div class="h-full w-1/3 animate-pulse bg-accent"></div></div>
						{/if}
						{#if t.message}<div class="mt-1 text-xs text-muted">{t.message}</div>{/if}
						{#if quiet >= QUIET_MIN}
							<div class="mt-1 text-xs text-warn">No output for {quiet} min. It may be waiting on a model, or stuck; the log shows the last thing it did.</div>
						{/if}
					{:else if t.status === 'failed' && t.message}
						<div class="mt-1 text-xs break-words text-danger">{t.message}</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
	<div class="flex justify-end">
		<button class="btn shadow-md" onclick={toggle} aria-expanded={open}>
			{#if busy}
				<span class="h-2 w-2 animate-pulse rounded-full bg-accent"></span> {busy} running
			{:else if live.unseen.length}
				<span class="h-2 w-2 rounded-full {unseenFailed ? 'bg-danger' : 'bg-good'}"></span>
				{unseenFailed ? `${unseenFailed} failed` : `${live.unseen.length} finished`}
			{:else}
				<span class="h-2 w-2 rounded-full {live.connected ? 'bg-good' : 'bg-off'}"></span> Tasks
			{/if}
		</button>
	</div>
</div>
