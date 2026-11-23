<script lang="ts">
	let { verdict, overridden = false }: { verdict: string; overridden?: boolean } = $props();
	const styles: Record<string, string> = {
		eligible: 'bg-good-bg text-good',
		unclear: 'bg-warn-bg text-warn',
		ineligible: 'bg-off-bg text-off',
		not_screened: 'bg-sunken text-muted'
	};
	const labels: Record<string, string> = { not_screened: 'not screened' };
</script>

<span
	class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap {styles[verdict] ?? styles.not_screened} {overridden ? 'ring-1 ring-current' : ''}"
	title={overridden ? 'Overridden by you' : undefined}
>
	{labels[verdict] ?? verdict}{overridden ? ' ·  yours' : ''}
</span>
