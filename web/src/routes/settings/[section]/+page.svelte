<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { get, send, upload, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { provider } from '$lib/format';
	import ModelTag from '$lib/components/ModelTag.svelte';

	let { data } = $props();
	const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
	// The form edits a copy; the effect below resets it when the server's data changes.
	// svelte-ignore state_referenced_locally
	let s = $state(clone(data.settings));
	// svelte-ignore state_referenced_locally
	let original = $state(JSON.stringify(data.settings));
	$effect(() => {
		// New data from the server (after saving or navigating): reset the form.
		const fresh = JSON.stringify(data.settings);
		if (fresh !== original) {
			original = fresh;
			s = clone(data.settings);
		}
	});
	const dirty = $derived(JSON.stringify(s) !== original);
	let saving = $state(false);

	const nav = [
		['profile', 'Profile'], ['eligibility', 'Eligibility rules'], ['runs', 'Runs and limits'], ['sources', 'Job sources'],
		['mail', 'Mail'], ['models', 'Models'], ['templates', 'Resume templates'], ['log', 'AI log and privacy'], ['api', 'API']
	];
	const PROVIDERS = ['ollama', 'anthropic', 'openai', 'google_genai'];
	const STEPS = ['extract', 'score', 'tailor', 'humanize'];

	async function save() {
		saving = true;
		try {
			await send('PUT', '/settings', s);
			toasts.show('Saved. The previous config.toml is kept in data/config_backups/.');
			await invalidateAll();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'Saving failed.', 'error');
		} finally {
			saving = false;
		}
	}

	const lines = (v: string[]) => (v ?? []).join('\n');
	const parse = (t: string) => t.split('\n').map((x) => x.trim()).filter(Boolean);

	// templates
	let previewUrl = $state<string | null>(null);
	let previewNote = $state('');
	let previewing = $state(false);
	async function previewTemplate(name: string) {
		previewing = true;
		const res = await fetch('/api/cv/preview', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ template: name }) });
		previewing = false;
		if (!res.ok) {
			previewNote = (await res.json().catch(() => ({ detail: 'Preview failed.' }))).detail;
			previewUrl = null;
			return;
		}
		previewUrl = URL.createObjectURL(await res.blob());
		previewNote = `${res.headers.get('x-pages')} page(s)${res.headers.get('x-warning') ? ` · ${res.headers.get('x-warning')}` : ''}`;
	}
	async function addTemplate(e: Event) {
		const f = (e.currentTarget as HTMLInputElement).files?.[0];
		if (!f) return;
		const form = new FormData();
		form.set('file', f);
		try {
			await upload('/templates', form);
			toasts.show(`Added ${f.name}.`);
			invalidateAll();
		} catch (err) {
			toasts.show(err instanceof ApiError ? err.message : 'Upload failed.', 'error');
		}
	}
	async function downloadJake() {
		const text = await get<string>('/templates/builtin/jake');
		const a = document.createElement('a');
		a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
		a.download = 'jake.tex.j2';
		a.click();
	}

	// comparisons
	let report = $state<{ id: string; summary: Record<string, unknown>[]; markdown: string } | null>(null);
</script>

<svelte:head><title>Settings · AutoTailor</title></svelte:head>

