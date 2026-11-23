import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const link = () => readFileSync(join(process.env.E2E_HOME!, 'login_link.txt'), 'utf-8').trim();

/** Open a page and wait until the app is interactive (it keeps a live connection, so the network never idles). */
async function open(page: Page, path: string) {
	await page.goto(path);
	await page.locator('body[data-ready]').waitFor();
}

test('signing in: nothing opens without a session, and the link works only once', async ({ page, browser }) => {
	await open(page, '/');
	await expect(page.getByRole('heading', { name: 'Today' })).toBeVisible();
	const fresh = await browser.newContext({ storageState: undefined });
	const anon = await fresh.newPage();
	await anon.goto('/jobs');
	await expect(anon).toHaveURL(/\/login$/);
	expect((await fresh.request.get('/api/jobs')).status()).toBe(401);
	await anon.goto(link());
	await expect(anon.getByText('expired or was already used')).toBeVisible();
	await fresh.close();
});

test('jobs: list, keyboard, open a job', async ({ page }) => {
	await open(page, '/jobs');
	await expect(page.getByRole('heading', { name: /Jobs/ })).toContainText('2');
	await expect(page.locator('tbody tr')).toHaveCount(2);
	await page.keyboard.press('j');
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/jobs\/lever%3Ae2e-b/);
	await expect(page.getByRole('heading', { name: 'Data Science Intern' })).toBeVisible();
});

test('a job: override the verdict, mark it applied, see it on the board', async ({ page }) => {
	await open(page, '/jobs/lever%3Ae2e-a?tab=eligibility');
	await page.getByLabel('Verdict', { exact: true }).selectOption('ineligible');
	await page.getByLabel('Why').fill('Needs a security clearance');
	await page.getByRole('button', { name: 'Override', exact: true }).click();
	await expect(page.getByText('The rules said eligible.')).toBeVisible();
	await page.getByRole('button', { name: 'Remove override' }).click();
	await expect(page.getByText('The rules said eligible.')).toHaveCount(0);

	await page.getByLabel('Application status').selectOption('applied');
	await expect(page.getByText(/Applied \w+ \d+/).first()).toBeVisible();
	await open(page, '/applications');
	const card = page.locator('article', { hasText: 'Northwind Robotics' });
	await expect(card).toBeVisible();
	await card.getByLabel('Status').selectOption('interview');
	await expect(page.getByRole('region', { name: 'Interview' }).getByText('Northwind Robotics')).toBeVisible();
});

test('master CV: edit, save, and the preview renders', async ({ page }) => {
	await open(page, '/cv');
	await expect(page.getByAltText('A sample resume from this CV')).toBeVisible({ timeout: 30_000 });
	await page.getByRole('tab', { name: /Contact/ }).click();
	await page.getByLabel('Name').fill('Sam Q. Example');
	await page.getByRole('button', { name: 'Save CV' }).click();
	await expect(page.getByText('CV saved')).toBeVisible();
	await page.reload();
	await page.locator('body[data-ready]').waitFor();
	await page.getByRole('tab', { name: /Contact/ }).click();
	await expect(page.getByLabel('Name')).toHaveValue('Sam Q. Example');
});

test('settings: a change is validated and saved', async ({ page }) => {
	await open(page, '/settings/runs');
	await page.getByLabel('Resumes per run (top X)').fill('4');
	await page.getByRole('button', { name: 'Save settings' }).click();
	await expect(page.getByText(/^Saved\./)).toBeVisible();
	await page.reload();
	await page.locator('body[data-ready]').waitFor();
	await expect(page.getByLabel('Resumes per run (top X)')).toHaveValue('4');
});

test('command palette: find a job and go to it', async ({ page }) => {
	await open(page, '/');
	await page.keyboard.press('Control+k');
	await page.getByLabel('Search', { exact: true }).fill('northwind');
	await expect(page.getByRole('option', { name: /Northwind Robotics/ })).toBeVisible();
	await page.keyboard.press('ArrowDown');
	await page.getByRole('option', { name: /Northwind Robotics/ }).click();
	await expect(page).toHaveURL(/\/jobs\/lever%3Ae2e-a/);
});

test('setup wizard: models, CV from a file, about you, job boards', async ({ page }) => {
	await open(page, '/setup');
	await page.getByRole('button', { name: 'Start' }).click();

	await page.getByRole('radio', { name: /On this computer/ }).click();
	await expect(page.getByText('ollama pull nomic-embed-text')).toBeVisible();
	await page.getByRole('button', { name: 'Save and continue' }).click();

	await expect(page.getByRole('heading', { name: 'Your CV' })).toBeVisible();
	await page.locator('input[type=file]').setInputFiles(join(process.cwd(), '..', 'tests', 'fixtures', 'cv_ml_student.yaml'));
	await expect(page.getByText(/bullets found/)).toBeVisible();
	await page.getByRole('button', { name: 'Save CV and continue' }).click();

	await expect(page.getByRole('heading', { name: 'About you' })).toBeVisible();
	await page.getByRole('button', { name: 'Robotics', exact: true }).click();
	await page.getByRole('button', { name: 'Save and continue' }).click();

	await expect(page.getByRole('heading', { name: 'Job boards to follow' })).toBeVisible();
	await page.getByLabel('Search companies').fill('stripe');
	await page.getByRole('checkbox', { name: 'Stripe' }).check();
	await page.getByRole('button', { name: 'Save and continue' }).click();

	await expect(page.getByRole('heading', { name: "You're set up" })).toBeVisible();
	await page.getByRole('button', { name: 'Go to Today' }).click();
	await expect(page).toHaveURL('/');
	await open(page, '/settings/sources');
	await expect(page.getByLabel(/greenhouse/i)).toHaveValue(/stripe/);
});
