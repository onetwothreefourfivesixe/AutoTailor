<script lang="ts">
	import { goto } from '$app/navigation';
	import { get, command, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import type { JobRow } from '$lib/types';

	let { open = $bindable(false), onadd }: { open?: boolean; onadd: () => void } = $props();
	let query = $state('');
	let jobs = $state<JobRow[]>([]);
	let selected = $state(0);
	let input: HTMLInputElement | undefined = $state();
	let dialog: HTMLDialogElement | undefined = $state();

	interface Item {
		label: string;
		hint: string;
		run: () => void | Promise<void>;
	}

	const pages: Item[] = [
		['Today', '/'], ['Jobs', '/jobs'], ['Runway', '/runway'], ['Applications', '/applications'], ['Runs', '/runs'],
		['Master CV', '/cv'], ['Settings', '/settings/profile'], ['Models', '/settings/models'], ['Resume templates', '/settings/templates'], ['Activity', '/activity']
	].map(([label, href]) => ({ label, hint: 'Go to', run: () => goto(href) }));

	const actions: Item[] = [
		{ label: 'Add jobs or emails', hint: 'A', run: () => onadd() },
		{ label: 'Run the whole pipeline', hint: 'Task', run: () => start('run') },
		{ label: 'Fetch job boards', hint: 'Task', run: () => start('fetch') },
		{ label: 'Screen new postings', hint: 'Task', run: () => start('screen') },
		{ label: 'Rank', hint: 'Task', run: () => start('rank') },
		{ label: 'Tailor the shortlist', hint: 'Task', run: () => start('tailor') },
		{ label: 'Match Runway roles', hint: 'Task', run: () => start('runway resolve') }
	];

	async function start(cmd: string) {
		try {
			await command(cmd);
			toasts.show(`Started: ${cmd}`);
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'Could not start it.', 'error');
		}
	}

	const items = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const match = (i: Item) => !q || i.label.toLowerCase().includes(q);
		const out: Item[] = [...actions.filter(match), ...pages.filter(match)];
		for (const j of jobs) out.push({ label: `${j.company} · ${j.title}`, hint: 'Job', run: () => goto(`/jobs/${encodeURIComponent(j.id)}`) });
		if (q) out.push({ label: `autotailor ${query.trim().replace(/^autotailor\s+/, '')}`, hint: 'Run command', run: () => start(query.trim()) });
		return out;
	});

	let timer: ReturnType<typeof setTimeout>;
	$effect(() => {
		const q = query.trim();
		clearTimeout(timer);
		if (q.length < 2) {
			jobs = [];
			return;
		}
		timer = setTimeout(async () => {
			jobs = await get<JobRow[]>(`/jobs?q=${encodeURIComponent(q)}&limit=6`).catch(() => []);
		}, 150);
	});

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) {
			query = '';
			selected = 0;
			dialog.showModal();
			input?.focus();
		}
		if (!open && dialog.open) dialog.close();
	});

	async function choose(i: number) {
		const item = items[i];
		if (!item) return;
		open = false;
		await item.run();
	}

	function key(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			selected = Math.min(items.length - 1, selected + 1);
			e.preventDefault();
		} else if (e.key === 'ArrowUp') {
			selected = Math.max(0, selected - 1);
			e.preventDefault();
		} else if (e.key === 'Enter') {
			e.preventDefault();
			choose(selected);
		}
	}
</script>

<dialog bind:this={dialog} onclose={() => (open = false)} class="mx-auto mt-[12vh] w-[calc(100%-2rem)] max-w-xl rounded-xl border border-line bg-surface p-0 text-fg shadow-2xl backdrop:bg-black/40" aria-label="Command palette">
	<input
		bind:this={input}
		bind:value={query}
		oninput={() => (selected = 0)}
		onkeydown={key}
		class="w-full border-b border-line bg-transparent px-4 py-3 text-base outline-none"
		placeholder="Jump to a job or page, start a task, or type a command…"
		aria-label="Search"
	/>
	<ul class="max-h-80 overflow-y-auto py-1" role="listbox">
		{#each items as item, i (item.hint + item.label)}
			<li role="option" aria-selected={i === selected}>
				<button class="flex w-full items-center justify-between px-4 py-2 text-left text-sm {i === selected ? 'bg-sunken' : ''}" onmouseenter={() => (selected = i)} onclick={() => choose(i)}>
					<span class="truncate">{item.label}</span><span class="ml-3 shrink-0 text-xs text-muted">{item.hint}</span>
				</button>
			</li>
		{/each}
	</ul>
</dialog>
