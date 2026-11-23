<script lang="ts">
	import { command } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { date } from '$lib/format';

	let { data } = $props();
	const d = $derived(data.detail);
	// The report's "Not tailored" section: lines like "- **3. Company | Title**: why." with sub-bullets.
	const failed = $derived(d.report?.includes('## Not tailored') ? d.report.split('## Not tailored')[1].split('\n').filter((l) => l.trim().startsWith('-')).map((l) => ({ sub: l.startsWith('  '), text: l.replace(/^\s*-\s*/, '').replace(/\*\*/g, '') })) : []);
</script>

<svelte:head><title>Run {d.run.id} · AutoTailor</title></svelte:head>

<a href="/runs" class="text-sm text-muted hover:underline">← Runs</a>
<div class="mt-2 mb-6 flex flex-wrap items-center justify-between gap-3">
	<div>
		<h1 class="h1">Run {d.run.id}</h1>
		<p class="text-sm text-muted">{date(d.run.started_at)} · {d.run.status} · {d.counts.tailored ?? 0} tailored, {d.counts.failed ?? 0} not</p>
	</div>
	{#if d.run.status === 'interrupted' || d.run.status === 'running'}
		<button class="btn btn-primary" onclick={() => command(`resume ${d.run.id}`).then(() => toasts.show('Resuming…'), (e) => toasts.show(e.message, 'error'))}>Resume run</button>
	{/if}
</div>

<div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
	{#each d.outputs as o (o.posting_id)}
		<article class="card overflow-hidden">
			<a href="/jobs/{encodeURIComponent(o.posting_id)}?tab=resume" class="block bg-white">
				<img src="/api/postings/{encodeURIComponent(o.posting_id)}/resume.png" alt="Resume for {o.company}" loading="lazy" class="h-56 w-full object-cover object-top" />
			</a>
			<div class="p-3">
				<a href="/jobs/{encodeURIComponent(o.posting_id)}" class="font-medium hover:underline">{o.index}. {o.company}</a>
				<div class="truncate text-sm text-muted">{o.title}</div>
				<div class="mt-2 flex gap-2">
					{#if o.pdf}<a class="btn btn-sm" href="/api/files/{o.pdf}">PDF</a>{/if}
					{#if o.docx}<a class="btn btn-sm" href="/api/files/{o.docx}">Word</a>{/if}
				</div>
			</div>
		</article>
	{:else}
		<p class="text-sm text-muted">This run saved no resumes.</p>
	{/each}
</div>

{#if failed.length}
	<section class="card mt-6 p-4">
		<h2 class="h2 mb-2">Not tailored</h2>
		<ul class="space-y-1 text-sm">{#each failed as f, i (i)}<li class={f.sub ? 'pl-5 text-muted' : ''}>{f.sub ? '· ' : ''}{f.text}</li>{/each}</ul>
	</section>
{/if}
