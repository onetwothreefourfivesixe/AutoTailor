<script lang="ts">
	import { provider } from '$lib/format';
	let { local, name }: { local: boolean; name: string } = $props();
</script>

{#if local}
	<span class="rounded bg-sunken px-1.5 py-0.5 text-xs text-muted" title="Runs on this machine">on this machine</span>
{:else}
	<span class="rounded bg-cloud-bg px-1.5 py-0.5 text-xs text-cloud" title="Posting and CV text is sent to {provider(name)}">sent to {provider(name)}</span>
{/if}
