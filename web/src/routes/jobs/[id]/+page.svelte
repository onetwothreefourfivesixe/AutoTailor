<script lang="ts">
	import { page } from '$app/state';
	import { goto, invalidateAll } from '$app/navigation';
	import { get, send, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import { date, pct, when, words } from '$lib/format';
	import { APP_LABELS, APP_STATUSES, type AiCall, type ResumeView, type Question } from '$lib/types';
	import Tabs from '$lib/components/Tabs.svelte';
	import VerdictPill from '$lib/components/VerdictPill.svelte';

	let { data } = $props();
	const job = $derived(data.job);
	const p = $derived(job.posting);
	const m = $derived(job.match);

	let tab = $state(page.url.searchParams.get('tab') ?? 'overview');
	$effect(() => {
		const url = new URL(page.url);
		if ((url.searchParams.get('tab') ?? 'overview') !== tab) {
			tab === 'overview' ? url.searchParams.delete('tab') : url.searchParams.set('tab', tab);
			goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		}
	});

	const tabs = $derived([
		{ id: 'overview', label: 'Overview' },
		{ id: 'eligibility', label: 'Eligibility' },
		{ id: 'fit', label: 'Fit' },
		{ id: 'resume', label: 'Resume' },
		{ id: 'answers', label: `Answers (${job.questions.length})` },
		{ id: 'log', label: `AI log (${job.ai_calls})` }
	]);
	const apply = $derived(p.canonical_url ?? (p.source !== 'runway' ? p.source_url : null));
	const enc = (s: string) => encodeURIComponent(s);

	async function act<T>(fn: () => Promise<T>, ok?: string) {
		try {
			await fn();
			if (ok) toasts.show(ok);
			await invalidateAll();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'That failed.', 'error');
		}
	}

	// --- eligibility override
	let ovVerdict = $state<'eligible' | 'ineligible'>('eligible');
	let ovNote = $state('');

	// --- resume (loaded when its tab opens)
	let resume = $state<ResumeView | null | undefined>(undefined);
	let draft = $state(0);
	$effect(() => {
		if (tab === 'resume' && resume === undefined && job.has_resume) {
			get<ResumeView>(`/jobs/${enc(p.id)}/resume`).then((r) => {
				resume = r;
				draft = r.history.length;
			}).catch(() => (resume = null));
		}
	});

	// --- AI log
	let calls = $state<AiCall[] | undefined>(undefined);
	$effect(() => {
		if (tab === 'log' && calls === undefined) get<AiCall[]>(`/ai-calls?item=${enc(p.id)}`).then((c) => (calls = c));
	});

	// --- answers
	let newQuestion = $state('');
	let picks = $state<Record<number, Set<number>>>({});
	let notes = $state<Record<number, string>>({});
	let edits = $state<Record<number, string>>({});
	const picked = (q: Question) => picks[q.id] ?? new Set(q.points.map((_, i) => i));
	function togglePick(q: Question, i: number) {
		const s = new Set(picked(q));
		s.has(i) ? s.delete(i) : s.add(i);
		picks[q.id] = s;
	}
</script>

<svelte:head><title>{p.title} · {p.company} · AutoTailor</title></svelte:head>

