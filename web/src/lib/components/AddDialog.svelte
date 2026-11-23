<script lang="ts">
	import Modal from './Modal.svelte';
	import { send, upload, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { invalidateAll } from '$app/navigation';

	let { open = $bindable(false), files = $bindable<File[]>([]) }: { open?: boolean; files?: File[] } = $props();

	interface Candidate {
		index: number;
		kind: 'posting' | 'email' | 'error';
		status: string;
		source: string;
		error: string | null;
		company: string | null;
		title: string | null;
		location: string | null;
		roles: { title: string; company: string; match_score: number | null }[];
		keep: boolean;
	}

	let tab = $state<'links' | 'text' | 'files'>('links');
	let urls = $state('');
	let text = $state('');
	let reading = $state(false);
	let saving = $state(false);
	let batch = $state<string | null>(null);
	let candidates = $state<Candidate[]>([]);
	let dragging = $state(false);

	$effect(() => {
		if (files.length) tab = 'files';
	});

	async function read() {
		reading = true;
		const form = new FormData();
		if (tab === 'links') form.set('urls', urls);
		if (tab === 'text') form.set('text', text);
		if (tab === 'files') for (const f of files) form.append('files', f);
		try {
			const r = await upload<{ batch: string; candidates: Omit<Candidate, 'keep'>[] }>('/intake/preview', form);
			batch = r.batch;
			candidates = r.candidates.map((c) => ({ ...c, keep: c.kind === 'posting' || (c.kind === 'email' && c.roles.length > 0) }));
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'Reading failed.', 'error');
		} finally {
			reading = false;
		}
	}

	async function save() {
		if (!batch) return;
		saving = true;
		const edits: Record<number, Record<string, string>> = {};
		for (const c of candidates) if (c.kind === 'posting') edits[c.index] = { company: c.company ?? '', title: c.title ?? '', location: c.location ?? '' };
		try {
			const r = await send<{ new: string[]; merged: number; emails: number; roles: number }>('POST', '/intake/save', {
				batch,
				keep: candidates.filter((c) => c.keep).map((c) => c.index),
				edits
			});
			toasts.show(`Saved ${r.new.length} new${r.merged ? `, ${r.merged} merged into stored postings` : ''}${r.emails ? `, ${r.roles} Runway roles` : ''}.`);
			const postings = r.new.filter((id) => !id.startsWith('runway:'));
			if (postings.length) await send('POST', '/tasks', { command: `screen ${postings.join(' ')}` });
			if (r.new.some((id) => id.startsWith('runway:'))) await send('POST', '/tasks', { kind: 'resolve' });
			reset();
			open = false;
			invalidateAll();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'Saving failed.', 'error');
		} finally {
			saving = false;
		}
	}

	function reset() {
		batch = null;
		candidates = [];
		urls = '';
		text = '';
		files = [];
	}

	function drop(e: DragEvent) {
		e.preventDefault();
		dragging = false;
		files = [...files, ...Array.from(e.dataTransfer?.files ?? [])];
	}
</script>

<Modal bind:open title="Add jobs and emails" wide>
	{#if !batch}
		<div class="mb-4 flex gap-2" role="tablist">
			{#each [['links', 'Links'], ['text', 'Text or email'], ['files', 'Files']] as [id, label] (id)}
				<button role="tab" aria-selected={tab === id} class="btn btn-sm {tab === id ? 'btn-primary' : ''}" onclick={() => (tab = id as typeof tab)}>{label}</button>
			{/each}
		</div>
		{#if tab === 'links'}
			<label class="label" for="add-urls">Job posting links, one per line</label>
			<textarea id="add-urls" class="input h-32 font-mono text-xs" bind:value={urls} placeholder="https://boards.greenhouse.io/acme/jobs/123"></textarea>
			<p class="mt-2 text-xs text-muted">LinkedIn, Handshake, Runway, Indeed and Glassdoor links aren't opened (their terms forbid it); paste the posting's text instead.</p>
		{:else if tab === 'text'}
			<label class="label" for="add-text">A posting's text, or a whole email (Gmail: ⋮ › Show original › copy)</label>
			<textarea id="add-text" class="input h-56" bind:value={text}></textarea>
		{:else}
			<div
				role="region"
				aria-label="Drop files here"
				class="flex h-40 flex-col items-center justify-center rounded-lg border-2 border-dashed {dragging ? 'border-accent bg-sunken' : 'border-line'}"
				ondragover={(e) => {
					e.preventDefault();
					dragging = true;
				}}
				ondragleave={() => (dragging = false)}
				ondrop={drop}
			>
				<p class="text-sm">Drop .eml, .pdf, .html, .txt or .md files</p>
				<label class="btn btn-sm mt-2 cursor-pointer">
					Choose files <input type="file" multiple accept=".eml,.pdf,.html,.htm,.txt,.md" class="hidden" onchange={(e) => (files = [...files, ...Array.from(e.currentTarget.files ?? [])])} />
				</label>
			</div>
			{#if files.length}
				<ul class="mt-3 space-y-1 text-sm">
					{#each files as f, i (f.name + i)}
						<li class="flex justify-between"><span>{f.name}</span><button class="text-xs text-muted hover:text-danger" onclick={() => (files = files.filter((_, j) => j !== i))}>remove</button></li>
					{/each}
				</ul>
			{/if}
		{/if}
		<div class="mt-4 flex justify-end gap-2">
			<button class="btn btn-primary" disabled={reading || (tab === 'links' ? !urls.trim() : tab === 'text' ? !text.trim() : !files.length)} onclick={read}>
				{reading ? 'Reading…' : 'Read'}
			</button>
		</div>
	{:else}
		<p class="mb-3 text-sm text-muted">Check what was read. Fix any field, untick what you don't want, then save.</p>
		<div class="space-y-2">
			{#each candidates as c (c.index)}
				<div class="card p-3 {c.keep ? '' : 'opacity-60'}">
					<div class="flex items-start gap-3">
						<input type="checkbox" class="mt-1" bind:checked={c.keep} disabled={c.kind === 'error'} aria-label="Save this" />
						<div class="min-w-0 flex-1">
							<div class="mb-1 flex items-center gap-2 text-xs text-muted">
								<span class="truncate">{c.source}</span>
								<span class="rounded bg-sunken px-1.5">{c.status}</span>
							</div>
							{#if c.kind === 'posting'}
								<div class="grid gap-2 sm:grid-cols-3">
									<input class="input" bind:value={c.company} aria-label="Company" />
									<input class="input" bind:value={c.title} aria-label="Title" />
									<input class="input" bind:value={c.location} aria-label="Location" placeholder="Location" />
								</div>
							{:else if c.kind === 'email'}
								<ul class="text-sm">
									{#each c.roles as r (r.title + r.company)}<li>{r.company} · {r.title} <span class="text-muted">{r.match_score != null ? `${Math.round(r.match_score * 100)}%` : ''}</span></li>{/each}
								</ul>
							{:else}
								<p class="text-sm text-danger">{c.error}</p>
							{/if}
						</div>
					</div>
				</div>
			{/each}
		</div>
		<div class="mt-4 flex justify-between gap-2">
			<button class="btn" onclick={reset}>Start over</button>
			<button class="btn btn-primary" disabled={saving || !candidates.some((c) => c.keep)} onclick={save}>
				{saving ? 'Saving…' : `Save ${candidates.filter((c) => c.keep).length} and screen`}
			</button>
		</div>
	{/if}
</Modal>
