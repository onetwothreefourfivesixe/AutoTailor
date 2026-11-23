<script lang="ts">
	// AutoTailor's command line in the browser: the same commands as the terminal, run by the
	// background worker (never a shell). Output streams in as the command runs; long commands
	// keep going if you leave the page, and their output is here when you come back.
	import { onDestroy, onMount, tick } from 'svelte';
	import { get, send, command, ApiError } from '$lib/client/api';

	type Entry = { id: number | null; text: string; status: string; log: string; error?: string };
	type Help = { command: string; help: string };

	let entries = $state<Entry[]>([]);
	let input = $state('');
	let help = $state<Help[]>([]);
	let showHelp = $state(false);
	let history: string[] = [];
	let back = -1; // position while browsing history with ↑/↓
	let box: HTMLDivElement | undefined = $state();
	let field: HTMLInputElement | undefined = $state();
	const streams = new Map<number, EventSource>();

	function load<T>(key: string, fallback: T): T {
		try {
			return JSON.parse(localStorage.getItem(key) ?? '') as T;
		} catch {
			return fallback;
		}
	}
	function save(key: string, value: unknown) {
		try {
			localStorage.setItem(key, JSON.stringify(value));
		} catch {
			/* private window: history just isn't kept */
		}
	}

	async function scroll() {
		await tick();
		box?.scrollTo({ top: box.scrollHeight });
	}

	function follow(e: Entry) {
		if (e.id === null || streams.has(e.id)) return;
		const es = new EventSource(`/api/tasks/${e.id}/events`);
		streams.set(e.id, es);
		es.addEventListener('log', (m) => {
			e.log += JSON.parse((m as MessageEvent).data);
			scroll();
		});
		es.addEventListener('progress', (m) => {
			e.status = JSON.parse((m as MessageEvent).data).status;
			if (!['queued', 'running'].includes(e.status)) finish(e);
		});
		es.onerror = () => finish(e); // the server ends the stream when the task ends
	}
	// The final output can arrive in the same message as the end: read the whole log once more.
	async function finish(e: Entry) {
		close(e.id!);
		const t = await get<{ log: string; status: string }>(`/tasks/${e.id}`).catch(() => null);
		if (t) {
			e.log = t.log;
			e.status = t.status;
			scroll();
		}
	}
	function close(id: number) {
		streams.get(id)?.close();
		streams.delete(id);
	}

	onMount(async () => {
		history = load<string[]>('terminal.history', []);
		help = await get<Help[]>('/commands').catch(() => []);
		// The last few commands run from here, with their output.
		const recent = (await get<{ id: number; kind: string; args_json: string; status: string }[]>('/tasks?limit=40').catch(() => []))
			.filter((t) => t.kind === 'cli')
			.slice(0, 8)
			.reverse();
		for (const t of recent) {
			const full = await get<{ log: string; status: string }>(`/tasks/${t.id}`).catch(() => null);
			const e: Entry = { id: t.id, text: JSON.parse(t.args_json).join(' '), status: full?.status ?? t.status, log: full?.log ?? '' };
			entries.push(e);
			if (['queued', 'running'].includes(e.status)) follow(entries[entries.length - 1]);
		}
		scroll();
		field?.focus();
	});
	onDestroy(() => [...streams.keys()].forEach(close));

	async function run() {
		const text = input.trim().replace(/^autotailor\s+/, '');
		if (!text) return;
		input = '';
		back = -1;
		history = [text, ...history.filter((h) => h !== text)].slice(0, 50);
		save('terminal.history', history);
		if (text === 'clear') {
			entries = [];
			return;
		}
		if (text === 'help') {
			showHelp = true;
			return;
		}
		const e: Entry = { id: null, text, status: 'queued', log: '' };
		entries.push(e);
		const live = entries[entries.length - 1];
		scroll();
		try {
			const t = await command(text);
			live.id = t.id;
			follow(live);
		} catch (err) {
			live.status = 'error';
			live.error = err instanceof ApiError ? err.message : 'Could not start it.';
		}
		scroll();
	}

	function key(e: KeyboardEvent) {
		if (e.key === 'ArrowUp' && history.length) {
			back = Math.min(history.length - 1, back + 1);
			input = history[back];
			e.preventDefault();
		} else if (e.key === 'ArrowDown') {
			back = Math.max(-1, back - 1);
			input = back < 0 ? '' : history[back];
			e.preventDefault();
		}
	}

	const STATUS: Record<string, string> = { queued: 'waiting for the worker…', running: 'running…', done: 'done', failed: 'failed', cancelled: 'stopped', error: 'not run' };