<a href="/jobs" class="text-sm text-muted hover:underline">← Jobs</a>
<div class="mt-2 mb-4 flex flex-wrap items-start justify-between gap-4">
	<div class="min-w-0">
		<h1 class="h1">{p.title}</h1>
		<p class="text-muted">{p.company}{p.location ? ` · ${p.location}` : ''}{p.posted_at ? ` · posted ${date(p.posted_at)}` : ''}</p>
		<div class="mt-2 flex flex-wrap items-center gap-2 text-sm">
			{#if m}<VerdictPill verdict={m.verdict} overridden={!!m.original_verdict} />{:else}<VerdictPill verdict="not_screened" />{/if}
			{#if m?.fit_score != null}<span class="text-muted">fit <b class="text-fg">{m.fit_score.toFixed(2)}</b></span>{/if}
			{#if p.external_score != null}<span class="text-muted">Runway {pct(p.external_score)}</span>{/if}
			{#if job.application}<span class="rounded bg-sunken px-2 py-0.5 text-xs">{APP_LABELS[job.application.status]} {date(job.application.applied_at)}</span>{/if}
			{#if job.hidden}<span class="rounded bg-off-bg px-2 py-0.5 text-xs text-off">hidden</span>{/if}
		</div>
	</div>
	<div class="flex flex-wrap gap-2">
		{#if apply}<a class="btn" href={apply} target="_blank" rel="noreferrer">Open posting ↗</a>{/if}
		<button class="btn btn-primary" disabled={!p.description} onclick={() => act(() => send('POST', '/jobs/bulk', { ids: [p.id], action: 'tailor' }), 'Tailoring started. Follow it in the task tray.')}>
			{job.has_resume ? 'Tailor again' : 'Tailor resume'}
		</button>
		<select
			class="input w-auto"
			aria-label="Application status"
			value={job.application?.status ?? ''}
			onchange={(e) => {
				const v = e.currentTarget.value;
				act(() => (v ? send('PUT', `/applications/${enc(p.id)}`, { status: v }) : send('DELETE', `/applications/${enc(p.id)}`)), v ? `Marked ${APP_LABELS[v as keyof typeof APP_LABELS].toLowerCase()}.` : 'Application removed.');
			}}
		>
			<option value="">Not applied</option>
			{#each APP_STATUSES as s (s)}<option value={s}>{APP_LABELS[s]}</option>{/each}
		</select>
		<button class="btn" onclick={() => act(() => send('PUT', `/jobs/${enc(p.id)}/hidden`, { hidden: !job.hidden }), job.hidden ? 'Unhidden.' : 'Hidden from lists.')}>{job.hidden ? 'Unhide' : 'Hide'}</button>
	</div>
</div>

<Tabs {tabs} bind:active={tab} />

<div class="mt-4">
	{#if tab === 'overview'}
		<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
			<article class="card min-w-0 p-5">
				{#if job.missing_quotes.length}
					<p class="mb-3 rounded bg-warn-bg p-2 text-xs text-warn">Quotes not found in the text (so not trusted): {job.missing_quotes.map((q) => `“${q}”`).join('; ')}</p>
				{/if}
				{#if job.highlighted}
					<p class="mb-3 text-xs text-muted">Highlighted: the quotes the requirements were read from.</p>
					<div class="leading-relaxed">{@html job.highlighted}</div>
				{:else}
					<p class="text-muted">No description stored{p.source === 'runway' ? ': this Runway role still needs its employer link (see Runway).' : '.'}</p>
				{/if}
			</article>
			<aside class="space-y-4 text-sm">
				<div class="card p-4">
					<h2 class="h2 mb-2">Eligibility</h2>
					<ul class="list-disc space-y-1 pl-4">{#each (m?.verdict_reasons ?? ['Not screened yet.']).slice(0, 4) as r (r)}<li>{r}</li>{/each}</ul>
				</div>
				{#if m?.fit}
					<div class="card p-4">
						<h2 class="h2 mb-2">Fit {m.fit_score?.toFixed(2)}</h2>
						<dl class="grid grid-cols-2 gap-y-1">
							{#each ['skills', 'experience', 'level', 'interests'] as const as k (k)}<dt class="text-muted capitalize">{k}</dt><dd class="text-right">{m.fit[k].score}/5</dd>{/each}
						</dl>
						{#if m.fit.missing.length}<p class="mt-2 text-xs text-muted">Missing: {m.fit.missing.join('; ')}</p>{/if}
					</div>
				{/if}
				<div class="card p-4">
					<h2 class="h2 mb-2">Resume</h2>
					{#if job.has_resume}<button class="text-accent hover:underline" onclick={() => (tab = 'resume')}>See the tailored resume</button>{:else}<p class="text-muted">Not tailored yet.</p>{/if}
				</div>
			</aside>
		</div>
	{:else if tab === 'eligibility'}
		<div class="grid gap-6 lg:grid-cols-2">
			<section class="card p-5">
				<h2 class="h2 mb-3">Findings</h2>
				{#if m}
					<p class="mb-2"><VerdictPill verdict={m.verdict} overridden={!!m.original_verdict} />
						{#if m.original_verdict}<span class="ml-2 text-sm text-muted">The rules said {m.original_verdict}.</span>{/if}</p>
					<ul class="list-disc space-y-1 pl-5 text-sm">{#each m.verdict_reasons as r (r)}<li>{r}</li>{/each}</ul>
				{:else}<p class="text-sm text-muted">Not screened yet.</p>{/if}
				{#if p.requirements}
					{@const r = p.requirements}
					<h2 class="h2 mt-5 mb-2">Requirements read from the posting</h2>
					<dl class="grid grid-cols-[10rem_1fr] gap-y-1 text-sm">
						{#each [['Degree levels', r.degree_levels.join(', ')], ['Undergraduates only', r.undergraduates_only ? 'yes' : ''], ['Enrollment required', r.enrollment_required == null ? '' : r.enrollment_required ? 'yes' : 'no'], ['Graduation between', r.graduation_earliest || r.graduation_latest ? `${r.graduation_earliest ?? '…'} and ${r.graduation_latest ?? '…'}` : ''], ['Majors', r.majors.join(', ')], ['Work authorization', r.work_authorization ?? '']].filter(([, v]) => v) as [k, v] (k)}
							<dt class="text-muted">{k}</dt><dd>{v}</dd>
						{:else}<dd class="col-span-2 text-muted">Nothing stated.</dd>{/each}
					</dl>
					{#if r.evidence.length}
						<h3 class="mt-4 mb-1 text-xs font-medium text-muted">Evidence</h3>
						<ul class="space-y-1 text-sm">{#each r.evidence as e (e.field + e.quote)}<li><span class="text-muted">{e.field}:</span> “{e.quote}”</li>{/each}</ul>
					{/if}
				{/if}
			</section>
			<section class="card p-5">
				<h2 class="h2 mb-1">Override</h2>
				<p class="mb-3 text-sm text-muted">When the model or a rule got it wrong. Overrides win everywhere and survive re-screening.</p>
				<div class="flex flex-wrap gap-2">
					<select class="input w-auto" bind:value={ovVerdict} aria-label="Verdict"><option value="eligible">Eligible</option><option value="ineligible">Ineligible</option></select>
					<input class="input min-w-0 flex-1" bind:value={ovNote} placeholder="Why (e.g. the recruiter confirmed first-years can apply)" aria-label="Why" />
					<button class="btn btn-primary" onclick={() => act(() => send('PUT', `/postings/${enc(p.id)}/override`, { verdict: ovVerdict, note: ovNote }), 'Overridden.')}>Override</button>
				</div>
				{#if m?.original_verdict}
					<button class="btn mt-3" onclick={() => act(() => send('DELETE', `/postings/${enc(p.id)}/override`), 'Override removed.')}>Remove override</button>
				{/if}
			</section>
		</div>
	{:else if tab === 'fit'}
		{#if !m?.fit}
			<p class="card p-5 text-muted">No fit score yet. Rank to score it.</p>
		{:else}
			<div class="grid gap-4 md:grid-cols-2">
				{#each ['skills', 'experience', 'level', 'interests'] as const as k (k)}
					<div class="card p-4">
						<div class="mb-1 flex justify-between"><h2 class="font-semibold capitalize">{k}</h2><span class="tabular-nums">{m.fit[k].score}/5</span></div>
						<p class="text-sm text-muted">{m.fit[k].reason}</p>
					</div>
				{/each}
			</div>
			{#if m.fit.missing.length}<div class="card mt-4 p-4 text-sm"><b>Missing:</b> {m.fit.missing.join('; ')}</div>{/if}
			{#if job.cited_bullets.length}
				<div class="card mt-4 p-4">
					<h2 class="h2 mb-2">Most relevant CV bullets</h2>
					<ul class="space-y-1.5 text-sm">
						{#each job.cited_bullets as b (b.id)}
							<li><a href="/cv#{b.id}" class="font-mono text-xs text-accent hover:underline">{b.id}</a> {b.text ?? '(no longer in the CV)'}</li>
						{/each}
					</ul>
				</div>
			{/if}
		{/if}
	{:else if tab === 'resume'}
		{#if !job.has_resume}
			<p class="card p-5 text-muted">Not tailored yet. Use “Tailor resume” above.</p>
		{:else if resume === undefined}
			<p class="text-muted">Loading…</p>
		{:else if resume === null}
			<p class="card p-5 text-muted">The saved resume files are missing.</p>
		{:else}
			<div class="grid gap-6 xl:grid-cols-[minmax(0,34rem)_1fr]">
				<div>
					<img src="/api/postings/{enc(p.id)}/resume.png" alt="The tailored resume" class="w-full rounded-lg border border-line bg-white shadow-sm" />
					<div class="mt-2 flex flex-wrap items-center gap-2 text-sm">
						{#if resume.pdf}<a class="btn btn-sm" href="/api/files/{resume.pdf}">Download PDF</a>{/if}
						{#if resume.docx}<a class="btn btn-sm" href="/api/files/{resume.docx}">Download Word</a>{/if}
						<span class="text-muted">Run {resume.run_id} · {resume.model} · {resume.pages} page(s)</span>
					</div>
				</div>
				<div class="space-y-4">
					{#if resume.keyword_score != null}
						<section class="card p-4">
							<div class="mb-2 flex items-baseline justify-between">
								<h2 class="h2">Keyword match</h2>
								<span class="text-lg font-semibold tabular-nums {resume.keyword_score >= 0.8 ? 'text-good' : resume.keyword_score >= 0.6 ? 'text-warn' : 'text-danger'}">{Math.round(resume.keyword_score * 100)}%</span>
							</div>
							<p class="mb-2 text-xs text-muted">How many of the posting's key terms appear on the resume. Applicant-tracking systems look for these; 80% or more is a strong match.</p>
							{#if resume.keywords_matched.length}<p class="text-sm"><span class="text-muted">On the resume:</span> {resume.keywords_matched.join(', ')}</p>{/if}
							{#if resume.keywords_missing.length}
								<p class="mt-1 text-sm"><span class="text-muted">Missing:</span> {resume.keywords_missing.join(', ')}</p>
								<p class="mt-1 text-xs text-muted">If one of these is true for you, add it to your <a class="text-accent hover:underline" href="/cv">Master CV</a> and tailor again. AutoTailor won't add it on its own.</p>
							{/if}
						</section>
					{/if}
					<section class="card p-4">
						<h2 class="h2 mb-2">Against the master CV</h2>
						<p class="mb-3 text-xs text-muted">Words the model changed are underlined; the CV wording is below each bullet.{resume.added_to_fill.length ? ` ${resume.added_to_fill.length} bullet(s) were added word for word to fill the page.` : ''}{resume.removed_to_fit.length ? ` ${resume.removed_to_fit.length} were dropped to fit one page.` : ''}</p>
						<ul class="space-y-3 text-sm">
							{#each resume.bullets as b (b.source_id)}
								<li>
									<span class="font-mono text-xs text-muted">{b.source_id}</span>
									{#if resume.added_to_fill.includes(b.source_id)}<span class="ml-1 rounded bg-sunken px-1.5 text-xs text-muted" title="Added word for word from your CV to fill the page">added to fill the page</span>{/if}
									<div>{#if b.changed}{@html b.html}{:else}{b.tailored} <span class="text-xs text-muted">(unchanged)</span>{/if}</div>
									{#if b.changed && b.original}<div class="text-xs text-muted">CV: {b.original}</div>{/if}
								</li>
							{/each}
						</ul>
						{#if resume.skills.length}<p class="mt-3 text-sm"><b>Skills:</b> {resume.skills.join(', ')}</p>{/if}
					</section>
					{#if resume.history.length}
						{@const h = resume.history[Math.max(0, draft - 1)]}
						<section class="card p-4">
							<h2 class="h2 mb-2">Drafts</h2>
							<input type="range" min="1" max={resume.history.length} bind:value={draft} class="w-full" aria-label="Draft" />
							<p class="mt-1 text-sm">Draft {draft} of {resume.history.length}: {h.note ? h.note : h.errors.length ? `${h.errors.length} problem(s)` : 'passed'}</p>
							<p class="mt-1 text-xs text-muted">Bullets: {h.bullets.join(', ')}</p>
							{#if h.errors.length}<ul class="mt-2 list-disc space-y-1 pl-5 text-sm text-warn">{#each h.errors as e (e)}<li>{e}</li>{/each}</ul>{/if}
						</section>
					{/if}
				</div>
			</div>
		{/if}
	{:else if tab === 'answers'}
		<div class="space-y-4">
			<form class="card flex flex-wrap gap-2 p-4" onsubmit={(e) => { e.preventDefault(); if (newQuestion.trim()) act(() => send('POST', '/questions', { posting_id: p.id, text: newQuestion }).then(() => (newQuestion = '')), 'Question added.'); }}>
				<input class="input min-w-0 flex-1" bind:value={newQuestion} placeholder="Add a question from the application, with its limit, e.g. “Why us? (150 words)”" aria-label="New question" />
				<button class="btn btn-primary">Add</button>
			</form>
			{#each job.questions as q (q.id)}
				<section class="card p-4">
					<div class="mb-2 flex flex-wrap items-start justify-between gap-2">
						<div>
							<h2 class="font-medium">{q.text}</h2>
							<p class="text-xs text-muted">{q.status}{q.word_limit ? ` · limit ${q.word_limit} words` : ''}</p>
						</div>
						<div class="flex gap-2">
							<button class="btn btn-sm" onclick={() => act(() => send('POST', `/questions/${q.id}/suggest`), 'Suggesting points…')}>{q.status === 'new' ? 'Suggest points' : 'Start over'}</button>
							<button class="btn btn-sm btn-danger" onclick={() => confirm('Delete this question?') && act(() => send('DELETE', `/questions/${q.id}`))}>Delete</button>
						</div>
					</div>
					{#if q.points.length}
						<ul class="mb-3 space-y-1.5 text-sm">
							{#each q.points as pt, i (pt.source_id)}
								<li class="flex gap-2">
									{#if q.status === 'waiting'}<input type="checkbox" checked={picked(q).has(i)} onchange={() => togglePick(q, i)} aria-label="Use this point" />{/if}
									<span>{pt.point} <span class="text-xs text-muted">· {pt.why} · <span class="font-mono">{pt.source_id}</span></span></span>
								</li>
							{/each}
						</ul>
					{/if}
					{#if q.status === 'waiting'}
						<div class="flex flex-wrap gap-2">
							<input class="input min-w-0 flex-1" placeholder="Anything to add or stress (optional)" bind:value={notes[q.id]} aria-label="Notes" />
							<button class="btn btn-primary" onclick={() => act(() => send('POST', `/questions/${q.id}/decision`, { draft: true, use: [...picked(q)].map((i) => i + 1), notes: notes[q.id] ?? '' }), 'Drafting and humanizing…')}>Draft from these</button>
							<button class="btn" onclick={() => act(() => send('POST', `/questions/${q.id}/decision`, { draft: false }), 'Kept the points.')}>Points are enough</button>
						</div>
					{/if}
					{#if q.answer !== null && q.answer !== undefined}
						{@const text = edits[q.id] ?? q.answer}
						<textarea class="input h-40" value={text} oninput={(e) => (edits[q.id] = e.currentTarget.value)} aria-label="Answer"></textarea>
						<div class="mt-1 flex items-center justify-between text-xs">
							<span class={q.word_limit && words(text) > q.word_limit ? 'text-danger' : 'text-muted'}>{words(text)} words{q.word_limit ? ` of ${q.word_limit}` : ''}{q.model ? ` · drafted by ${q.model}` : ''}</span>
							{#if edits[q.id] !== undefined && edits[q.id] !== q.answer}
								<button class="btn btn-sm btn-primary" onclick={() => act(() => send('PATCH', `/questions/${q.id}`, { answer: edits[q.id] }).then(() => delete edits[q.id]), 'Saved.')}>Save edit</button>
							{/if}
						</div>
						{#each q.problems as pr (pr)}<p class="mt-1 text-xs text-warn">• {pr}</p>{/each}
					{/if}
				</section>
			{:else}
				<p class="text-sm text-muted">No questions yet.</p>
			{/each}
		</div>
	{:else if tab === 'log'}
		{#if calls === undefined}
			<p class="text-muted">Loading…</p>
		{:else if calls.length === 0}
			<p class="card p-5 text-muted">No model calls logged for this job (they're kept for the days set in Settings › AI log).</p>
		{:else}
			<ul class="space-y-2">
				{#each calls as c (c.id)}
					<li class="card">
						<details>
							<summary class="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
								{#if c.error}<span class="text-danger">failed</span>{/if}
								<span class="font-medium">{c.step}</span><span class="font-mono text-xs">{c.model}</span>
								<span class="text-xs {c.local ? 'text-muted' : 'text-cloud'}">{c.local ? 'on this machine' : `sent to ${c.provider}`}</span>
								<span class="text-xs text-muted">{(c.ms / 1000).toFixed(1)} s{c.tokens_in ? ` · ${c.tokens_in}→${c.tokens_out} tokens` : ''} · {when(c.at)}</span>
							</summary>
							<div class="space-y-3 border-t border-line p-3 text-xs">
								{#if c.error}<p class="text-danger">{c.error}</p>{/if}
								{#each JSON.parse(c.messages ?? '[]') as msg, i (i)}
									<div><div class="mb-1 font-semibold">{msg.role}</div><pre class="max-h-72 overflow-auto rounded bg-sunken p-2 whitespace-pre-wrap">{msg.content}</pre></div>
								{/each}
								{#if c.response}<div><div class="mb-1 font-semibold">response</div><pre class="max-h-72 overflow-auto rounded bg-sunken p-2 whitespace-pre-wrap">{c.response}</pre></div>{/if}
							</div>
						</details>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>
