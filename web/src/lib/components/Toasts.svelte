<script lang="ts">
	import { toasts } from '$lib/client/live.svelte';

	const style = { info: 'bg-fg text-bg', success: 'bg-good-bg text-good border border-good/30', error: 'bg-danger-bg text-danger border border-danger/30' };
</script>

<div class="pointer-events-none fixed bottom-4 left-1/2 z-50 flex w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2" aria-live="polite">
	{#each toasts.items as t (t.id)}
		<div class="pointer-events-auto flex items-start gap-3 rounded-md px-4 py-2 text-sm shadow-lg {style[t.kind]}" role={t.kind === 'error' ? 'alert' : 'status'}>
			<span class="min-w-0 flex-1 break-words">{t.text}</span>
			{#if t.link}
				<a class="shrink-0 font-medium underline" href={t.link.href} onclick={() => toasts.dismiss(t.id)}>{t.link.label}</a>
			{/if}
			<button class="shrink-0 opacity-60 hover:opacity-100" onclick={() => toasts.dismiss(t.id)} aria-label="Dismiss">✕</button>
		</div>
	{/each}
</div>
