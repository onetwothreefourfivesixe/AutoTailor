<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { get, send, ApiError } from '$lib/client/api';
	import { toasts } from '$lib/client/live.svelte';
	import Tabs from '$lib/components/Tabs.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import type { BulletUse } from './+page.server';

	let { data } = $props();

	const ENTRY_SECTIONS = [
		['experience', 'Experience'], ['projects', 'Projects'], ['activities', 'Leadership & Activities'],
		['volunteer', 'Volunteer'], ['awards', 'Awards'], ['certifications', 'Certifications']
	] as const;
	const tabs = [
		{ id: 'contact', label: 'Contact' },
		{ id: 'education', label: 'Education' },
		...ENTRY_SECTIONS.map(([id, label]) => ({ id, label })),
		{ id: 'skills', label: 'Skills' },
		{ id: 'yaml', label: 'Raw YAML' }
	];
	let active = $state('experience');

	const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
	/** Fill in the lists the editor expects, so a sparse CV file still edits cleanly. */
	function normalize(cv: Record<string, any>) {
		const c = clone(cv);
		c.contact ??= { name: '', links: [] };
		c.contact.links ??= [];
		for (const k of ['education', ...ENTRY_SECTIONS.map(([k]) => k), 'skills']) c[k] ??= [];
		return c;
	}
	// The form edits a copy; the effect resets it when the server's data changes (after saving).
	// svelte-ignore state_referenced_locally
	let cv = $state(data.cv ? normalize(data.cv) : null);
	// svelte-ignore state_referenced_locally
	let original = $state(JSON.stringify(data.cv ? normalize(data.cv) : null));
	$effect(() => {
		const fresh = JSON.stringify(data.cv ? normalize(data.cv) : null);
		if (fresh !== original) {
			original = fresh;
			cv = data.cv ? normalize(data.cv) : null;
		}
	});
	const dirty = $derived(JSON.stringify(cv) !== original);
	let saving = $state(false);
	let open = $state<Record<string, boolean>>({}); // which entries are expanded, by section:index

	// Comma- and line-separated lists: parsed when the field is left, so typing a comma isn't eaten.
	const csv = (t: string) => t.split(',').map((x) => x.trim()).filter(Boolean);
	const lines = (t: string) => t.split('\n').map((x) => x.trim()).filter(Boolean);
	const blank = (v: string) => (v.trim() ? v.trim() : null);

	const newEntry = (section: string) =>
		section === 'education'
			? { id: null, school: '', degree: '', field: null, location: null, start: null, graduation: null, gpa: null, hidden: false, details: [] }
			: { id: null, title: '', organization: null, location: null, start: null, end: null, tags: [], stack: [], hidden: false, bullets: [] };
	const newBullet = () => ({ id: null, text: '', tags: [], base: false });

	function addEntry(section: string) {
		cv![section].push(newEntry(section));
		open[`${section}:${cv![section].length - 1}`] = true;
	}
	function move<T>(list: T[], i: number, by: number) {
		const j = i + by;
		if (j < 0 || j >= list.length) return;
		[list[i], list[j]] = [list[j], list[i]];
	}
	const entryLabel = (section: string, e: any) =>
		section === 'education'
			? [e.school || '(new school)', e.degree].filter(Boolean).join(' · ')
			: [e.title || '(new entry)', e.organization].filter(Boolean).join(' · ');
	const bulletsKey = (section: string) => (section === 'education' ? 'details' : 'bullets');
	const uses = (id: string | null): BulletUse[] => (id ? (data.usage[id] ?? []) : []);

	// --- saving ------------------------------------------------------------------------------
	let confirmUses = $state<Record<string, BulletUse[]> | null>(null);
	let confirmOpen = $state(false); // bound to the dialog: Esc closes it too
	let confirmFor = $state<'form' | 'yaml'>('form');
	let problems = $state<string[]>([]);

	function explain(e: unknown): string[] {
		if (e instanceof ApiError && Array.isArray(e.detail))
			return e.detail.map((d: { loc?: (string | number)[]; msg?: string }) => `${(d.loc ?? []).join(' › ')}: ${d.msg}`);
		return [e instanceof ApiError ? e.message : 'Saving failed.'];
	}

	async function save(confirm = false) {
		saving = true;
		problems = [];
		try {
			if (confirmFor === 'yaml') await send('PUT', '/cv/yaml', { text: yaml, confirm_delete_used: confirm });
			else await send('PUT', '/cv', { cv, confirm_delete_used: confirm });
			confirmUses = null;
			confirmOpen = false;
			toasts.show('Saved. The previous version is kept in data/cv_backups/.');
			await invalidateAll();
			if (confirmFor === 'yaml') await loadYaml();
		} catch (e) {
			if (e instanceof ApiError && e.status === 409 && (e.detail as any)?.uses) {
				confirmUses = (e.detail as any).uses; // ask before deleting bullets past resumes used
				confirmOpen = true;
			} else {
				problems = explain(e);
				toasts.show('Not saved; see the problems listed.', 'error');
			}
		} finally {
			saving = false;
		}
	}
	const saveForm = () => ((confirmFor = 'form'), save());

	// --- raw YAML ------------------------------------------------------------------------------
	let yaml = $state('');
	let yamlSaved = $state('');
	async function loadYaml() {
		yaml = yamlSaved = await get<string>('/cv/yaml');
	}
	$effect(() => {
		if (active === 'yaml' && !yamlSaved) loadYaml();
	});
	const saveYaml = () => ((confirmFor = 'yaml'), save());

	// --- links from elsewhere: /cv#<bullet id> opens the entry holding that bullet --------------
	onMount(async () => {
		const id = decodeURIComponent(location.hash.slice(1));
		if (!id || !cv) return;
		for (const section of ['education', ...ENTRY_SECTIONS.map(([k]) => k)]) {
			const i = (cv[section] as any[]).findIndex((e) => e.id === id || (e[bulletsKey(section)] ?? []).some((b: any) => b.id === id));
			if (i >= 0) {
				active = section;
				open[`${section}:${i}`] = true;
				await tick();
				const el = document.getElementById(`cv-${id}`);
				el?.scrollIntoView({ block: 'center' });
				el?.classList.add('ring-2', 'ring-accent');
				return;
			}
		}
		toasts.show(`${id} is no longer in the CV.`, 'error');
	});
