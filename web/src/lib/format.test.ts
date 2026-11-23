import { describe, expect, it } from 'vitest';
import { fit, pct, taskLabel, when, words } from './format';

describe('format', () => {
	it('fit and percentages', () => {
		expect(fit(0.8571)).toBe('0.86');
		expect(fit(null)).toBe('–');
		expect(pct(0.95)).toBe('95%');
	});
	it('relative times', () => {
		expect(when(new Date().toISOString())).toBe('just now');
		expect(when(new Date(Date.now() - 2 * 3600e3).toISOString())).toBe('2 h ago');
		expect(when(null)).toBe('');
	});
	it('task labels', () => {
		expect(taskLabel('tailor', '["--ignore-limits"]')).toBe('Tailor resumes --ignore-limits');
		expect(taskLabel('cli', '["rank", "--rescore"]')).toBe('autotailor rank --rescore');
	});
	it('word counts match the Python check', () => {
		expect(words("I built an OCR pipeline, and it's fast.")).toBe(8);
	});
});
