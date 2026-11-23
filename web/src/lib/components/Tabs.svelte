<script lang="ts">
	let { tabs, active = $bindable() }: { tabs: { id: string; label: string }[]; active: string } = $props();

	function key(e: KeyboardEvent, i: number) {
		const n = tabs.length;
		const to = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i - 1 + n) % n : -1;
		if (to >= 0) {
			active = tabs[to].id;
			(document.getElementById(`tab-${tabs[to].id}`) as HTMLElement | null)?.focus();
		}
	}
</script>

<div role="tablist" class="flex gap-1 overflow-x-auto border-b border-line">
	{#each tabs as t, i (t.id)}
		<button
			id="tab-{t.id}"
			role="tab"
			aria-selected={active === t.id}
			tabindex={active === t.id ? 0 : -1}
			onclick={() => (active = t.id)}
			onkeydown={(e) => key(e, i)}
			class="-mb-px border-b-2 px-3 py-2 text-sm whitespace-nowrap {active === t.id ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}"
		>
			{t.label}
		</button>
	{/each}
</div>
