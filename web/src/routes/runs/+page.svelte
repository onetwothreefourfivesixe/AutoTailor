<script lang="ts">
	import { command } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { when } from '$lib/format';

	let { data } = $props();
	const tone: Record<string, string> = { done: 'text-good', running: 'text-accent', interrupted: 'text-warn', failed: 'text-danger' };
</script>

<svelte:head><title>Runs · AutoTailor</title></svelte:head>

<div class="mb-4 flex items-center justify-between"><h1 class="h1">Runs</h1>
	<button class="btn btn-primary" onclick={() => command('tailor').then(() => toasts.show('Tailoring the shortlist…'), (e) => toasts.show(e.message, 'error'))}>Tailor shortlist</button>
</div>
{#if data.runs.length === 0}
	<p class="card p-5 text-sm text-muted">No runs yet. Tailor the shortlist to start one.</p>
{:else}
	<div class="card overflow-x-auto">
		<table class="w-full text-sm">
			<thead class="border-b border-line text-left text-xs text-muted"><tr><th class="p-2">Run</th><th class="p-2">Started</th><th class="p-2">Status</th><th class="p-2">Tailored</th><th class="p-2">Not tailored</th><th class="hidden p-2 md:table-cell">Model</th></tr></thead>
			<tbody>
				{#each data.runs as r (r.id)}
					<tr class="border-b border-line last:border-0 hover:bg-sunken">
						<td class="p-2"><a class="font-medium hover:underline" href="/runs/{r.id}">Run {r.id}</a></td>
						<td class="p-2 text-muted">{when(r.started_at)}</td>
						<td class="p-2 {tone[r.status] ?? ''}">{r.status}</td>
						<td class="p-2 tabular-nums">{r.counts.tailored ?? '–'}</td>
						<td class="p-2 tabular-nums">{r.counts.failed ?? '–'}</td>
						<td class="hidden p-2 font-mono text-xs md:table-cell">{r.model}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
