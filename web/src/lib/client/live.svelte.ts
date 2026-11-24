import type { Task } from '$lib/types';
import { taskLabel } from '$lib/format';

/** Kinds whose result is a run's resumes: their "finished" notice links to that run. */
const RUN_KINDS = new Set(['run', 'tailor', 'resume']);

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
	/** Tasks that ended since the tray was last opened (for its badge). */
	unseen = $state<{ id: number; status: Task['status'] }[]>([]);
	private source: EventSource | null = null;
	private finished = new Set<number>();
	private loaded = false; // the first list is history, not news

	start() {
		if (this.source || typeof EventSource === 'undefined') return;
		const es = new EventSource('/api/events');
		this.source = es;
		es.onopen = () => (this.connected = true);
		es.onerror = () => (this.connected = false);
		es.addEventListener('tasks', (e) => {
			const tasks = JSON.parse((e as MessageEvent).data) as Task[];
			let bump = false;
			const firstLoad = !this.loaded;
			this.loaded = true;
			for (const t of tasks) {
				const over = !['queued', 'running'].includes(t.status);
				if (over && !this.finished.has(t.id)) {
					this.finished.add(t.id);
					if (!firstLoad) {
						// Ended while this page was open: say so, don't just refresh quietly.
						bump = true;
						this.announce(t);
						this.unseen = [...this.unseen, { id: t.id, status: t.status }];
					}
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

	/** A notice for a task that just ended, linking to where its result or reason is. */
	private announce(t: Task) {
		const label = taskLabel(t.kind, t.args_json);
		const log = { href: `/activity#task-${t.id}`, label: 'See log' };
		if (t.status === 'failed') {
			toasts.show(`${label} failed${t.message ? `: ${t.message}` : '.'}`, 'error', log);
		} else if (t.status === 'cancelled') {
			toasts.show(`${label} stopped.`, 'info', log);
		} else if (RUN_KINDS.has(t.kind)) {
			const run = this.latestRun?.id;
			toasts.show(`${label} finished.`, 'success', run ? { href: `/runs/${run}`, label: 'Open run' } : log);
		} else {
			toasts.show(`${label} finished.`, 'success', log);
		}
	}

	/** The tray was opened: its badge is cleared. */
	seen() {
		this.unseen = [];
	}
}

export const live = new Live();

/** Small transient messages (bottom of the screen). */
type ToastKind = 'info' | 'success' | 'error';
type ToastLink = { href: string; label: string };

class Toasts {
	items = $state<{ id: number; text: string; kind: ToastKind; link?: ToastLink }[]>([]);
	private next = 1;
	/** `link`: an action to go with it (e.g. open the run); such toasts stay longer. */
	show(text: string, kind: ToastKind = 'info', link?: ToastLink) {
		const id = this.next++;
		this.items.push({ id, text, kind, link });
		const ms = kind === 'error' ? 10000 : link ? 8000 : 3500;
		setTimeout(() => this.dismiss(id), ms);
	}
	dismiss(id: number) {
		this.items = this.items.filter((t) => t.id !== id);
	}
}

export const toasts = new Toasts();