</script>

<svelte:head><title>Command line · AutoTailor</title></svelte:head>

<div class="mb-3 flex flex-wrap items-end justify-between gap-2">
	<div>
		<h1 class="h1">Command line</h1>
		<p class="text-sm text-muted">AutoTailor's own commands, as in the terminal (not a shell). Long ones keep running in the background if you leave; their output is here when you come back.</p>
	</div>
	<button class="btn btn-sm" onclick={() => (showHelp = !showHelp)} aria-expanded={showHelp}>{showHelp ? 'Hide commands' : 'Commands'}</button>
</div>

{#if showHelp}
	<section class="card mb-3 max-h-72 overflow-y-auto p-3 text-sm" aria-label="Available commands">
		<p class="mb-2 text-xs text-muted">Click one to fill it in. Add <code>--help</code> to any command for its options, e.g. <code>rank --help</code>. Also: <code>help</code>, <code>clear</code>.</p>
		<ul class="grid gap-x-6 gap-y-1 md:grid-cols-2">
			{#each help as h (h.command)}
				<li class="flex min-w-0 gap-2"><button class="shrink-0 font-mono text-accent hover:underline" onclick={() => { input = h.command + ' '; field?.focus(); }}>{h.command}</button><span class="truncate text-muted" title={h.help}>{h.help}</span></li>
			{/each}
		</ul>
	</section>
{/if}

<div class="overflow-hidden rounded-lg border border-line bg-[#0f1115] text-[#d7dae0] shadow-sm">
	<div bind:this={box} class="h-[60vh] overflow-y-auto p-4 font-mono text-[13px] leading-relaxed" role="log" aria-live="polite" aria-label="Command output">
		{#if !entries.length}<p class="text-[#7d8590]">Type a command below, e.g. <span class="text-[#d7dae0]">jobs list</span>, <span class="text-[#d7dae0]">rank --rescore</span>, <span class="text-[#d7dae0]">discover "biomedical"</span>, or <span class="text-[#d7dae0]">help</span>.</p>{/if}
		{#each entries as e, i (i)}
			<div class="mb-3">
				<div class="flex items-center gap-2">
					<span class="text-[#3fb950]">autotailor</span><span class="min-w-0 flex-1 break-all">{e.text}</span>
					<span class="shrink-0 text-xs {e.status === 'failed' || e.status === 'error' ? 'text-[#f85149]' : 'text-[#7d8590]'}">{STATUS[e.status] ?? e.status}</span>
					{#if e.id !== null && ['queued', 'running'].includes(e.status)}<button class="shrink-0 rounded border border-[#30363d] px-1.5 text-xs hover:bg-[#21262d]" onclick={() => send('POST', `/tasks/${e.id}/cancel`)}>Stop</button>{/if}
				</div>
				{#if e.error}<pre class="whitespace-pre-wrap text-[#f85149]">{e.error}</pre>{/if}
				{#if e.log}<pre class="whitespace-pre-wrap">{e.log.replace(/^\$ autotailor .*\n/, '')}</pre>{/if}
			</div>
		{/each}
	</div>
	<form class="flex items-center gap-2 border-t border-[#30363d] px-4 py-2 font-mono text-[13px]" onsubmit={(e) => { e.preventDefault(); run(); }}>
		<label for="terminal-input" class="text-[#3fb950]">autotailor</label>
		<input id="terminal-input" bind:this={field} bind:value={input} onkeydown={key} class="min-w-0 flex-1 bg-transparent text-[#d7dae0] outline-none" autocomplete="off" spellcheck="false" placeholder="jobs list" aria-label="Command" />
		<button class="rounded border border-[#30363d] px-2 py-0.5 text-xs hover:bg-[#21262d]">Run</button>
	</form>
</div>
<p class="mt-2 text-xs text-muted">↑/↓ go through earlier commands. Commands run one at a time; a command waits while another task (such as a tailoring run) finishes.</p>
