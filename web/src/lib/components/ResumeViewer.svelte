<script lang="ts">
	// The tailored resume full size, and a full editor for final changes: the header, every
	// section (rename, reorder, add, remove), every entry (title, organization, place, dates, tech
	// stack), every bullet (reword, write new ones, insert from the CV) and the skills. A live
	// preview re-renders as you type; saving renders the PDF and Word files again, and the
	// tailored version can always be restored.
	import { send, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import type { EditableLayout, KeywordProposal, ResumeView } from '$lib/types';

	let {
		open = $bindable(false),
		proposal = $bindable(null),
		postingId,
		resume,
		version,
		onsaved
	}: { open?: boolean; proposal?: KeywordProposal | null; postingId: string; resume: ResumeView; version: number; onsaved: () => void } = $props();

	// Keywords the AI added (from the Resume tab): opened in the editor for review, unsaved.
	let changes = $state<KeywordProposal['changes']>([]);
	let skipped = $state<KeywordProposal['skipped']>([]);
	$effect(() => {
		if (!open || !proposal) return;
		const pr = proposal;
		proposal = null;
		startEditing(pr.layout);
		changes = pr.changes;
		skipped = pr.skipped;
		start = JSON.stringify(resume.layout); // so the proposal counts as unsaved
	});

	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});

	const enc = (s: string) => encodeURIComponent(s);
	let zoom = $state(false);
	let editing = $state(false);
	let saving = $state(false);
	let warnings = $state<string[]>([]);
	let draft = $state<EditableLayout | null>(null);
	let start = '';
	const dirty = $derived(editing && draft !== null && JSON.stringify(draft) !== start);

	function startEditing(from?: EditableLayout) {
		if (!resume.layout) return toasts.show("This resume can't be edited (the master CV didn't load).", 'error');
		draft = structuredClone($state.snapshot(from ?? resume.layout)) as EditableLayout;
		start = JSON.stringify(draft);
		warnings = [];
		changes = [];
		skipped = [];
		openPanels = new Set(['header']);
		editing = true;
	}

	// --- live preview ------------------------------------------------------------------------------
	let preview = $state<string | null>(null);
	let previewPages = $state<number | null>(null);
	let previewing = $state(false);
	let previewError = $state<string | null>(null);
	let seq = 0;
	let timer: ReturnType<typeof setTimeout>;
	$effect(() => {
		if (!editing || !draft) return;
		const body = JSON.stringify(draft); // tracks every field
		clearTimeout(timer);
		timer = setTimeout(() => render(body), 900);
	});
	let inflight: AbortController | null = null;
	async function render(body: string) {
		const mine = ++seq;
		inflight?.abort(); // a newer edit makes the previous preview pointless: don't wait for it
		const ctrl = (inflight = new AbortController());
		previewing = true;
		try {
			const res = await fetch(`/api/jobs/${enc(postingId)}/resume/preview`, { method: 'POST', headers: { 'content-type': 'application/json' }, body, signal: ctrl.signal });
			if (mine !== seq) return;
			if (!res.ok) {
				previewError = (await res.json().catch(() => ({}))).detail ?? 'The preview failed.';
				return;
			}
			const url = URL.createObjectURL(await res.blob());
			if (preview) URL.revokeObjectURL(preview);
			preview = url;
			previewPages = Number(res.headers.get('x-pages'));
			previewError = null;
		} catch (e) {
			if ((e as Error).name !== 'AbortError' && mine === seq) previewError = "The preview couldn't be made; your edits are still here.";
		} finally {
			if (mine === seq) previewing = false;
		}
	}

	// --- editing helpers ---------------------------------------------------------------------------
	let openPanels = $state<Set<string>>(new Set(['header']));
	const toggle = (id: string) => {
		const s = new Set(openPanels);
		s.has(id) ? s.delete(id) : s.add(id);
		openPanels = s;
	};
	function move<T>(list: T[], i: number, by: number) {
		const j = i + by;
		if (j < 0 || j >= list.length) return;
		list.splice(j, 0, ...list.splice(i, 1));
	}
	const blankItem = () => ({ title: '', organization: '', location: '', dates: '', stack: '', bullets: [''] });
	const byEntry = $derived(
		resume.cv_bullets.reduce<Record<string, typeof resume.cv_bullets>>((acc, b) => ((acc[b.entry] ??= []).push(b), acc), {})
	);
	const onPage = $derived(new Set(draft?.sections.flatMap((s) => s.items.flatMap((i) => i.bullets)) ?? []));

	async function save() {
		if (!draft) return;
		saving = true;
		try {
			const r = await send<{ pages: number; warnings: string[] }>('PUT', `/jobs/${enc(postingId)}/resume/layout`, draft);
			warnings = r.warnings;
			start = JSON.stringify(draft);
			toasts.show(r.pages > 1 ? `Saved, but it runs to ${r.pages} pages.` : 'Saved. The PDF and Word files are updated.', r.pages > 1 ? 'error' : undefined);
			onsaved();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'Saving failed.', 'error');
		} finally {
			saving = false;
		}
	}

	async function restore() {
		if (!confirm('Undo all your edits and go back to the resume as AutoTailor tailored it?')) return;
		saving = true;
		try {
			await send('POST', `/jobs/${enc(postingId)}/resume/restore`);
			toasts.show('Restored the tailored version.');
			editing = false;
			warnings = [];
			onsaved();
		} catch (e) {
			toasts.show(e instanceof ApiError ? e.message : 'That failed.', 'error');
		} finally {
			saving = false;
		}
	}

	function stopEditing() {
		if (dirty && !confirm('Discard your unsaved edits?')) return false;
		editing = false;
		return true;
	}
	function close() {
		if (editing && !stopEditing()) return;
		open = false;
	}
