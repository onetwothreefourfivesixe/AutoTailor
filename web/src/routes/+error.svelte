<script lang="ts">
	import { page } from '$app/state';
	const down = $derived(page.status === 503);
</script>

<svelte:head><title>{page.status} · AutoTailor</title></svelte:head>

<div class="mx-auto mt-16 max-w-lg text-center">
	<p class="text-sm text-muted">{page.status}</p>
	<h1 class="h1 mt-1">{down ? "AutoTailor's API isn't running" : page.status === 404 ? 'Not found' : 'Something went wrong'}</h1>
	<p class="mt-3 text-sm text-muted">{page.error?.message}</p>
	<div class="mt-6 flex justify-center gap-2">
		<button class="btn" onclick={() => location.reload()}>Try again</button>
		<a class="btn btn-primary" href="/">Today</a>
	</div>
</div>
