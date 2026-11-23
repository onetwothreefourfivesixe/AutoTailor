<script lang="ts">
	import { toasts } from '$lib/client/live.svelte';
</script>

<div class="pointer-events-none fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col gap-2" aria-live="polite">
	{#each toasts.items as t (t.id)}
		<div class="pointer-events-auto rounded-md px-4 py-2 text-sm shadow-lg {t.kind === 'error' ? 'bg-danger-bg text-danger' : 'bg-fg text-bg'}">{t.text}</div>
	{/each}
</div>
