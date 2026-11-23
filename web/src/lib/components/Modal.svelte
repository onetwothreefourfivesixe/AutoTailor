<script lang="ts">
	import type { Snippet } from 'svelte';
	let { open = $bindable(false), title, wide = false, children }: { open?: boolean; title: string; wide?: boolean; children: Snippet } = $props();
	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});
</script>

<!-- Native <dialog>: focus trapping, Esc to close and inert background come with it. -->
<dialog
	bind:this={dialog}
	onclose={() => (open = false)}
	class="m-auto w-[calc(100%-2rem)] {wide ? 'max-w-4xl' : 'max-w-xl'} rounded-xl border border-line bg-surface p-0 text-fg shadow-2xl backdrop:bg-black/40"
>
	<div class="flex items-center justify-between border-b border-line px-5 py-3">
		<h2 class="font-semibold">{title}</h2>
		<button class="btn btn-sm" onclick={() => (open = false)} aria-label="Close">Esc</button>
	</div>
	<div class="max-h-[75vh] overflow-y-auto p-5">
		{@render children()}
	</div>
</dialog>
