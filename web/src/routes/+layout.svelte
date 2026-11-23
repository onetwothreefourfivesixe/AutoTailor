<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { invalidateAll } from '$app/navigation';
	import { live } from '$lib/client/live.svelte';
	import TaskTray from '$lib/components/TaskTray.svelte';
	import Toasts from '$lib/components/Toasts.svelte';
	import AddDialog from '$lib/components/AddDialog.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';

	let { children } = $props();
	let addOpen = $state(false);
	let paletteOpen = $state(false);
	let dropped = $state<File[]>([]);
	let navOpen = $state(false);
	let theme = $state('light');

	const nav = [
		{ href: '/', label: 'Today' },
		{ href: '/jobs', label: 'Jobs' },
		{ href: '/runway', label: 'Runway' },
		{ href: '/applications', label: 'Applications' },
		{ href: '/runs', label: 'Runs' },
		{ href: '/cv', label: 'Master CV' },
		{ href: '/settings/profile', label: 'Settings', match: '/settings' },
		{ href: '/activity', label: 'Activity' },
		{ href: '/terminal', label: 'Command line' }
	];

	const signedIn = $derived(page.url.pathname !== '/login');
	const active = (item: { href: string; match?: string }) =>
		item.href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(item.match ?? item.href);

	onMount(() => {
		theme = document.documentElement.dataset.theme ?? 'light';
		if (signedIn) live.start();
		document.body.dataset.ready = '1'; // the app is interactive (used by the end-to-end tests)
	});

	// Reload the current page's data when background work finishes or postings arrive.
	let seen = 0;
	$effect(() => {
		const n = live.changed;
		if (n !== seen) {
			seen = n;
			invalidateAll();
		}
	});

	function toggleTheme() {
		theme = theme === 'dark' ? 'light' : 'dark';
		document.documentElement.dataset.theme = theme;
		try {
			localStorage.setItem('theme', theme);
		} catch {
			/* private mode */
		}
	}

	function typing(e: KeyboardEvent) {
		const el = e.target as HTMLElement;
		return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
	}

	function keydown(e: KeyboardEvent) {
		if (!signedIn) return;
		if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			paletteOpen = true;
		} else if (e.key === 'a' && !e.metaKey && !e.ctrlKey && !e.altKey && !typing(e) && !addOpen && !paletteOpen) {
			e.preventDefault();
			addOpen = true;
		}
	}

	// Drop files anywhere: open the Add dialog with them.
	function dragover(e: DragEvent) {
		if (signedIn && e.dataTransfer?.types.includes('Files')) e.preventDefault();
	}
	function drop(e: DragEvent) {
		if (!signedIn || !e.dataTransfer?.files.length || addOpen) return;
		e.preventDefault();
		dropped = Array.from(e.dataTransfer.files);
		addOpen = true;
	}
</script>

<svelte:window onkeydown={keydown} ondragover={dragover} ondrop={drop} />

{#if !signedIn}
	{@render children()}
{:else}
	<div class="flex min-h-screen">
		<aside class="fixed inset-y-0 left-0 z-30 w-56 -translate-x-full border-r border-line bg-surface transition-transform md:translate-x-0 {navOpen ? 'translate-x-0' : ''}">
			<div class="flex h-14 items-center gap-2 px-4">
				<img src="/favicon.svg" alt="" class="h-6 w-6" />
				<span class="font-semibold">AutoTailor</span>
			</div>
			<nav class="space-y-0.5 px-2" aria-label="Main">
				{#each nav as item (item.href)}
					<a
						href={item.href}
						onclick={() => (navOpen = false)}
						class="flex items-center justify-between rounded-md px-3 py-1.5 text-sm {active(item) ? 'bg-sunken font-medium' : 'text-muted hover:bg-sunken hover:text-fg'}"
						aria-current={active(item) ? 'page' : undefined}
					>
						{item.label}
						{#if item.href === '/' && live.needs}
							<span class="rounded-full bg-accent px-1.5 text-xs text-accent-fg" title="{live.needs} item(s) need you">{live.needs}</span>
						{/if}
					</a>
				{/each}
			</nav>
			<div class="absolute inset-x-0 bottom-0 space-y-1 border-t border-line p-3">
				<button class="btn w-full justify-center" onclick={() => (addOpen = true)}>+ Add <kbd class="text-xs text-muted">A</kbd></button>
				<button class="btn w-full justify-center" onclick={() => (paletteOpen = true)}>Search <kbd class="text-xs text-muted">Ctrl K</kbd></button>
				<div class="flex gap-1">
					<button class="btn btn-sm flex-1 justify-center" onclick={toggleTheme}>{theme === 'dark' ? 'Light' : 'Dark'}</button>
					<form method="POST" action="/logout" class="flex-1"><button class="btn btn-sm w-full justify-center">Sign out</button></form>
				</div>
			</div>
		</aside>
		<div class="min-w-0 flex-1 md:pl-56">
			<header class="flex h-12 items-center gap-2 border-b border-line bg-surface px-4 md:hidden">
				<button class="btn btn-sm" onclick={() => (navOpen = !navOpen)} aria-label="Menu">☰</button>
				<span class="font-semibold">AutoTailor</span>
			</header>
			<main class="mx-auto max-w-[1400px] px-4 py-6 pb-24 md:px-8">
				{@render children()}
			</main>
		</div>
	</div>
	<AddDialog bind:open={addOpen} bind:files={dropped} />
	<CommandPalette bind:open={paletteOpen} onadd={() => (addOpen = true)} />
	<TaskTray />
{/if}
<Toasts />
