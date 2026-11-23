<script lang="ts">
	// A full-screen view of a tailored resume, with a panel for small hand edits: reorder,
	// remove or reword bullets, add bullets from the CV, and pick the skills listed first.
	// Saving renders the PDF and Word files again; the tailored version can be restored.
	import { send, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import type { ResumeView } from '$lib/types';

	let {
		open = $bindable(false),
		postingId,
		resume,
		version,
		onsaved
	}: { open?: boolean; postingId: string; resume: ResumeView; version: number; onsaved: () => void } = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});

	const enc = (s: string) => encodeURIComponent(s);
	let zoom = $state(false); // false: fit the width of the view; true: actual size, scroll to read
	let editing = $state(false);
	let saving = $state(false);
	let warnings = $state<string[]>([]);

	type Row = { source_id: string; text: string };
	let rows = $state<Row[]>([]);
	let skills = $state<string[]>([]);
	let addBullet = $state('');
	let addSkill = $state('');

	function startEditing() {
		rows = resume.bullets.map((b) => ({ source_id: b.source_id, text: b.tailored }));
		skills = [...resume.skills];
		warnings = [];
		editing = true;
	}

	const entryOf = $derived(Object.fromEntries(resume.cv_bullets.map((b) => [b.id, b.entry])));
	const unused = $derived(resume.cv_bullets.filter((b) => !rows.some((r) => r.source_id === b.id)));
	const unusedByEntry = $derived(
		unused.reduce<Record<string, typeof unused>>((acc, b) => ((acc[b.entry] ??= []).push(b), acc), {})
	);
	const allSkills = $derived(resume.cv_skills.flatMap((g) => g.items));
	const original = (id: string) => resume.cv_bullets.find((b) => b.id === id)?.text;
	const dirty = $derived(
		editing &&
			(JSON.stringify(rows) !== JSON.stringify(resume.bullets.map((b) => ({ source_id: b.source_id, text: b.tailored }))) ||
				JSON.stringify(skills) !== JSON.stringify(resume.skills))
	);

	function move(list: unknown[], i: number, by: number) {
		const j = i + by;
		if (j < 0 || j >= list.length) return;
		list.splice(j, 0, ...list.splice(i, 1));
	}

	async function save() {
		saving = true;
		try {
			const r = await send<{ pages: number; warnings: string[] }>('PUT', `/jobs/${enc(postingId)}/resume`, { bullets: rows, skills });
			warnings = r.warnings;
			toasts.show(r.pages > 1 ? `Saved, but it now runs to ${r.pages} pages. Remove a bullet or two.` : 'Saved. The PDF and Word files are updated.', r.pages > 1 ? 'error' : undefined);
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

	function close() {
		if (dirty && !confirm('Discard your unsaved edits?')) return;
		editing = false;
		open = false;
	}
</script>

<dialog
	bind:this={dialog}
	onclose={() => { editing = false; open = false; }}
	oncancel={(e) => { if (dirty) { e.preventDefault(); close(); } }}
	class="m-auto h-[calc(100vh-2rem)] max-h-none w-[calc(100vw-2rem)] max-w-none rounded-xl border border-line bg-surface p-0 text-fg shadow-2xl backdrop:bg-black/60"
	aria-label="Resume"
>
	<div class="flex h-full flex-col">
		<div class="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
			<h2 class="mr-auto font-semibold">Resume{resume.edited ? ' (edited by you)' : ''}</h2>
			<button class="btn btn-sm" onclick={() => (zoom = !zoom)} aria-pressed={zoom}>{zoom ? 'Fit to width' : 'Actual size'}</button>
			{#if resume.pdf}<a class="btn btn-sm" href="/api/files/{resume.pdf}">PDF</a>{/if}
			{#if resume.docx}<a class="btn btn-sm" href="/api/files/{resume.docx}">Word</a>{/if}
			{#if !editing}<button class="btn btn-sm btn-primary" onclick={startEditing}>Edit</button>{/if}
			<button class="btn btn-sm" onclick={close} aria-label="Close">Esc</button>
		</div>
		<div class="flex min-h-0 flex-1 flex-col lg:flex-row">
			<div class="min-h-0 flex-1 overflow-auto bg-sunken p-4">
				<img
					src="/api/postings/{enc(postingId)}/resume.png?scale=2.6&v={version}"
					alt="The tailored resume, full size"
					class="mx-auto rounded border border-line bg-white shadow {zoom ? 'max-w-none' : 'w-full max-w-5xl'}"
				/>
			</div>
			{#if editing}
				<aside class="flex min-h-0 flex-col border-t border-line lg:w-[30rem] lg:border-t-0 lg:border-l" aria-label="Edit the resume">
					<div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-sm">
						<p class="text-xs text-muted">Changes apply to this job's resume only; your Master CV stays as it is. A bullet higher in the list moves its job or project up. Saving keeps your choices as they are (no page filling).</p>
						<ol class="space-y-3">
							{#each rows as r, i (r.source_id)}
								<li class="rounded-md border border-line p-2">
									<div class="mb-1 flex items-center gap-1 text-xs text-muted">
										<span class="mr-auto min-w-0 truncate" title={r.source_id}>{entryOf[r.source_id] ?? r.source_id}</span>
										<button class="btn btn-sm" disabled={i === 0} onclick={() => move(rows, i, -1)} aria-label="Move up">↑</button>
										<button class="btn btn-sm" disabled={i === rows.length - 1} onclick={() => move(rows, i, 1)} aria-label="Move down">↓</button>
										<button class="btn btn-sm" onclick={() => rows.splice(i, 1)} aria-label="Remove bullet">✕</button>
									</div>
									<textarea class="input min-h-16 text-sm" bind:value={r.text} aria-label="Bullet text"></textarea>
									{#if original(r.source_id) && r.text !== original(r.source_id)}
										<div class="mt-1 flex items-start gap-2 text-xs text-muted">
											<span class="min-w-0 flex-1">CV: {original(r.source_id)}</span>
											<button class="text-accent hover:underline" onclick={() => (r.text = original(r.source_id) ?? r.text)}>Use CV wording</button>
										</div>
									{/if}
								</li>
							{/each}
						</ol>
						<label class="block">
							<span class="label">Add a bullet from your CV</span>
							<select class="input" bind:value={addBullet} onchange={() => { const b = unused.find((x) => x.id === addBullet); if (b) rows.push({ source_id: b.id, text: b.text }); addBullet = ''; }}>
								<option value="">Choose…</option>
								{#each Object.entries(unusedByEntry) as [entry, list] (entry)}
									<optgroup label={entry}>
										{#each list as b (b.id)}<option value={b.id}>{b.text.length > 90 ? b.text.slice(0, 90) + '…' : b.text}</option>{/each}
									</optgroup>
								{/each}
							</select>
						</label>
						<div>
							<span class="label">Skills listed first</span>
							<div class="flex flex-wrap gap-1">
								{#each skills as s, i (s)}
									<span class="inline-flex items-center gap-1 rounded bg-sunken px-2 py-0.5">
										<button class="text-muted" disabled={i === 0} onclick={() => move(skills, i, -1)} aria-label="Move {s} earlier">‹</button>
										{s}
										<button class="text-muted" onclick={() => skills.splice(i, 1)} aria-label="Remove {s}">✕</button>
									</span>
								{/each}
							</div>
							<select class="input mt-2" bind:value={addSkill} onchange={() => { if (addSkill) skills.push(addSkill); addSkill = ''; }} aria-label="Add a skill">
								<option value="">Add a skill…</option>
								{#each allSkills.filter((s) => !skills.includes(s)) as s (s)}<option value={s}>{s}</option>{/each}
							</select>
							<p class="mt-1 text-xs text-muted">Depending on Settings › Skills section, these come first in each group, or are the only ones shown.</p>
						</div>
						{#if warnings.length}
							<ul class="list-disc space-y-1 rounded-md bg-sunken p-3 pl-6 text-xs text-warn">{#each warnings as w (w)}<li>{w}</li>{/each}</ul>
						{/if}
					</div>
					<div class="flex flex-wrap items-center gap-2 border-t border-line p-3">
						<button class="btn btn-primary" disabled={saving || !dirty || !rows.length} onclick={save}>{saving ? 'Saving…' : 'Save and re-render'}</button>
						<button class="btn" disabled={saving} onclick={() => { if (!dirty || confirm('Discard your unsaved edits?')) editing = false; }}>Cancel</button>
						{#if resume.edited}<button class="btn ml-auto" disabled={saving} onclick={restore}>Restore tailored version</button>{/if}
					</div>
				</aside>
			{/if}
		</div>
	</div>
</dialog>
