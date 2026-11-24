/** Shapes returned by the local API (src/autotailor/api.py, api_more.py). */

export type Verdict = 'eligible' | 'unclear' | 'ineligible' | 'not_screened';
export type AppStatus = 'applied' | 'heard_back' | 'interview' | 'offer' | 'rejected' | 'withdrawn';
export const APP_STATUSES: AppStatus[] = ['applied', 'heard_back', 'interview', 'offer', 'rejected', 'withdrawn'];
export const APP_LABELS: Record<AppStatus, string> = {
	applied: 'Applied',
	heard_back: 'Heard back',
	interview: 'Interview',
	offer: 'Offer',
	rejected: 'Rejected',
	withdrawn: 'Withdrawn'
};

export interface JobRow {
	id: string;
	company: string;
	title: string;
	location: string | null;
	source: string;
	status: string;
	posted_at: string | null;
	verdict: Verdict;
	overridden: boolean;
	fit: number | null;
	prescore: number | null;
	runway_score: number | null;
	tailored: boolean;
	application: AppStatus | null;
	hidden: boolean;
}

export interface Criterion {
	score: number;
	reason: string;
}

export interface Match {
	posting_id: string;
	verdict: Exclude<Verdict, 'not_screened'>;
	original_verdict: string | null;
	verdict_reasons: string[];
	prescore: number | null;
	fit_score: number | null;
	fit_reasons: string[];
	fit: {
		skills: Criterion;
		experience: Criterion;
		level: Criterion;
		interests: Criterion;
		missing: string[];
		relevant_bullet_ids: string[];
	} | null;
}

export interface Evidence {
	field: string;
	quote: string;
}

export interface Requirements {
	degree_levels: string[];
	undergraduates_only: boolean;
	enrollment_required: boolean | null;
	graduation_earliest: string | null;
	graduation_latest: string | null;
	majors: string[];
	work_authorization: string | null;
	evidence: Evidence[];
}

export interface Posting {
	id: string;
	source: string;
	company: string;
	title: string;
	location: string | null;
	posted_at: string | null;
	description: string | null;
	canonical_url: string | null;
	source_url: string | null;
	external_score: number | null;
	status: string;
	requirements: Requirements | null;
}

export interface Question {
	id: number;
	posting_id: string;
	text: string;
	word_limit: number | null;
	status: string;
	answer: string | null;
	model: string | null;
	points: { source_id: string; point: string; why: string; cv_text?: string }[];
	problems: string[];
}

export interface Application {
	posting_id: string;
	status: AppStatus;
	applied_at: string;
	run_id: number | null;
	notes: string;
	updated_at: string;
	company?: string;
	title?: string;
	location?: string | null;
	url?: string | null;
	events: { status: string; note: string | null; at: string }[];
}

export interface JobDetail {
	posting: Posting;
	match: Match | null;
	highlighted: string | null;
	missing_quotes: string[];
	cited_bullets: { id: string; text: string | null }[];
	questions: Question[];
	runway: Record<string, unknown> | null;
	application: Application | null;
	hidden: boolean;
	ai_calls: number;
	has_resume: boolean;
}

export interface EditableItem { title: string; organization: string; location: string; dates: string; stack: string; bullets: string[] }
export interface EditableLayout {
	name: string;
	contact_items: { text: string; url: string | null }[];
	sections: { key: string; title: string; items: EditableItem[] }[];
	skills: { name: string; items: string }[];
}

export interface ResumeView {
	edited: boolean;
	layout: EditableLayout | null;
	layout_edited: boolean;
	cv_bullets: { id: string; text: string; entry: string }[];
	cv_skills: { name: string; items: string[] }[];
	run_id: number;
	model: string | null;
	created_at: string;
	pdf: string | null;
	docx: string | null;
	pages: number | null;
	skills: string[];
	bullets: { source_id: string; original: string | null; tailored: string; changed: boolean; html: string }[];
	history: { attempt: number; bullets: string[]; errors: string[]; note?: string }[];
	added_to_fill: string[];
	removed_to_fit: string[];
	keyword_score: number | null;
	keywords_matched: string[];
	keywords_missing: string[];
}

export interface Task {
	id: number;
	kind: string;
	args_json: string;
	status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
	done: number | null;
	total: number | null;
	message: string | null;
	created_at: string;
	started_at: string | null;
	finished_at: string | null;
	/** Last output or progress from the running task. */
	updated_at?: string | null;
	log?: string;
}

export interface Need {
	kind: string;
	title: string;
	detail: string;
	ref: string;
	when: string | null;
	posting_id: string | null;
}

export interface Run {
	id: number;
	started_at: string;
	finished_at: string | null;
	status: string;
	counts: Record<string, number>;
	model: string;
}

export interface AiCall {
	id: number;
	at: string;
	step: string;
	provider: string;
	model: string;
	local: number;
	item: string | null;
	task_id: number | null;
	messages: string | null;
	response: string | null;
	error: string | null;
	ms: number;
	tokens_in: number | null;
	tokens_out: number | null;
}

export interface StepModel {
	step: string;
	provider: string;
	model: string;
	local: boolean;
	pulled: boolean | null;
}

export interface Overview {
	counts: Record<string, number>;
	mode: 'local' | 'cloud' | 'mixed';
	models: Record<string, { provider: string; model: string; local: boolean }>;
	shortlist: { id: string; company: string; title: string; fit: number }[];
	needs: number;
	worker_running: boolean;
}

export interface SetupStatus {
	checks: Record<'settings' | 'cv' | 'profile' | 'models' | 'sources', boolean>;
	complete: boolean;
	dismissed: boolean;
	cv: 'missing' | 'template' | 'invalid' | 'ready';
	keys: Record<string, boolean>;
	missing_keys: string[];
	boards: number;
	mode: string;
}