<h1 class="h1 mb-4">Settings</h1>
<div class="grid gap-6 md:grid-cols-[12rem_1fr]">
	<nav class="flex gap-1 overflow-x-auto md:flex-col" aria-label="Settings sections">
		{#each nav as [id, label] (id)}
			<a href="/settings/{id}" class="rounded-md px-3 py-1.5 text-sm whitespace-nowrap {data.section === id ? 'bg-sunken font-medium' : 'text-muted hover:bg-sunken'}" aria-current={data.section === id ? 'page' : undefined}>{label}</a>
		{/each}
	</nav>

	<div class="min-w-0 pb-16">
		{#if data.section === 'profile'}
			<section class="card space-y-4 p-5">
				<div>
					<h2 class="mb-2 font-semibold">Education</h2>
					{#each s.profile.education as e, i (i)}
						<div class="mb-2 grid gap-2 sm:grid-cols-[8rem_1fr_9rem_10rem_1fr_auto]">
							<select class="input" bind:value={e.level} aria-label="Level">{#each ['associate', 'bachelor', 'master', 'mba', 'phd', 'other'] as l (l)}<option>{l}</option>{/each}</select>
							<input class="input" bind:value={e.field} placeholder="Major / field" aria-label="Field" />
							<select class="input" bind:value={e.status} aria-label="Status"><option value="in_progress">in progress</option><option value="completed">completed</option></select>
							<input class="input" type="date" bind:value={e.graduation_date} aria-label="Graduation" />
							<input class="input" bind:value={e.school} placeholder="School" aria-label="School" />
							<button class="btn btn-sm" onclick={() => s.profile.education.splice(i, 1)} aria-label="Remove">✕</button>
						</div>
					{/each}
					<button class="btn btn-sm" onclick={() => s.profile.education.push({ level: 'bachelor', field: '', status: 'in_progress', graduation_date: null, school: '' })}>+ Degree</button>
				</div>
				<label class="block"><span class="label">Locations you'd work in (one per line)</span><textarea class="input h-20" value={lines(s.profile.locations)} oninput={(e) => (s.profile.locations = parse(e.currentTarget.value))}></textarea></label>
				<label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={s.profile.open_to_remote} /> Open to remote roles</label>
				<label class="block"><span class="label">Work authorization</span><input class="input" bind:value={s.profile.work_authorization} placeholder="e.g. US citizen" /></label>
				<label class="block"><span class="label">Role interests (one per line)</span><textarea class="input h-24" value={lines(s.profile.role_interests)} oninput={(e) => (s.profile.role_interests = parse(e.currentTarget.value))}></textarea></label>
			</section>
		{:else if data.section === 'eligibility'}
			<section class="card space-y-3 p-5 text-sm">
				<label class="flex items-start gap-2"><input type="checkbox" bind:checked={s.eligibility.higher_degree_satisfies_lower} /> <span>A higher degree satisfies a lower requirement <span class="block text-xs text-muted">A posting asking for a Bachelor's admits a Master's student, unless it says undergraduates only.</span></span></label>
				<label class="flex items-start gap-2"><input type="checkbox" bind:checked={s.eligibility.in_progress_counts} /> <span>A degree in progress counts</span></label>
				<label class="flex items-start gap-2"><input type="checkbox" bind:checked={s.eligibility.keep_unclear} /> <span>Keep postings with unclear requirements (flagged), rather than dropping them</span></label>
				<label class="block max-w-xs"><span class="label">Graduation window slack (months)</span><input class="input" type="number" min="0" max="24" bind:value={s.eligibility.graduation_window_slack_months} /></label>
				<p class="text-xs text-muted">After changing these or your education, re-screen (no model calls): command palette › <code>screen --rejudge</code>.</p>
			</section>
		{:else if data.section === 'runs'}
			<section class="card grid gap-4 p-5 sm:grid-cols-2">
				{#each [['jobs_per_run', 'Resumes per run (top X)'], ['max_runs_per_day', 'Runs per day, at most'], ['max_runs_per_week', 'Runs per 7 days, at most'], ['shortlist_size', 'Postings fit-scored per run'], ['parallel_jobs', 'Resumes tailored at once']] as [k, label] (k)}
					<label><span class="label">{label}</span><input class="input" type="number" min="1" bind:value={s.run[k]} /></label>
				{/each}
				<label><span class="label">Minimum fit score to tailor: {Number(s.run.min_fit_score).toFixed(2)}</span><input type="range" min="0" max="1" step="0.05" bind:value={s.run.min_fit_score} class="w-full" /></label>
				<div class="space-y-2 sm:col-span-2">
					<label class="flex items-start gap-2 text-sm"><input type="checkbox" bind:checked={s.run.cut_low_fit} />
						<span>Cut jobs that score below the cut-off <span class="block text-xs text-muted">Never tailored, and left out of Jobs and Today. Jobs not scored yet are never cut, nor ones you tailored, applied to or added questions for. The Jobs page's “Cut” view lists them.</span></span></label>
					{#if s.run.cut_low_fit}
						<label class="block pl-6 text-sm"><span class="label">What cutting does</span>
							<select class="input max-w-md" bind:value={s.run.cut_action}>
								<option value="delete">Delete them (keeps the database small; they aren't fetched again)</option>
								<option value="hide">Hide them (kept in the database)</option>
							</select></label>
						<label class="flex flex-wrap items-center gap-2 pl-6 text-sm">
							<input type="checkbox" checked={s.run.cut_below != null} onchange={(e) => (s.run.cut_below = e.currentTarget.checked ? Math.min(Number(s.run.min_fit_score), 0.4) : null)} />
							<span>Use a different cut-off than the minimum fit score</span>
							{#if s.run.cut_below != null}
								<input type="range" min="0" max="1" step="0.05" bind:value={s.run.cut_below} class="w-40" aria-label="Cut-off" />
								<span class="tabular-nums">{Number(s.run.cut_below).toFixed(2)}</span>
							{/if}
						</label>
						<p class="pl-6 text-xs text-muted">Cutting below {Number(s.run.cut_below ?? s.run.min_fit_score).toFixed(2)}. A lower cut-off than the tailoring minimum keeps near misses in view.</p>
					{/if}
				</div>
				<h2 class="font-semibold sm:col-span-2">Tailored resumes</h2>
				{#each [['min_bullets', 'Fewest bullets'], ['max_bullets', 'Most bullets the model picks'], ['max_attempts', 'Drafts before giving up']] as [k, label] (k)}
					<label><span class="label">{label}</span><input class="input" type="number" min="1" bind:value={s.tailor[k]} /></label>
				{/each}
				<label class="flex items-start gap-2 text-sm sm:col-span-2"><input type="checkbox" bind:checked={s.tailor.fill_page} />
					<span>Fill the page <span class="block text-xs text-muted">After tailoring, add your next most relevant CV bullets (word for word, spread across sections) until the page is full, or trim a resume that runs over one page.</span></span></label>
			</section>
		{:else if data.section === 'sources'}
			<section class="card space-y-4 p-5">
				<label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={s.sources.ats_boards} /> Fetch from company job boards</label>
				<p class="text-xs text-muted">Company slugs, one per line: the name in the board's URL (boards.greenhouse.io/<b>slug</b>).</p>
				<div class="grid gap-3 md:grid-cols-3">
					{#each ['greenhouse', 'lever', 'ashby'] as k (k)}
						<label><span class="label capitalize">{k} ({s.sources[k].length})</span><textarea class="input h-56 font-mono text-xs" value={lines(s.sources[k])} oninput={(e) => (s.sources[k] = parse(e.currentTarget.value))}></textarea></label>
					{/each}
				</div>
				<div class="grid gap-3 md:grid-cols-2">
					<label><span class="label">Keep titles containing (one per line; empty keeps all)</span><textarea class="input h-24" value={lines(s.sources.title_include)} oninput={(e) => (s.sources.title_include = parse(e.currentTarget.value))}></textarea></label>
					<label><span class="label">Drop titles containing</span><textarea class="input h-24" value={lines(s.sources.title_exclude)} oninput={(e) => (s.sources.title_exclude = parse(e.currentTarget.value))}></textarea></label>
				</div>
				<p class="text-xs text-muted">LinkedIn, Handshake, Runway, Indeed and Glassdoor are never fetched; paste those postings with Add.</p>
			</section>
		{:else if data.section === 'mail'}
			<section class="card space-y-3 p-5 text-sm">
				<label class="flex items-center gap-2"><input type="checkbox" bind:checked={s.mail.drop_folder} /> Drop folder (save Runway .eml files into data/inbox, or drop them on any page)</label>
				<label class="flex items-center gap-2"><input type="checkbox" bind:checked={s.mail.imap} /> Pull from a Gmail label over IMAP (read-only)</label>
				{#if s.mail.imap}
					<div class="grid gap-3 pl-6 sm:grid-cols-3">
						<label><span class="label">Gmail address</span><input class="input" bind:value={s.mail.imap_user} /></label>
						<label><span class="label">Label</span><input class="input" bind:value={s.mail.imap_label} /></label>
						<label><span class="label">Look back (days)</span><input class="input" type="number" min="1" bind:value={s.mail.imap_days} /></label>
					</div>
					<p class="pl-6 text-xs text-muted">The app password goes in the OS keyring (<code>autotailor mail login</code>) or in .env as AUTOTAILOR_IMAP_PASSWORD, never here.</p>
				{/if}
				<label class="flex items-center gap-2"><input type="checkbox" bind:checked={s.mail.smtp_listener} /> Local mail server for forwarding from a desktop mail client (port <input class="input inline w-24 py-0.5" type="number" bind:value={s.mail.smtp_port} />)</label>
				<label class="block"><span class="label">Accepted senders (one per line)</span><textarea class="input h-16 font-mono text-xs" value={lines(s.mail.allowed_senders)} oninput={(e) => (s.mail.allowed_senders = parse(e.currentTarget.value))}></textarea></label>
			</section>
		{:else if data.section === 'models'}
			{@const m = data.models}
			<section class="card space-y-4 p-5">
				<p class="text-sm">Mode: <b>{m?.mode}</b> · {m?.mode === 'local' ? 'no posting or CV text leaves this machine' : 'steps marked “sent to” send posting and CV text to that provider'}.</p>
				{#each [['default', 'Default (every step without its own)'], ...STEPS.map((x) => [x, `Step: ${x}`])] as [key, label] (key)}
					{@const own = key === 'default' || !!s.models.steps[key]}
					{@const spec = key === 'default' ? s.models.default : (s.models.steps[key] ?? s.models.default)}
					<div class="border-t border-line pt-3 first:border-0 first:pt-0">
						<div class="mb-2 flex items-center justify-between gap-2">
							<h3 class="font-medium">{label}</h3>
							{#if key !== 'default'}
								<label class="flex items-center gap-2 text-sm"><input type="checkbox" checked={own} onchange={(e) => { if (e.currentTarget.checked) s.models.steps[key] = clone(s.models.default); else delete s.models.steps[key]; }} /> Own model</label>
							{/if}
						</div>
						{#if own}
							<div class="grid gap-2 sm:grid-cols-[10rem_1fr_9rem]">
								<select class="input" bind:value={spec.provider} aria-label="Provider">{#each PROVIDERS as pr (pr)}<option value={pr}>{provider(pr)}</option>{/each}</select>
								<input class="input font-mono text-xs" bind:value={spec.model} list="pulled-models" aria-label="Model" />
								{#if spec.provider === 'ollama'}<input class="input" type="number" step="1024" bind:value={spec.context_window} aria-label="Context window" title="Context window (tokens)" />{/if}
							</div>
							<div class="mt-1"><ModelTag local={spec.provider === 'ollama'} name={spec.provider} /></div>
						{/if}
					</div>
				{/each}
				<div class="border-t border-line pt-3">
					<h3 class="mb-2 font-medium">Embeddings (pre-ranking)</h3>
					<div class="grid gap-2 sm:grid-cols-[10rem_1fr]">
						<select class="input" bind:value={s.models.embeddings.provider} aria-label="Embeddings provider">{#each ['ollama', 'openai', 'google_genai'] as pr (pr)}<option value={pr}>{provider(pr)}</option>{/each}</select>
						<input class="input font-mono text-xs" bind:value={s.models.embeddings.model} aria-label="Embeddings model" />
					</div>
				</div>
				<datalist id="pulled-models">{#each m?.pulled ?? [] as p (p.name)}<option value={p.name}></option>{/each}</datalist>
			</section>
			<section class="card mt-4 p-5">
				<h2 class="mb-2 font-semibold">Pulled in Ollama</h2>
				{#if m?.pulled === null}<p class="text-sm text-muted">Ollama isn't reachable.</p>{/if}
				<ul class="space-y-1 text-sm">{#each m?.pulled ?? [] as p (p.name)}<li class="flex justify-between"><span class="font-mono text-xs">{p.name}</span><span class="text-muted">{p.parameters ?? ''} · {(p.size / 1e9).toFixed(1)} GB</span></li>{/each}</ul>
				{#each m?.steps.filter((x) => x.pulled === false) ?? [] as x (x.step)}<p class="mt-2 text-sm text-danger">{x.model} ({x.step}) isn't pulled: <code>ollama pull {x.model}</code></p>{/each}
			</section>
			<section class="card mt-4 p-5">
				<h2 class="mb-1 font-semibold">Comparisons</h2>
				<p class="mb-3 text-sm text-muted">Rerun a step on saved jobs with another model, e.g. command palette › <code>compare tailor -m ollama:gemma4:12b -n 3</code>.</p>
				<ul class="space-y-1 text-sm">{#each data.comparisons ?? [] as c (c.id)}<li><button class="text-accent hover:underline" onclick={async () => (report = await get(`/comparisons/${c.id}`))}>{c.id}</button></li>{:else}<li class="text-muted">No comparisons yet.</li>{/each}</ul>
				{#if report}
					<div class="mt-4 overflow-x-auto">
						<table class="w-full text-sm">
							<thead><tr>{#each Object.keys(report.summary[0] ?? {}) as k (k)}<th class="border-b border-line p-1.5 text-left text-xs text-muted">{k.replaceAll('_', ' ')}</th>{/each}</tr></thead>
							<tbody>{#each report.summary as row, i (i)}<tr>{#each Object.values(row) as v, j (j)}<td class="border-b border-line p-1.5 font-mono text-xs">{v ?? '–'}</td>{/each}</tr>{/each}</tbody>
						</table>
					</div>
				{/if}
			</section>
		{:else if data.section === 'templates'}
			{@const t = data.templates ?? { pdf: [], word: [] }}
			<section class="card space-y-4 p-5">
				<div class="grid gap-3 sm:grid-cols-2">
					<label><span class="label">PDF template</span><select class="input" bind:value={s.resume.template}>{#each t.pdf as x (x.name)}<option value={x.name}>{x.label}</option>{/each}</select></label>
					<label><span class="label">Word template</span><select class="input" bind:value={s.resume.word_template}><option value="">Built-in Word layout</option>{#each t.word as x (x.name)}<option value={x.name}>{x.label}</option>{/each}</select></label>
				</div>
				<p class="text-xs text-muted">LaTeX templates compile with Tectonic (downloaded into data/tools on first use; only TeX package names leave the machine). Your templates: values as <code>((( name )))</code>, statements as <code>((* for s in sections *))</code>; text is escaped for you. Word templates use docxtpl, e.g. <code>{'{{ name }}'}</code>.</p>
				<div class="flex flex-wrap gap-2">
					<button class="btn" onclick={() => previewTemplate(s.resume.template)} disabled={previewing}>{previewing ? 'Rendering…' : 'Preview with my CV'}</button>
					<button class="btn" onclick={downloadJake}>Download Jake's template</button>
					<label class="btn cursor-pointer">Upload .tex / .docx<input type="file" accept=".tex,.j2,.docx" class="hidden" onchange={addTemplate} /></label>
				</div>
				<ul class="text-sm">
					{#each [...t.pdf.filter((x) => !x.builtin), ...t.word] as x (x.name)}
						<li class="flex items-center justify-between border-t border-line py-1.5"><span class="font-mono text-xs">{x.name}</span><button class="btn btn-sm btn-danger" onclick={() => confirm(`Delete ${x.name}?`) && send('DELETE', `/templates/${x.name}`).then(() => invalidateAll())}>Delete</button></li>
					{/each}
				</ul>
				{#if previewUrl}<img src={previewUrl} alt="Template preview" class="max-w-xl rounded-lg border border-line bg-white" />{/if}
				{#if previewNote}<p class="text-xs text-muted">{previewNote}</p>{/if}
			</section>
		{:else if data.section === 'log'}
			<section class="card space-y-3 p-5 text-sm">
				<p class="text-muted">Every model call can be recorded locally (a job's AI log tab, and Activity). Nothing here leaves this machine.</p>
				<label class="flex items-center gap-2"><input type="checkbox" bind:checked={s.log.enabled} /> Record model calls</label>
				<label class="flex items-center gap-2"><input type="checkbox" bind:checked={s.log.keep_prompts} /> Keep prompts and responses (not just timing and status)</label>
				<label class="block max-w-xs"><span class="label">Keep for (days)</span><input class="input" type="number" min="1" bind:value={s.log.retention_days} /></label>
			</section>
		{:else if data.section === 'api'}
			<section class="card space-y-3 p-5 text-sm">
				<p>The local API serves everything this app does at <code>http://127.0.0.1:8765</code> (documentation at <code>/docs</code>), for scripts or a browser extension.</p>
				<p>Clients send the token from <code>data/api_token</code> in the <code>X-AutoTailor-Token</code> header. This app's server holds it, so your browser never sees it.</p>
				<p class="text-muted">To replace the token, delete <code>data/api_token</code> and restart <code>autotailor ui</code>.</p>
			</section>
		{/if}

		{#if !['api', 'templates'].includes(data.section) || dirty}
			<div class="fixed right-4 bottom-16 z-30 flex gap-2 rounded-lg border border-line bg-surface p-2 shadow-lg md:right-8">
				<button class="btn" disabled={!dirty} onclick={() => (s = clone(data.settings))}>Discard</button>
				<button class="btn btn-primary" disabled={!dirty || saving} onclick={save}>{saving ? 'Saving…' : 'Save settings'}</button>
			</div>
		{/if}
	</div>
</div>
