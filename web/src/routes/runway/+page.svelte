<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { send, command, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { pct, when } from '$lib/format';

	let { data } = $props();
	const waiting = $derived(data.roles.filter((r) => r.status === 'open' && r.posting_id && data.needsLink.includes(r.posting_id)));
	let answers = $state<Record<string, { url: string; text: string }>>({});
	// Reading never creates an entry: the page reads these while it renders, and changing state
	// during rendering is an error in Svelte 5 (state_unsafe_mutation). Typing creates it.
	const EMPTY = { url: '', text: '' };
	const a = (id: string) => answers[id] ?? EMPTY;
	const setA = (id: string, key: 'url' | 'text', value: string) => (answers[id] = { ...a(id), [key]: value });

	async function start(cmd: string, label: string) {
		try {
			await command(cmd);
			toasts.show(`Started: ${label}.`);
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'Could not start it.', 'error');
		}
	}

	async function answer(pid: string, body: Record<string, unknown>) {
		try {
			await send('POST', `/runway/${encodeURIComponent(pid)}/answer`, body);
			toasts.show(body.skip ? 'Skipping it.' : 'Reading the posting… (see the task tray)');
			delete answers[pid];
			invalidateAll();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'That failed.', 'error');
		}
	}
	const tone: Record<string, string> = { open: 'text-warn', resolved: 'text-good', skipped: 'text-muted' };
</script>

<svelte:head><title>Runway · AutoTailor</title></svelte:head>

<div class="mb-2 flex flex-wrap items-center justify-between gap-3">
	<h1 class="h1">Runway</h1>
	<div class="flex gap-2">
		<button class="btn" onclick={() => start('mail import', 'importing emails from data/inbox')}>Import emails</button>
		<button class="btn btn-primary" onclick={() => start('runway resolve', 'matching roles on job boards')}>Find all on job boards</button>
	</div>
</div>
<p class="mb-6 text-sm text-muted">Roles from Runway's notification emails. AutoTailor never opens Runway links: it reads the email, then looks for each role on the employer's own job board. Drop a Runway email anywhere on the page to add it.</p>

{#if waiting.length}
	<h2 class="h2 mb-2">Waiting for a link ({waiting.length})</h2>
	<div class="mb-8 space-y-3">
		{#each waiting as r (r.job_id)}
			{@const pid = r.posting_id as string}
			<section class="card p-4">
				<div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
					<div><span class="font-medium">{r.company} · {r.title}</span> <span class="text-sm text-muted">{r.location ?? ''} · {pct(r.match_score)} match</span></div>
					<a class="text-sm text-accent hover:underline" href="https://app.joinrunway.io/job/{r.job_id}" target="_blank" rel="noreferrer">Open on Runway ↗</a>
				</div>
				<p class="mb-2 text-xs text-muted">Open it in your browser, follow it to the employer's posting, and paste that link (or the posting text).</p>
				<div class="flex flex-wrap gap-2">
					<input class="input min-w-0 flex-1" placeholder="Employer's posting URL" value={a(pid).url} oninput={(e) => setA(pid, 'url', e.currentTarget.value)} aria-label="Employer URL" />
					<button class="btn btn-primary" disabled={!a(pid).url.trim()} onclick={() => answer(pid, { url: a(pid).url.trim() })}>Use link</button>
					<button class="btn" onclick={() => answer(pid, { skip: true })}>Skip role</button>
				</div>
				<details class="mt-2">
					<summary class="cursor-pointer text-xs text-muted">…or paste the posting text</summary>
					<textarea class="input mt-2 h-28" value={a(pid).text} oninput={(e) => setA(pid, 'text', e.currentTarget.value)} aria-label="Posting text"></textarea>
					<button class="btn btn-sm mt-1" disabled={!a(pid).text.trim()} onclick={() => answer(pid, { text: a(pid).text })}>Use text</button>
				</details>
			</section>
		{/each}
	</div>
{/if}

<h2 class="h2 mb-2">All roles ({data.roles.length})</h2>
{#if data.roles.length === 0}
	<p class="card p-5 text-sm text-muted">No Runway roles yet. In Gmail open a Runway email, ⋮ › Download message, and drop the file here.</p>
{:else}
	<div class="card overflow-x-auto">
		<table class="w-full text-sm">
			<thead class="border-b border-line text-left text-xs text-muted"><tr><th class="p-2">Status</th><th class="p-2">Match</th><th class="p-2">Role</th><th class="hidden p-2 md:table-cell">Location</th><th class="p-2">Seen</th></tr></thead>
			<tbody>
				{#each data.roles as r (r.job_id)}
					<tr class="border-b border-line last:border-0">
						<td class="p-2 {tone[r.status]}">{r.status}</td>
						<td class="p-2 tabular-nums">{pct(r.match_score)}</td>
						<td class="p-2">{#if r.posting_id}<a class="hover:underline" href="/jobs/{encodeURIComponent(r.posting_id)}">{r.company} · {r.title}</a>{:else}{r.company} · {r.title}{/if}</td>
						<td class="hidden p-2 text-muted md:table-cell">{r.location ?? ''}</td>
						<td class="p-2 text-muted">{when(r.seen_at)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
