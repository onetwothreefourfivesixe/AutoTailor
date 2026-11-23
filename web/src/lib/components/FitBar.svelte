<script lang="ts">
	let { value }: { value: number | null } = $props();
</script>

{#if value == null}
	<span class="text-muted">–</span>
{:else}
	<span class="inline-flex items-center gap-2" title="Fit {value.toFixed(2)}">
		<span class="h-1.5 w-12 overflow-hidden rounded-full bg-sunken">
			<span class="block h-full rounded-full bg-accent" style="width: {Math.round(value * 100)}%"></span>
		</span>
		<span class="tabular-nums">{value.toFixed(2)}</span>
	</span>
{/if}
