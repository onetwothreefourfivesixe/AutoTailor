<script lang="ts">
	let { data } = $props();
</script>

<svelte:head><title>Sign in · AutoTailor</title></svelte:head>

<main class="mx-auto mt-24 max-w-md px-4 text-center">
	<h1 class="text-2xl font-semibold">AutoTailor</h1>
	{#if data.failed}
		<p class="mt-4 text-sm text-danger">That sign-in link has expired or was already used.</p>
	{/if}
	<p class="mt-4 text-sm text-muted">
		Open AutoTailor with <code class="rounded bg-sunken px-1.5 py-0.5">autotailor ui</code> in a terminal. It
		prints a one-time sign-in link for this browser.
	</p>
</main>
