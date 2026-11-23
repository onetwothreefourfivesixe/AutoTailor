import type { Task } from '$lib/types';

/**
 * App-wide live state from the API's event stream (/api/events): tasks, the Needs-you
 * count, the latest run, and the number of postings. Pages read it to refresh themselves.
 */
class Live {
	tasks = $state<Task[]>([]);
	needs = $state<number | null>(null);
	latestRun = $state<Record<string, unknown> | null>(null);
	postings = $state<number>(0);
	connected = $state(false);
	/** Increments whenever something finished, so pages can reload their data. */
	changed = $state(0);
	private source: EventSource | null = null;
	private finished = new Set<number>();

	start() {
		if (this.source || typeof EventSource === 'undefined') return;
		const es = new EventSource('/api/events');
		this.source = es;
		es.onopen = () => (this.connected = true);
		es.onerror = () => (this.connected = false);
		es.addEventListener('tasks', (e) => {
			const tasks = JSON.parse((e as MessageEvent).data) as Task[];
			let bump = false;
			for (const t of tasks) {
				const over = !['queued', 'running'].includes(t.status);
				if (over && !this.finished.has(t.id)) {
					if (this.tasks.length) bump = true; // not on first load
					this.finished.add(t.id);
				}
			}
			this.tasks = tasks;
			if (bump) this.changed++;
		});
		es.addEventListener('needs', (e) => (this.needs = JSON.parse((e as MessageEvent).data).count));
		es.addEventListener('runs', (e) => (this.latestRun = JSON.parse((e as MessageEvent).data).latest));
		es.addEventListener('postings', (e) => {
			const n = JSON.parse((e as MessageEvent).data).count as number;
			if (this.postings && n !== this.postings) this.changed++;
			this.postings = n;
		});
	}

	get active() {
		return this.tasks.filter((t) => t.status === 'queued' || t.status === 'running');
	}
}

export const live = new Live();

/** Small transient messages (bottom of the screen). */
class Toasts {
	items = $state<{ id: number; text: string; kind: 'info' | 'error' }[]>([]);
	private next = 1;
	show(text: string, kind: 'info' | 'error' = 'info') {
		const id = this.next++;
		this.items.push({ id, text, kind });
		setTimeout(() => (this.items = this.items.filter((t) => t.id !== id)), kind === 'error' ? 7000 : 3500);
	}
}

export const toasts = new Toasts();
