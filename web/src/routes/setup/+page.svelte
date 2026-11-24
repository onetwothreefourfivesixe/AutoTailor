<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { get, send, upload, command, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import type { SetupStatus } from '$lib/types';

	let { data } = $props();

	const STEPS = ['Welcome', 'AI models', 'Your CV', 'About you', 'Job boards', 'Done'];
	let step = $state(0);
	let busy = $state(false);
	const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
	// svelte-ignore state_referenced_locally
	let s = $state(clone(data.settings));
	// svelte-ignore state_referenced_locally
	let status = $state<SetupStatus>(data.status);

	function fail(e: unknown, fallback = 'That failed.') {
		toasts.show(e instanceof ApiError ? e.message : fallback, 'error');
	}

	async function saveSettings(next = true) {
		busy = true;
		try {
			s = await send('PUT', '/settings', s);
			status = await get<SetupStatus>('/setup/status');
			if (next) step++;
		} catch (e) {
			fail(e, 'Saving failed.');
		} finally {
			busy = false;
		}
	}

	// --- step 2: models -------------------------------------------------------------------
	type Mode = 'local' | 'cloud' | 'mixed';
	// svelte-ignore state_referenced_locally
	let mode = $state<Mode>(data.status.mode as Mode);
	let cloudProvider = $state<'anthropic' | 'openai' | 'google_genai'>('google_genai');
	let cloudModel = $state('gemini-2.5-pro');
	let key = $state('');
	// svelte-ignore state_referenced_locally
	let pulled = $state<string[]>((data.models.pulled ?? []).map((m) => m.name));
	const LOCAL_CHAT = 'qwen3-vl:4b-instruct';
	const LOCAL_EMBED = 'nomic-embed-text';
	const has = (name: string) => pulled.some((p) => p === name || p === `${name}:latest`);
	const PROVIDERS = {
		anthropic: {
			label: 'Anthropic (Claude)', model: 'claude-sonnet-5-5', url: 'https://console.anthropic.com/settings/keys', starts: 'sk-ant-',
			how: ['Sign in at console.anthropic.com.', 'Add credit under Settings › Billing.', 'Settings › API Keys › Create Key.', 'Copy the key (shown once) and paste it here.']
		},
		openai: {
			label: 'OpenAI', model: '', url: 'https://platform.openai.com/api-keys', starts: 'sk-',
			how: ['Sign in at platform.openai.com.', 'Add a payment method under Settings › Billing.', 'API keys › Create new secret key.', 'Copy the key (shown once) and paste it here.']
		},
		google_genai: {
			label: 'Google Gemini', model: 'gemini-2.5-pro', url: 'https://aistudio.google.com/app/apikey', starts: 'AIza',
			how: ['Sign in at aistudio.google.com with a Google account.', 'Get API key › Create API key.', 'Copy the key and paste it here.', 'The free tier may use what you send to improve Google’s products; turn on billing for the paid terms.']
		}
	} as const;
	$effect(() => {
		const p = PROVIDERS[cloudProvider];
		if (p.model) cloudModel = p.model;
	});

	async function refreshOllama() {
		const m = await get<{ pulled: { name: string }[] | null }>('/models').catch(() => ({ pulled: null }));
		pulled = (m.pulled ?? []).map((x) => x.name);
		if (m.pulled === null) toasts.show('Ollama isn’t running. Start it, then check again.', 'error');
	}

	async function saveKey() {
		try {
			const r = await send<{ keys: Record<string, boolean> }>('PUT', '/setup/keys', { provider: cloudProvider, key });
			status.keys = r.keys;
			key = '';
			toasts.show('Key saved to .env on this computer.');
		} catch (e) {
			fail(e);
		}
	}

	async function saveModels() {
		const local = { ...s.models.default, provider: 'ollama', model: LOCAL_CHAT };
		const cloud = { ...s.models.default, provider: cloudProvider, model: cloudModel };
		if (mode === 'local') {
			s.models.default = local;
			s.models.steps = {};
			s.models.embeddings = { ...s.models.embeddings, provider: 'ollama', model: LOCAL_EMBED };
		} else if (mode === 'cloud') {
			s.models.default = cloud;
			s.models.steps = {};
			s.models.embeddings =
				cloudProvider === 'anthropic'
					? { ...s.models.embeddings, provider: 'ollama', model: LOCAL_EMBED }
					: { ...s.models.embeddings, provider: cloudProvider, model: cloudProvider === 'openai' ? 'text-embedding-3-small' : 'gemini-embedding-001' };
		} else {
			s.models.default = local;
			s.models.steps = { tailor: cloud, humanize: cloud };
			s.models.embeddings = { ...s.models.embeddings, provider: 'ollama', model: LOCAL_EMBED };
		}
		await saveSettings();
	}

	// --- step 3: CV -----------------------------------------------------------------------------
	let dragging = $state(false);
	let reading = $state(false);
	let draft = $state<Record<string, any> | null>(null);
	let unverified = $state<string[]>([]);
	let suggestedEducation = $state<Record<string, any>[]>([]);
	const SECTIONS: [string, string][] = [['education', 'Education'], ['experience', 'Experience'], ['projects', 'Projects'], ['activities', 'Activities'], ['volunteer', 'Volunteer'], ['awards', 'Awards'], ['certifications', 'Certifications']];
	const bulletsOf = (section: string, e: Record<string, any>) => (section === 'education' ? e.details : e.bullets) ?? [];
	const countBullets = (cv: Record<string, any>) => SECTIONS.reduce((n, [k]) => n + (cv[k] ?? []).reduce((m: number, e: any) => m + bulletsOf(k, e).length, 0), 0);

	async function readCv(file: File) {
		reading = true;
		draft = null;
		const form = new FormData();
		form.set('file', file);
		try {
			const r = await upload<{ cv: Record<string, any>; unverified: string[]; education: Record<string, any>[] }>('/setup/cv', form);
			draft = r.cv;
			unverified = r.unverified;
			suggestedEducation = r.education;
		} catch (e) {
			fail(e, 'Reading the file failed.');
		} finally {
			reading = false;
		}
	}

	async function saveCv() {
		busy = true;
		try {
			await send('PUT', '/cv', { cv: draft, confirm_delete_used: true });
			if (suggestedEducation.length && !s.profile.education.length) s.profile.education = suggestedEducation;
			status = await get<SetupStatus>('/setup/status');
			toasts.show('CV saved. You can edit it any time on the Master CV page.');
			step++;
		} catch (e) {
			fail(e, 'Saving the CV failed.');
		} finally {
			busy = false;
		}
	}

	// --- step 4: about you -------------------------------------------------------------------------
	const INTERESTS = ['Software engineering', 'Machine learning', 'Data science', 'AI research', 'Backend', 'Frontend', 'Full stack', 'Robotics', 'Quantitative finance', 'Product management', 'Security', 'Infrastructure'];
	const lines = (v: string[]) => (v ?? []).join('\n');
	const parse = (t: string) => t.split('\n').map((x) => x.trim()).filter(Boolean);
	function toggleInterest(i: string) {
		const has = s.profile.role_interests.includes(i);
		s.profile.role_interests = has ? s.profile.role_interests.filter((x: string) => x !== i) : [...s.profile.role_interests, i];
	}

	// --- step 5: job boards ---------------------------------------------------------------------------
	// svelte-ignore state_referenced_locally
	let internships = $state((data.settings.sources.title_include ?? []).includes('intern'));
	let boardQuery = $state('');
	// svelte-ignore state_referenced_locally
	let chosen = $state<Set<string>>(new Set(data.boards.filter((b) => b.following).map((b) => `${b.ats}:${b.slug}`)));
	let customAts = $state('greenhouse');
	let customSlug = $state('');
	const shown = $derived(data.boards.filter((b) => !boardQuery || b.name.toLowerCase().includes(boardQuery.toLowerCase())));
	// Find companies for the fields described in your own words (catalog + optional model suggestions).
	type Found = { name: string; ats: string; slug: string; jobs: number; following: boolean; from_model: boolean };
	// svelte-ignore state_referenced_locally
	let fields = $state((data.settings.profile?.role_interests ?? []).join(', '));
	let askModel = $state(false);
	let searching = $state(false);
	let found = $state<{ industries: string[]; boards: Found[]; not_found: string[]; model: string; local: boolean; suggest_usajobs?: boolean } | null>(null);
	async function findCompanies() {
		searching = true;
		try {
			found = await send('POST', '/setup/discover', { text: fields, ask_model: askModel });
			chosen = new Set([...chosen, ...found!.boards.map((b) => `${b.ats}:${b.slug}`)]);
			toasts.show(`Found ${found!.boards.length} companies; they're selected below.`);
		} catch (e) {
			fail(e);
		} finally {
			searching = false;
		}
	}
	function toggleBoard(id: string) {
		const c = new Set(chosen);
		c.has(id) ? c.delete(id) : c.add(id);
		chosen = c;
	}
	async function saveSources() {
		for (const ats of ['greenhouse', 'lever', 'ashby']) {
			s.sources[ats] = [...chosen].filter((id) => id.startsWith(`${ats}:`)).map((id) => id.slice(ats.length + 1));
		}
		s.sources.title_include = internships ? ['intern', 'internship', 'co-op'] : [];
		await saveSettings();
	}

	// --- step 6: done ------------------------------------------------------------------------------------
	async function finish(runNow: boolean) {
		await send('POST', '/setup/finish');
		if (runNow) {
			try {
				await command('run');
				toasts.show('Started the pipeline. Follow it in the task tray.');
			} catch (e) {
				fail(e);
			}
		}
		await invalidateAll();
		goto('/');
	}

	async function skip() {
		await send('POST', '/setup/finish');
		goto('/');
	}

	const CHECK_LABELS: Record<string, string> = { settings: 'Settings file is valid', cv: 'Your CV is in', profile: 'Your education is set', models: 'Every cloud model has its API key', sources: 'At least one job board is followed' };
</script>

<svelte:head><title>Set up · AutoTailor</title></svelte:head>

<div class="mx-auto max-w-3xl">
	<div class="mb-6 flex items-center justify-between">
		<h1 class="h1">Set up AutoTailor</h1>
		<button class="text-sm text-muted hover:underline" onclick={skip}>Skip for now</button>
	</div>

	<ol class="mb-8 flex gap-1" aria-label="Setup steps">
		{#each STEPS as label, i (label)}
			<li class="flex-1">
				<button class="w-full text-left" onclick={() => (step = i)} aria-current={step === i ? 'step' : undefined}>
					<div class="h-1.5 rounded-full {i <= step ? 'bg-accent' : 'bg-sunken'}"></div>
					<div class="mt-1 hidden text-xs sm:block {i === step ? 'font-medium' : 'text-muted'}">{label}</div>
				</button>
			</li>
		{/each}
	</ol>

	{#if step === 0}
		<section class="card space-y-4 p-6">
			<h2 class="text-lg font-semibold">Find the jobs you can get, and send a resume made for each one.</h2>
			<p class="text-sm text-muted">AutoTailor collects postings, drops the ones you aren't eligible for, ranks the rest by how well they fit you, and writes a tailored one-page resume for the best ones, using only what's in your CV. It never applies for you, and your data stays on this computer.</p>
			<p class="text-sm">Setup takes about five minutes:</p>
			<ol class="list-decimal space-y-1 pl-5 text-sm">
				<li>Choose where the AI runs: on this computer, or a cloud provider with an API key.</li>
				<li>Drop in your current resume or CV; AutoTailor turns it into your master CV.</li>
				<li>Tell it about your degree and the roles you want.</li>
				<li>Pick the companies whose job boards to follow.</li>
			</ol>
			<div class="flex justify-end"><button class="btn btn-primary" onclick={() => step++}>Start</button></div>
		</section>
	{:else if step === 1}
		<section class="card space-y-5 p-6">
			<div>
				<h2 class="text-lg font-semibold">Where should the AI run?</h2>
				<p class="text-sm text-muted">You can change this any time in Settings › Models, step by step.</p>
			</div>
			<div class="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="Where the AI runs">
				{#each [['local', 'On this computer', 'Free and private. Needs Ollama, and is slower on laptops without a good GPU.'], ['cloud', 'In the cloud', 'Best quality and speed. Needs an API key; you pay per use, usually cents per resume.'], ['mixed', 'Mixed', 'Reading and ranking on this computer; writing resumes in the cloud.']] as [id, title, note] (id)}
					<button role="radio" aria-checked={mode === id} class="card p-4 text-left {mode === id ? 'border-accent ring-1 ring-accent' : 'hover:bg-sunken'}" onclick={() => (mode = id as Mode)}>
						<div class="font-medium">{title}</div>
						<div class="mt-1 text-xs text-muted">{note}</div>
					</button>
				{/each}
			</div>

			{#if mode !== 'cloud'}
				<div class="rounded-lg bg-sunken p-4 text-sm">
					<h3 class="mb-2 font-medium">On this computer (Ollama)</h3>
					<ol class="list-decimal space-y-1 pl-5">
						<li>Install Ollama from <a class="text-accent hover:underline" href="https://ollama.com/download" target="_blank" rel="noreferrer">ollama.com/download</a> and start it.</li>
						<li>In a terminal, download the two models:
							<ul class="mt-1 space-y-1">
								{#each [LOCAL_CHAT, LOCAL_EMBED] as m (m)}
									<li class="flex items-center gap-2"><code class="rounded bg-surface px-1.5 py-0.5">ollama pull {m}</code>
										{#if has(m)}<span class="text-xs text-good">✓ installed</span>{:else}<span class="text-xs text-warn">not yet</span>{/if}</li>
								{/each}
							</ul>
						</li>
					</ol>
					<button class="btn btn-sm mt-3" onclick={refreshOllama}>Check again</button>
				</div>
			{/if}

			{#if mode !== 'local'}
				{@const p = PROVIDERS[cloudProvider]}
				<div class="rounded-lg bg-sunken p-4 text-sm">
					<h3 class="mb-2 font-medium">Cloud provider</h3>
					<div class="mb-3 flex flex-wrap gap-2">
						{#each Object.entries(PROVIDERS) as [id, info] (id)}
							<button class="btn btn-sm {cloudProvider === id ? 'btn-primary' : ''}" onclick={() => (cloudProvider = id as typeof cloudProvider)}>{info.label} {status.keys[id] ? '✓' : ''}</button>
						{/each}
					</div>
					<p class="mb-1 font-medium">Get a {p.label} API key:</p>
					<ol class="mb-3 list-decimal space-y-0.5 pl-5">{#each p.how as h (h)}<li>{h}</li>{/each}</ol>
					<a class="text-accent hover:underline" href={p.url} target="_blank" rel="noreferrer">Open the {p.label} key page ↗</a>
					<div class="mt-3 flex flex-wrap gap-2">
						<input class="input min-w-0 flex-1 font-mono" type="password" autocomplete="off" placeholder="{status.keys[cloudProvider] ? 'A key is saved. Paste a new one to replace it.' : `Paste your key (starts with ${p.starts})`}" bind:value={key} aria-label="API key" />
						<button class="btn btn-primary" disabled={!key.trim()} onclick={saveKey}>Save key</button>
					</div>
					<p class="mt-1 text-xs text-muted">Saved only in the <code>.env</code> file on this computer; it's never shown again or sent anywhere but {p.label}.</p>
					<label class="mt-3 block"><span class="label">Model</span><input class="input font-mono text-xs" bind:value={cloudModel} placeholder="The provider's model ID" /></label>
					{#if cloudProvider === 'anthropic' && mode === 'cloud'}<p class="mt-1 text-xs text-muted">Anthropic has no model for ranking (embeddings), so that step uses Ollama's {LOCAL_EMBED}.</p>{/if}
				</div>
			{/if}

			<div class="flex justify-between">
				<button class="btn" onclick={() => step--}>Back</button>
				<button class="btn btn-primary" disabled={busy || (mode !== 'local' && !cloudModel.trim())} onclick={saveModels}>Save and continue</button>
			</div>
		</section>
	{:else if step === 2}
		<section class="card space-y-4 p-6">
			<div>
				<h2 class="text-lg font-semibold">Your CV</h2>
				<p class="text-sm text-muted">Drop your current resume or CV. Include everything you might ever want on a resume, even if it runs past a page: AutoTailor picks the right parts for each job.</p>
			</div>
			{#if status.cv === 'ready' && !draft}
				<p class="rounded bg-good-bg p-3 text-sm text-good">You already have a master CV. Drop a file to replace it, or continue.</p>
			{/if}
			<div
				role="region"
				aria-label="Drop your CV here"
				class="flex h-44 flex-col items-center justify-center rounded-lg border-2 border-dashed text-center {dragging ? 'border-accent bg-sunken' : 'border-line'}"
				ondragover={(e) => { e.preventDefault(); e.stopPropagation(); dragging = true; }}
				ondragleave={() => (dragging = false)}
				ondrop={(e) => { e.preventDefault(); e.stopPropagation(); dragging = false; const f = e.dataTransfer?.files[0]; if (f) readCv(f); }}
			>
				{#if reading}
					<p class="text-sm">Reading your CV… this can take a minute with a local model.</p>
				{:else}
					<p class="text-sm">Drop a PDF, Word, text or Markdown file here</p>
					<label class="btn btn-sm mt-2 cursor-pointer">Choose a file<input type="file" class="hidden" accept=".pdf,.docx,.txt,.md,.yaml,.yml" onchange={(e) => { const f = e.currentTarget.files?.[0]; if (f) readCv(f); }} /></label>
					<p class="mt-2 text-xs text-muted">Read on this computer, then sorted into sections by your “extract” model.</p>
				{/if}
			</div>

			{#if draft}
				<div class="space-y-3">
					<p class="text-sm"><b>{draft.contact?.name}</b> · {countBullets(draft)} bullets found. Check it over; everything can be edited later on the Master CV page.</p>
					{#if unverified.length}
						<p class="rounded bg-warn-bg p-3 text-sm text-warn">{unverified.length} bullet(s) don't match your file word for word (marked below). The AI may have reworded them; fix them on the Master CV page after saving.</p>
					{/if}
					{#each SECTIONS as [key, label] (key)}
						{#if (draft[key] ?? []).length}
							<details class="card p-3" open={key === 'experience'}>
								<summary class="cursor-pointer text-sm font-medium">{label} ({draft[key].length})</summary>
								<ul class="mt-2 space-y-2 text-sm">
									{#each draft[key] as e, i (i)}
										<li>
											<div class="font-medium">{key === 'education' ? e.school : e.title}{(key === 'education' ? e.degree : e.organization) ? ` · ${key === 'education' ? e.degree : e.organization}` : ''}</div>
											<ul class="mt-1 list-disc pl-5 text-muted">
												{#each bulletsOf(key, e) as b (b.id)}<li class={unverified.includes(b.id) ? 'text-warn' : ''}>{b.text}{unverified.includes(b.id) ? ' (check)' : ''}</li>{/each}
											</ul>
										</li>
									{/each}
								</ul>
							</details>
						{/if}
					{/each}
					{#if (draft.skills ?? []).length}<p class="text-sm"><span class="text-muted">Skills:</span> {draft.skills.map((g: any) => g.items.join(', ')).join('; ')}</p>{/if}
				</div>
			{/if}

			<div class="flex justify-between">
				<button class="btn" onclick={() => step--}>Back</button>
				<div class="flex gap-2">
					{#if !draft}<button class="btn" onclick={() => step++}>{status.cv === 'ready' ? 'Continue' : "I'll add it later"}</button>{/if}
					{#if draft}<button class="btn btn-primary" disabled={busy} onclick={saveCv}>Save CV and continue</button>{/if}
				</div>
			</div>
		</section>
	{:else if step === 3}
		<section class="card space-y-4 p-6">
			<div>
				<h2 class="text-lg font-semibold">About you</h2>
				<p class="text-sm text-muted">Used to skip postings you can't apply to (degree level, graduation date) and to rank the rest.</p>
			</div>
			<div>
				<h3 class="mb-2 text-sm font-medium">Your degrees</h3>
				{#each s.profile.education as e, i (i)}
					<div class="mb-2 grid gap-2 sm:grid-cols-[8rem_1fr_9rem_10rem_auto]">
						<select class="input" bind:value={e.level} aria-label="Level">{#each ['associate', 'bachelor', 'master', 'mba', 'phd', 'other'] as l (l)}<option>{l}</option>{/each}</select>
						<input class="input" bind:value={e.field} placeholder="Major, e.g. Computer Science" aria-label="Major" />
						<select class="input" bind:value={e.status} aria-label="Status"><option value="in_progress">in progress</option><option value="completed">completed</option></select>
						<input class="input" type="date" bind:value={e.graduation_date} aria-label="Graduation date" />
						<button class="btn btn-sm" onclick={() => s.profile.education.splice(i, 1)} aria-label="Remove">✕</button>
					</div>
				{/each}
				<button class="btn btn-sm" onclick={() => s.profile.education.push({ level: 'bachelor', field: '', status: 'in_progress', graduation_date: null, school: '' })}>+ Degree</button>
				{#if !s.profile.education.length}<p class="mt-2 text-xs text-warn">Add at least one degree (in progress counts).</p>{/if}
			</div>
			<div>
				<h3 class="mb-2 text-sm font-medium">Roles you want</h3>
				<div class="flex flex-wrap gap-1.5">
					{#each [...new Set([...INTERESTS, ...s.profile.role_interests])] as i (i)}
						<button class="btn btn-sm {s.profile.role_interests.includes(i) ? 'btn-primary' : ''}" aria-pressed={s.profile.role_interests.includes(i)} onclick={() => toggleInterest(i)}>{i}</button>
					{/each}
				</div>
			</div>
			<div class="grid gap-3 sm:grid-cols-2">
				<label><span class="label">Where you'd work (one place per line)</span><textarea class="input h-20" value={lines(s.profile.locations)} oninput={(e) => (s.profile.locations = parse(e.currentTarget.value))} placeholder="San Francisco, CA&#10;New York, NY"></textarea></label>
				<div class="space-y-3">
					<label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={s.profile.open_to_remote} /> Open to remote roles</label>
					<label><span class="label">Work authorization</span><input class="input" bind:value={s.profile.work_authorization} placeholder="e.g. US citizen; F-1, needs sponsorship" /></label>
				</div>
			</div>
			<div class="flex justify-between">
				<button class="btn" onclick={() => step--}>Back</button>
				<button class="btn btn-primary" disabled={busy} onclick={() => saveSettings()}>Save and continue</button>
			</div>
		</section>
	{:else if step === 4}
		<section class="card space-y-4 p-6">
			<div>
				<h2 class="text-lg font-semibold">Job boards to follow</h2>
				<p class="text-sm text-muted">These companies post on public job boards AutoTailor can read. Big companies with their own careers sites (Google, Apple, …) aren't here: paste those postings with Add.</p>
			</div>
			<label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={internships} /> I'm looking for internships (keep only intern, internship and co-op titles)</label>
			<div class="space-y-2 rounded-lg border border-line p-4">
				<label class="block"><span class="label">What fields or industries interest you? Describe them in your own words.</span>
					<input class="input" bind:value={fields} placeholder="e.g. biomedical, medical devices, health data" onkeydown={(e) => e.key === 'Enter' && fields.trim() && !searching && findCompanies()} /></label>
				<div class="flex flex-wrap items-center gap-3">
					<button class="btn btn-primary" disabled={searching || fields.trim().length < 3} onclick={findCompanies}>{searching ? 'Searching…' : 'Find companies'}</button>
					<label class="flex items-center gap-2 text-sm text-muted"><input type="checkbox" bind:checked={askModel} /> Also ask the AI for more companies (slower; each is checked before it's listed)</label>
				</div>
				{#if searching}<p class="text-xs text-muted">Checking each company's job board for open jobs. This takes up to a minute{askModel ? ', longer with the AI' : ''}.</p>{/if}
				{#if found}
					<p class="text-sm">Found <b>{found.boards.length}</b> companies with job boards in: {found.industries.join(', ') || 'no matching industry'}. They're selected; untick any you don't want.</p>
					{#if found.suggest_usajobs}
						<p class="rounded-md bg-sunken p-2 text-sm">Much law, policy and government work isn't on these job boards. Federal jobs, including internships, come from <b>USAJOBS</b>: turn it on later under <a class="text-accent hover:underline" href="/settings/sources" target="_blank" rel="noreferrer">Settings › Job sources</a> (it needs a free key). Law firm and Capitol Hill postings: paste them with Add.</p>
					{/if}
					{#if found.boards.length}
						<div class="grid max-h-72 gap-1 overflow-y-auto rounded-lg border border-line p-2 sm:grid-cols-2">
							{#each found.boards as b (b.ats + b.slug)}
								<label class="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-sunken">
									<input type="checkbox" checked={chosen.has(`${b.ats}:${b.slug}`)} onchange={() => toggleBoard(`${b.ats}:${b.slug}`)} />
									<span class="min-w-0 flex-1 truncate">{b.name}</span>
									{#if b.from_model}<span class="rounded bg-sunken px-1.5 text-xs text-muted" title="Suggested by the AI, then checked">AI</span>{/if}
									<span class="text-xs text-muted tabular-nums">{b.jobs} open</span>
								</label>
							{/each}
						</div>
					{/if}
				{/if}
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<input class="input max-w-xs" placeholder="Search companies" bind:value={boardQuery} aria-label="Search companies" />
				<button class="btn btn-sm" onclick={() => (chosen = new Set([...chosen, ...shown.map((b) => `${b.ats}:${b.slug}`)]))}>Select {boardQuery ? 'these' : 'all'}</button>
				<button class="btn btn-sm" onclick={() => (chosen = new Set())}>Clear</button>
				<span class="text-sm text-muted">{chosen.size} selected</span>
			</div>
			<div class="grid max-h-80 gap-1 overflow-y-auto rounded-lg border border-line p-2 sm:grid-cols-3">
				{#each shown as b (b.ats + b.slug)}
					<label class="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-sunken"><input type="checkbox" checked={chosen.has(`${b.ats}:${b.slug}`)} onchange={() => toggleBoard(`${b.ats}:${b.slug}`)} /> {b.name}</label>
				{/each}
			</div>
			<div class="flex flex-wrap items-end gap-2">
				<label><span class="label">Another company</span><select class="input w-auto" bind:value={customAts}><option value="greenhouse">Greenhouse</option><option value="lever">Lever</option><option value="ashby">Ashby</option></select></label>
				<input class="input w-48" placeholder="slug, e.g. figma" bind:value={customSlug} aria-label="Board slug" />
				<button class="btn" disabled={!customSlug.trim()} onclick={() => { chosen = new Set([...chosen, `${customAts}:${customSlug.trim().toLowerCase()}`]); customSlug = ''; }}>Add</button>
			</div>
			<p class="text-xs text-muted">The slug is the name in the board's address, e.g. boards.greenhouse.io/<b>figma</b>.</p>
			<div class="flex justify-between">
				<button class="btn" onclick={() => step--}>Back</button>
				<button class="btn btn-primary" disabled={busy} onclick={saveSources}>Save and continue</button>
			</div>
		</section>
	{:else}
		<section class="card space-y-4 p-6">
			<h2 class="text-lg font-semibold">{status.complete ? "You're set up" : 'Almost there'}</h2>
			<ul class="space-y-1.5 text-sm">
				{#each Object.entries(status.checks) as [k, ok] (k)}
					<li class="flex items-center gap-2"><span class={ok ? 'text-good' : 'text-warn'}>{ok ? '✓' : '•'}</span> {CHECK_LABELS[k]}{#if !ok && k === 'models'}: add a key for {status.missing_keys.join(', ')}{/if}</li>
				{/each}
			</ul>
			<p class="text-sm text-muted">Running the pipeline fetches postings from your boards, screens and ranks them, and tailors resumes for the best ones. The first run can take a while; it continues in the background and shows progress in the task tray.</p>
			<div class="flex flex-wrap justify-between gap-2">
				<button class="btn" onclick={() => step--}>Back</button>
				<div class="flex gap-2">
					<button class="btn" onclick={() => finish(false)}>Go to Today</button>
					<button class="btn btn-primary" disabled={!status.complete} onclick={() => finish(true)}>Run the pipeline now</button>
				</div>
			</div>
		</section>
	{/if}
</div>