</script>

<dialog
	bind:this={dialog}
	onclose={() => { editing = false; open = false; }}
	oncancel={(e) => { e.preventDefault(); close(); }}
	class="m-auto h-[calc(100vh-2rem)] max-h-none w-[calc(100vw-2rem)] max-w-none rounded-xl border border-line bg-surface p-0 text-fg shadow-2xl backdrop:bg-black/60"
	aria-label="Resume"
>
	<div class="flex h-full flex-col">
		<div class="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
			<h2 class="mr-auto font-semibold">Resume{resume.edited ? ' (edited by you)' : ''}</h2>
			{#if editing}
				<span class="text-sm {previewPages && previewPages > 1 ? 'font-medium text-danger' : 'text-muted'}" aria-live="polite">
					{previewing ? 'Updating preview…' : previewError ? previewError : previewPages ? `${previewPages} page${previewPages > 1 ? 's' : ''}` : ''}
				</span>
			{/if}
			<button class="btn btn-sm" onclick={() => (zoom = !zoom)} aria-pressed={zoom}>{zoom ? 'Fit to width' : 'Actual size'}</button>
			{#if resume.pdf}<a class="btn btn-sm" href="/api/files/{resume.pdf}">PDF</a>{/if}
			{#if resume.docx}<a class="btn btn-sm" href="/api/files/{resume.docx}">Word</a>{/if}
			{#if !editing}<button class="btn btn-sm btn-primary" onclick={() => startEditing()}>Edit</button>{/if}
			<button class="btn btn-sm" onclick={close} aria-label="Close">Esc</button>
		</div>
		<div class="flex min-h-0 flex-1 flex-col lg:flex-row">
			<div class="min-h-0 flex-1 overflow-auto bg-sunken p-4">
				<img
					src={editing && preview ? preview : `/api/postings/${enc(postingId)}/resume.png?scale=2.6&v=${version}`}
					alt={editing ? 'Live preview of your edits' : 'The tailored resume, full size'}
					class="mx-auto rounded border border-line bg-white shadow transition-opacity {zoom ? 'max-w-none' : 'w-full max-w-5xl'} {editing && previewing ? 'opacity-70' : ''}"
				/>
			</div>
			{#if editing && draft}
				<aside class="flex min-h-0 flex-col border-t border-line lg:w-[34rem] lg:border-t-0 lg:border-l" aria-label="Edit the resume">
					<div class="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-sm">
						<p class="text-xs text-muted">Everything on the page can be changed here, for this job only (your Master CV stays as it is). The preview updates as you type; nothing is saved until you press Save.</p>
						{#if changes.length || skipped.length}
							<section class="rounded-lg border border-accent p-3" aria-label="Keyword changes">
								<h3 class="mb-1 font-medium">Keyword changes: review, then save</h3>
								<ul class="space-y-2">
									{#each changes as c (c.term)}
										<li>
											<span class="rounded bg-accent px-1.5 text-xs text-white">{c.term}</span> <span class="text-xs text-muted">{c.where}</span>
											<div class="mt-0.5 text-xs"><span class="text-muted line-through">{c.before || '(empty)'}</span></div>
											<div class="text-xs">{c.after}</div>
											{#if c.note}<div class="text-xs text-warn">{c.note}</div>{/if}
										</li>
									{/each}
									{#each skipped as s (s.term)}<li class="text-xs text-muted"><b>{s.term}</b>: {s.reason}</li>{/each}
								</ul>
							</section>
						{/if}

						<!-- header -->
						<section class="rounded-lg border border-line">
							<button class="flex w-full items-center justify-between px-3 py-2 font-medium" onclick={() => toggle('header')} aria-expanded={openPanels.has('header')}>Header <span class="text-muted">{openPanels.has('header') ? '▾' : '▸'}</span></button>
							{#if openPanels.has('header')}
								<div class="space-y-2 border-t border-line p-3">
									<label class="block"><span class="label">Name</span><input class="input" bind:value={draft.name} /></label>
									<span class="label">Contact line</span>
									{#each draft.contact_items as c, i (i)}
										<div class="flex gap-1">
											<input class="input min-w-0 flex-1" bind:value={c.text} placeholder="Shown text" aria-label="Contact text" />
											<input class="input min-w-0 flex-1" bind:value={c.url} placeholder="Link (optional)" aria-label="Contact link" />
											<button class="btn btn-sm" onclick={() => move(draft!.contact_items, i, -1)} disabled={i === 0} aria-label="Move contact up">↑</button>
											<button class="btn btn-sm" onclick={() => draft!.contact_items.splice(i, 1)} aria-label="Remove contact">✕</button>
										</div>
									{/each}
									<button class="btn btn-sm" onclick={() => draft!.contact_items.push({ text: '', url: null })}>+ Contact item</button>
								</div>
							{/if}
						</section>

						<!-- sections -->
						{#each draft.sections as s, si (si)}
							{@const id = `s${si}`}
							<section class="rounded-lg border border-line">
								<div class="flex items-center gap-1 px-3 py-2">
									<button class="min-w-0 flex-1 truncate text-left font-medium" onclick={() => toggle(id)} aria-expanded={openPanels.has(id)}>{openPanels.has(id) ? '▾' : '▸'} {s.title || 'Untitled section'} <span class="text-xs font-normal text-muted">({s.items.length})</span></button>
									<button class="btn btn-sm" onclick={() => move(draft!.sections, si, -1)} disabled={si === 0} aria-label="Move section up">↑</button>
									<button class="btn btn-sm" onclick={() => move(draft!.sections, si, 1)} disabled={si === draft!.sections.length - 1} aria-label="Move section down">↓</button>
									<button class="btn btn-sm" onclick={() => confirm(`Remove the whole “${s.title}” section?`) && draft!.sections.splice(si, 1)} aria-label="Remove section">✕</button>
								</div>
								{#if openPanels.has(id)}
									<div class="space-y-3 border-t border-line p-3">
										<label class="block"><span class="label">Section title</span><input class="input" bind:value={s.title} aria-label="Section title" /></label>
										{#each s.items as it, ii (ii)}
											<div class="rounded-md border border-line p-2">
												<div class="mb-2 flex items-center gap-1 text-xs text-muted">
													<span class="mr-auto truncate">{it.organization || it.title || 'New entry'}</span>
													<button class="btn btn-sm" onclick={() => move(s.items, ii, -1)} disabled={ii === 0} aria-label="Move entry up">↑</button>
													<button class="btn btn-sm" onclick={() => move(s.items, ii, 1)} disabled={ii === s.items.length - 1} aria-label="Move entry down">↓</button>
													<button class="btn btn-sm" onclick={() => s.items.splice(ii, 1)} aria-label="Remove entry">✕</button>
												</div>
												<div class="grid gap-1.5 sm:grid-cols-2">
													<input class="input" bind:value={it.title} placeholder={s.key === 'education' ? 'Degree' : 'Title or role'} aria-label="Title" />
													<input class="input" bind:value={it.organization} placeholder={s.key === 'education' ? 'School' : 'Organization (optional)'} aria-label="Organization" />
													<input class="input" bind:value={it.location} placeholder="Location (optional)" aria-label="Location" />
													<input class="input" bind:value={it.dates} placeholder="Dates (optional)" aria-label="Dates" />
													<input class="input sm:col-span-2" bind:value={it.stack} placeholder="Tech stack, shown after the title (optional)" aria-label="Tech stack" />
												</div>
												<ul class="mt-2 space-y-1.5">
													{#each it.bullets as _, bi (bi)}
														<li class="flex items-start gap-1">
															<textarea class="input min-h-12 flex-1 text-sm" bind:value={it.bullets[bi]} aria-label="Bullet"></textarea>
															<div class="flex flex-col gap-0.5">
																<button class="btn btn-sm px-1.5 py-0" onclick={() => move(it.bullets, bi, -1)} disabled={bi === 0} aria-label="Move bullet up">↑</button>
																<button class="btn btn-sm px-1.5 py-0" onclick={() => move(it.bullets, bi, 1)} disabled={bi === it.bullets.length - 1} aria-label="Move bullet down">↓</button>
																<button class="btn btn-sm px-1.5 py-0" onclick={() => it.bullets.splice(bi, 1)} aria-label="Remove bullet">✕</button>
															</div>
														</li>
													{/each}
												</ul>
												<div class="mt-1.5 flex flex-wrap gap-1.5">
													<button class="btn btn-sm" onclick={() => it.bullets.push('')}>+ Bullet</button>
													<select class="input w-auto max-w-full py-0.5 text-xs" aria-label="Insert a bullet from your CV" onchange={(e) => { const t = e.currentTarget.value; if (t) it.bullets.push(t); e.currentTarget.value = ''; }}>
														<option value="">+ From your CV…</option>
														{#each Object.entries(byEntry) as [entry, list] (entry)}
															<optgroup label={entry}>
																{#each list.filter((b) => !onPage.has(b.text)) as b (b.id)}<option value={b.text}>{b.text.length > 80 ? b.text.slice(0, 80) + '…' : b.text}</option>{/each}
															</optgroup>
														{/each}
													</select>
												</div>
											</div>
										{/each}
										<button class="btn btn-sm" onclick={() => s.items.push(blankItem())}>+ Entry</button>
									</div>
								{/if}
							</section>
						{/each}
						<button class="btn btn-sm" onclick={() => { draft!.sections.push({ key: 'custom', title: 'New section', items: [blankItem()] }); openPanels = new Set([...openPanels, `s${draft!.sections.length - 1}`]); }}>+ Section</button>

						<!-- skills -->
						<section class="rounded-lg border border-line">
							<button class="flex w-full items-center justify-between px-3 py-2 font-medium" onclick={() => toggle('skills')} aria-expanded={openPanels.has('skills')}>Skills <span class="text-muted">{openPanels.has('skills') ? '▾' : '▸'}</span></button>
							{#if openPanels.has('skills')}
								<div class="space-y-1.5 border-t border-line p-3">
									{#each draft.skills as g, gi (gi)}
										<div class="flex gap-1">
											<input class="input w-36 shrink-0" bind:value={g.name} placeholder="Group" aria-label="Skill group name" />
											<input class="input min-w-0 flex-1" bind:value={g.items} placeholder="Python, SQL, …" aria-label="Skills in this group" />
											<button class="btn btn-sm" onclick={() => move(draft!.skills, gi, -1)} disabled={gi === 0} aria-label="Move group up">↑</button>
											<button class="btn btn-sm" onclick={() => draft!.skills.splice(gi, 1)} aria-label="Remove group">✕</button>
										</div>
									{/each}
									<button class="btn btn-sm" onclick={() => draft!.skills.push({ name: '', items: '' })}>+ Group</button>
								</div>
							{/if}
						</section>

						{#if warnings.length}
							<ul class="list-disc space-y-1 rounded-md bg-sunken p-3 pl-6 text-xs text-warn" aria-label="Notes on your edits">{#each warnings as w (w)}<li>{w}</li>{/each}</ul>
						{/if}
					</div>
					<div class="flex flex-wrap items-center gap-2 border-t border-line p-3">
						<button class="btn btn-primary" disabled={saving || !dirty} onclick={save}>{saving ? 'Saving…' : 'Save and re-render'}</button>
						<button class="btn" disabled={saving} onclick={stopEditing}>{dirty ? 'Discard' : 'Done'}</button>
						{#if resume.edited}<button class="btn ml-auto" disabled={saving} onclick={restore}>Restore tailored version</button>{/if}
					</div>
				</aside>
			{/if}
		</div>
	</div>
</dialog>
