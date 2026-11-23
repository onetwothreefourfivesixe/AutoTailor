import { test as setup, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Sign in once with the one-time link; every test reuses the session cookie.
setup('sign in', async ({ page }) => {
	const home = process.env.E2E_HOME!;
	await page.goto(readFileSync(join(home, 'login_link.txt'), 'utf-8').trim());
	await expect(page).toHaveURL('/');
	await page.context().storageState({ path: join(home, 'session.json') });
});
