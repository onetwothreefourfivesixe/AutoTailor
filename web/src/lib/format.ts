/** Small display helpers shared by pages. */

export function when(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	const secs = (Date.now() - d.getTime()) / 1000;
	if (secs < 60) return 'just now';
	if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
	if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
	if (secs < 86400 * 7) return `${Math.round(secs / 86400)} d ago`;
	return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function date(iso: string | null | undefined): string {
	return iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '';
}

export function fit(x: number | null | undefined): string {
	return x == null ? '–' : x.toFixed(2);
}

export function pct(x: number | null | undefined): string {
	return x == null ? '–' : `${Math.round(x * 100)}%`;
}

const PROVIDERS: Record<string, string> = { anthropic: 'Anthropic', openai: 'OpenAI', google_genai: 'Google', ollama: 'Ollama' };

export function provider(p: string): string {
	return PROVIDERS[p] ?? p;
}

export function taskLabel(kind: string, argsJson: string): string {
	const args = JSON.parse(argsJson || '[]') as string[];
	const labels: Record<string, string> = {
		fetch: 'Fetch job boards', mail: 'Import Runway emails', resolve: 'Match Runway roles', screen: 'Screen eligibility',
		rank: 'Rank', tailor: 'Tailor resumes', resume: 'Resume a run', answers: 'Suggest answer points', run: 'Whole pipeline'
	};
	if (kind === 'cli') return `autotailor ${args.join(' ')}`;
	return [labels[kind] ?? kind, ...args].join(' ');
}

export function words(text: string): number {
	return (text.match(/\b[\w'’-]+\b/g) ?? []).length;
}