</script>

<svelte:head><title>Master CV · AutoTailor</title></svelte:head>

<h1 class="h1 mb-1">Master CV</h1>
<p class="mb-4 text-sm text-muted">Everything a tailored resume can say comes from here: tailoring picks and rewords these bullets, never adds new facts.</p>

{#if !cv}
	<section class="card space-y-3 p-5 text-sm">
		<p>There's no master CV yet.</p>
		<a class="btn btn-primary" href="/setup">Import your CV</a>
	</section>
{:else}
	<Tabs {tabs} bind:active />

	<div class="pt-4 pb-24">
		{#if active === 'contact'}
			<section class="card grid gap-3 p-5 sm:grid-cols-2">
				<label class="block"><span class="label">Name</span><input class="input" bind:value={cv.contact.name} /></label>
				<label class="block"><span class="label">Email</span><input class="input" type="email" value={cv.contact.email ?? ''} oninput={(e) => (cv!.contact.email = blank(e.currentTarget.value))} /></label>
				<label class="block"><span class="label">Phone</span><input class="input" value={cv.contact.phone ?? ''} oninput={(e) => (cv!.contact.phone = blank(e.currentTarget.value))} /></label>
				<label class="block"><span class="label">Location</span><input class="input" value={cv.contact.location ?? ''} oninput={(e) => (cv!.contact.location = blank(e.currentTarget.value))} /></label>
				<label class="block sm:col-span-2"><span class="label">Links (one per line)</span><textarea class="input h-24" value={cv.contact.links.join('\n')} onchange={(e) => (cv!.contact.links = lines(e.currentTarget.value))}></textarea></label>
			</section>
		{:else if active === 'skills'}
			<section class="card space-y-2 p-5">
				{#each cv.skills as g, i (i)}
					<div class="grid gap-2 sm:grid-cols-[12rem_1fr_auto]">
						<input class="input" bind:value={g.name} placeholder="Group, e.g. Languages" aria-label="Skill group" />
						<input class="input" value={g.items.join(', ')} onchange={(e) => (g.items = csv(e.currentTarget.value))} placeholder="Comma-separated" aria-label="Skills in {g.name || 'this group'}" />
						<div class="flex gap-1">
							<button class="btn btn-sm" onclick={() => move(cv!.skills, i, -1)} disabled={i === 0} aria-label="Move up">↑</button>
							<button class="btn btn-sm" onclick={() => move(cv!.skills, i, 1)} disabled={i === cv!.skills.length - 1} aria-label="Move down">↓</button>
							<button class="btn btn-sm" onclick={() => cv!.skills.splice(i, 1)} aria-label="Remove group">✕</button>
						</div>
					</div>
				{/each}
				<button class="btn btn-sm" onclick={() => cv!.skills.push({ name: '', items: [] })}>+ Skill group</button>
			</section>
		{:else if active === 'yaml'}
			<section class="card space-y-3 p-5">
				<p class="text-sm text-muted">The file itself, <code>cv/master.yaml</code>. Saving here replaces the form's version; unsaved form edits are lost.</p>
				<textarea class="input h-[60vh] font-mono text-xs" bind:value={yaml} spellcheck="false" aria-label="master.yaml"></textarea>
				<div class="flex gap-2">
					<button class="btn" disabled={yaml === yamlSaved} onclick={() => (yaml = yamlSaved)}>Discard</button>
					<button class="btn btn-primary" disabled={yaml === yamlSaved || saving} onclick={saveYaml}>{saving ? 'Saving…' : 'Save YAML'}</button>
				</div>
			</section>
		{:else}
			{@const section = active}
			{@const bk = bulletsKey(section)}
			<div class="space-y-3">
				{#if cv[section].length === 0}<p class="text-sm text-muted">Nothing here yet.</p>{/if}
				{#each cv[section] as e, i (i)}
					{@const key = `${section}:${i}`}
					<details class="card p-0" id={e.id ? `cv-${e.id}` : undefined} bind:open={open[key]}>
						<summary class="flex cursor-pointer items-center justify-between gap-2 px-4 py-3">
							<span class="min-w-0 truncate font-medium">{entryLabel(section, e)}</span>
							<span class="flex shrink-0 items-center gap-2 text-xs text-muted">
								{#if e.hidden}<span class="rounded bg-sunken px-1.5 py-0.5">hidden</span>{/if}
								{(e[bk] ?? []).length} bullet{(e[bk] ?? []).length === 1 ? '' : 's'}
							</span>
						</summary>
						<div class="space-y-3 border-t border-line p-4">
							{#if section === 'education'}
								<div class="grid gap-2 sm:grid-cols-3">
									<label class="block"><span class="label">School</span><input class="input" bind:value={e.school} /></label>
									<label class="block"><span class="label">Degree</span><input class="input" bind:value={e.degree} /></label>
									<label class="block"><span class="label">Field</span><input class="input" value={e.field ?? ''} oninput={(ev) => (e.field = blank(ev.currentTarget.value))} /></label>
								</div>
								<div class="grid gap-2 sm:grid-cols-4">
									<label class="block"><span class="label">Location</span><input class="input" value={e.location ?? ''} oninput={(ev) => (e.location = blank(ev.currentTarget.value))} /></label>
									<label class="block"><span class="label">Start</span><input class="input" value={e.start ?? ''} oninput={(ev) => (e.start = blank(ev.currentTarget.value))} /></label>
									<label class="block"><span class="label">Graduation</span><input class="input" value={e.graduation ?? ''} oninput={(ev) => (e.graduation = blank(ev.currentTarget.value))} /></label>
									<label class="block"><span class="label">GPA</span><input class="input" value={e.gpa ?? ''} oninput={(ev) => (e.gpa = blank(ev.currentTarget.value))} /></label>
								</div>
							{:else}
								<div class="grid gap-2 sm:grid-cols-2">
									<label class="block"><span class="label">Title</span><input class="input" bind:value={e.title} /></label>
									<label class="block"><span class="label">Organization</span><input class="input" value={e.organization ?? ''} oninput={(ev) => (e.organization = blank(ev.currentTarget.value))} /></label>
								</div>
								<div class="grid gap-2 sm:grid-cols-3">
									<label class="block"><span class="label">Location</span><input class="input" value={e.location ?? ''} oninput={(ev) => (e.location = blank(ev.currentTarget.value))} /></label>
									<label class="block"><span class="label">Start</span><input class="input" value={e.start ?? ''} oninput={(ev) => (e.start = blank(ev.currentTarget.value))} placeholder="2025-06 or Summer 2025" /></label>
									<label class="block"><span class="label">End</span><input class="input" value={e.end ?? ''} oninput={(ev) => (e.end = blank(ev.currentTarget.value))} placeholder="2025-08 or present" /></label>
								</div>
								<div class="grid gap-2 sm:grid-cols-2">
									<label class="block"><span class="label">Tags (comma-separated)</span><input class="input" value={(e.tags ?? []).join(', ')} onchange={(ev) => (e.tags = csv(ev.currentTarget.value))} /></label>
									<label class="block"><span class="label">Tech stack (comma-separated)</span><input class="input" value={(e.stack ?? []).join(', ')} onchange={(ev) => (e.stack = csv(ev.currentTarget.value))} /></label>
								</div>
							{/if}
							<label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={e.hidden} /> Hidden: keep it in the CV but never put it on a resume</label>

							<div>
								<h3 class="label">{section === 'education' ? 'Details (coursework, honors)' : 'Bullets'}</h3>
								<ul class="space-y-2">
									{#each e[bk] as b, j (j)}
										{@const used = uses(b.id)}
										<li class="rounded-md border border-line p-2" id={b.id ? `cv-${b.id}` : undefined}>
											<textarea class="input min-h-16" bind:value={b.text} placeholder="What you did and what came of it" aria-label="Bullet text"></textarea>
											<div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
												<input class="input max-w-60 py-1 text-xs" value={(b.tags ?? []).join(', ')} onchange={(ev) => (b.tags = csv(ev.currentTarget.value))} placeholder="Tags, comma-separated" aria-label="Bullet tags" />
												<label class="flex items-center gap-1" title="On your usual resume: tailoring starts from these bullets."><input type="checkbox" bind:checked={b.base} /> On usual resume</label>
												<span class="font-mono text-muted">{b.id ?? 'new: ID set when saved'}</span>
												{#if used.length}
													<span class="rounded bg-sunken px-1.5 py-0.5" title={used.map((u) => `Run ${u.run_id}: ${u.company} | ${u.title}`).join('\n')}>used in {used.length} resume{used.length === 1 ? '' : 's'}</span>
												{/if}
												<span class="ml-auto flex gap-1">
													<button class="btn btn-sm" onclick={() => move(e[bk], j, -1)} disabled={j === 0} aria-label="Move bullet up">↑</button>
													<button class="btn btn-sm" onclick={() => move(e[bk], j, 1)} disabled={j === e[bk].length - 1} aria-label="Move bullet down">↓</button>
													<button class="btn btn-sm" onclick={() => e[bk].splice(j, 1)} aria-label="Remove bullet">✕</button>
												</span>
											</div>
										</li>
									{/each}
								</ul>
								<button class="btn btn-sm mt-2" onclick={() => e[bk].push(newBullet())}>+ {section === 'education' ? 'Detail' : 'Bullet'}</button>
							</div>

							<div class="flex flex-wrap items-center gap-2 border-t border-line pt-3 text-xs text-muted">
								<span class="font-mono">{e.id ?? 'new: ID set when saved'}</span>
								<span class="ml-auto flex gap-1">
									<button class="btn btn-sm" onclick={() => move(cv![section], i, -1)} disabled={i === 0}>Move up</button>
									<button class="btn btn-sm" onclick={() => move(cv![section], i, 1)} disabled={i === cv![section].length - 1}>Move down</button>
									<button class="btn btn-sm btn-danger" onclick={() => cv![section].splice(i, 1)}>Remove entry</button>
								</span>
							</div>
						</div>
					</details>
				{/each}
				<button class="btn btn-sm" onclick={() => addEntry(section)}>+ {section === 'education' ? 'School' : 'Entry'}</button>
			</div>
		{/if}

		{#if problems.length}
			<div class="mt-4 rounded-md border border-line bg-sunken p-3 text-sm" role="alert">
				<p class="font-medium">Not saved. Fix these first:</p>
				<ul class="mt-1 list-disc pl-5">{#each problems as p (p)}<li>{p}</li>{/each}</ul>
			</div>
		{/if}
	</div>

	{#if active !== 'yaml'}
		<div class="fixed right-4 bottom-16 z-30 flex gap-2 rounded-lg border border-line bg-surface p-2 shadow-lg md:right-8">
			<button class="btn" disabled={!dirty} onclick={() => ((cv = data.cv ? normalize(data.cv) : null), (problems = []))}>Discard</button>
			<button class="btn btn-primary" disabled={!dirty || saving} onclick={saveForm}>{saving ? 'Saving…' : 'Save CV'}</button>
		</div>
	{/if}

	<Modal bind:open={confirmOpen} title="Delete bullets used in past resumes?">
		<p class="text-sm">These bullets were used in resumes you already have. Those resumes stay as they are, but you won't be able to trace them back to the CV.</p>
		<ul class="my-3 space-y-1 text-sm">
			{#each Object.entries(confirmUses ?? {}) as [id, list] (id)}
				<li><span class="font-mono text-xs">{id}</span>: {list.map((u) => `run ${u.run_id}, ${u.company} | ${u.title}`).join('; ')}</li>
			{/each}
		</ul>
		<div class="flex justify-end gap-2">
			<button class="btn" onclick={() => (confirmOpen = false)}>Keep them</button>
			<button class="btn btn-danger" disabled={saving} onclick={() => save(true)}>Delete and save</button>
		</div>
	</Modal>
{/if}
