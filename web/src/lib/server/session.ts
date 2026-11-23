import { randomBytes, timingSafeEqual } from 'node:crypto';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { home } from './config';

/**
 * Browser sessions. `autotailor ui` writes a one-time code to data/ui_login.json and
 * opens /login?code=...; the code becomes an HttpOnly, SameSite=Strict cookie. Sessions
 * live in memory, so restarting the UI server asks for a new login link.
 */
export const COOKIE = 'autotailor_session';
const sessions = new Set<string>();

function same(a: string, b: string): boolean {
	const x = Buffer.from(a);
	const y = Buffer.from(b);
	return x.length === y.length && timingSafeEqual(x, y);
}

/** Consume a login code; returns a new session ID, or null if the code is wrong or expired. */
export function redeem(code: string): string | null {
	const file = join(home(), 'data', 'ui_login.json');
	if (!code || !existsSync(file)) return null;
	try {
		const saved = JSON.parse(readFileSync(file, 'utf-8')) as { code: string; expires: number };
		if (Date.now() / 1000 > saved.expires || !same(code, saved.code)) return null;
		rmSync(file);
	} catch {
		return null;
	}
	const id = randomBytes(32).toString('base64url');
	sessions.add(id);
	return id;
}

export function valid(id: string | undefined): boolean {
	return !!id && sessions.has(id);
}

export function end(id: string | undefined): void {
	if (id) sessions.delete(id);
}
