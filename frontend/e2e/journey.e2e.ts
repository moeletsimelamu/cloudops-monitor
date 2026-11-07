// The primary operational workflow, through the interface only: sign in, register the sandbox,
// watch it fail and recover, ship and filter logs, pause, remove, sign out. The sandbox's failures
// are real HTTP responses from a real process; nothing here is mocked.
import { expect, test, type Page } from '@playwright/test';

import { SANDBOX_PORT, OPERATOR_TOKEN, setSandboxMode, watchForErrors } from './support';

const SLUG = 'orders-sandbox';
const NAME = 'Orders sandbox';

test.describe.configure({ mode: 'serial' });

let page: Page;
let problems: string[];

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  problems = watchForErrors(page);
});

test.afterAll(async ({ request }) => {
  await setSandboxMode(request, 'healthy');
  await page.close();
});

const button = (name: string | RegExp) =>
  page.getByRole('button', { name, exact: typeof name === 'string' });
const status = () => page.locator('co-status').first();

/** "Check now", waiting out the server's 5-second spacing between manual checks. */
async function checkNow(): Promise<void> {
  const rows = page.locator('co-checks-panel tbody tr.main');
  const before = await rows.count();
  await page.waitForTimeout(5_500);
  await button('Check now').click();
  await expect(rows).not.toHaveCount(before);
}

test('a wrong token is refused and the right one signs in', async () => {
  await page.goto('/overview');
  await expect(page).toHaveURL(/sign-in/);
  await page.locator('#token').fill('wrong-token-wrong-token-wrong-token-xx');
  await button('Sign in').click();
  await expect(page.locator('#token-error')).toBeVisible();
  await expect(page).toHaveURL(/sign-in/);

  await page.locator('#token').fill(OPERATOR_TOKEN);
  await button('Sign in').click();
  await expect(page).toHaveURL(/\/overview/);
});

test('the server refuses a cloud metadata address and the form says why', async () => {
  await page.goto('/applications/new');
  await page.locator('#name').fill(NAME);
  await page.locator('#slug').fill(SLUG);
  await page.locator('#check_url').fill('http://169.254.169.254/latest/meta-data');
  await button('Register application').click();
  await expect(page.locator('#check-url-error')).toBeVisible();
  await expect(page).toHaveURL(/\/applications\/new/);
});

test('an allowed target registers, and "Check now" records a real check', async ({ request }) => {
  await setSandboxMode(request, 'healthy');
  await page.locator('#check_url').fill(`http://127.0.0.1:${SANDBOX_PORT}/health`);
  await page.locator('#interval').fill('300'); // the test drives the checks itself
  await page.locator('#failure').fill('1');
  await page.locator('#recovery').fill('1');
  await button('Register application').click();
  await expect(page).toHaveURL(new RegExp(`/applications/${SLUG}`));
  await expect(page.getByRole('heading', { name: NAME })).toBeVisible();

  await checkNow();
  await expect(status()).toContainText('Healthy');
  await expect(page.locator('co-checks-panel tbody tr.main').first()).toContainText('200');
});

test('failing checks open an incident, shown on the page and in the event history', async ({
  request,
}) => {
  await setSandboxMode(request, 'errors');
  await checkNow();
  await expect(status()).not.toContainText('Healthy');
  await expect(page.getByText('Open incident.')).toBeVisible();
  await expect(page.locator('co-checks-panel tbody tr.main').first()).toContainText('500');

  await page.goto(`/events?application=${SLUG}`);
  await expect(page.locator('main')).toContainText(NAME);
  await expect(page.locator('main')).toContainText(/unavailable|degraded/i);

  await page.goto('/overview');
  await expect(page.locator('main')).toContainText(NAME);
});

test('recovery closes the incident', async ({ request }) => {
  await setSandboxMode(request, 'healthy');
  await page.goto(`/applications/${SLUG}`);
  await checkNow();
  await expect(status()).toContainText('Healthy');
  await expect(page.getByText('Open incident.')).toHaveCount(0);
});

test('an ingest key is shown once, works, and its logs can be filtered', async ({ request }) => {
  await page.getByRole('tab', { name: 'Configuration' }).click();
  await button('Create key').click();
  const shown = page.locator('.key code');
  await expect(shown).toContainText('cok_');
  const key = (await shown.innerText()).trim();

  // Ship logs with the key exactly as the page displayed it.
  const now = new Date().toISOString();
  const shipped = await request.post('/api/ingest/logs', {
    headers: { Authorization: `Bearer ${key}` },
    data: {
      entries: [
        { timestamp: now, severity: 'info', message: 'order 1001 placed', event: 'order.placed' },
        {
          timestamp: now,
          severity: 'error',
          message: 'database timeout password=hunter2-not-real',
        },
      ],
    },
  });
  expect(shipped.status()).toBe(200);
  expect((await shipped.json()).accepted).toBe(2);

  await page.reload();
  await expect(page.locator('.key code')).toHaveCount(0); // never shown again
  await expect(page.locator('main')).not.toContainText('cok_');

  await page.goto(`/logs?application=${SLUG}&window=1h`);
  const messages = page.locator('co-log-list li .message');
  await expect(messages).toHaveCount(2);
  await expect(page.locator('main')).not.toContainText('hunter2');
  await page.getByLabel('Text in message').fill('timeout');
  await button('Apply').click();
  await expect(messages).toHaveCount(1);
  await expect(messages.first()).toContainText('database timeout');
  await page.locator('co-log-list li button.row').first().click();
  await expect(page.locator('co-log-list dl.detail')).toHaveCount(1);
  await page.getByLabel('Text in message').fill('no-such-text-anywhere');
  await button('Apply').click();
  await expect(page.locator('co-empty')).toBeVisible();
});

test('pausing and resuming monitoring', async () => {
  await page.goto(`/applications/${SLUG}`);
  await button('Pause monitoring').click();
  await expect(status()).toContainText('Paused');
  await button('Resume monitoring').click();
  await expect(status()).not.toContainText('Paused');
});

test('removing asks first, and Cancel keeps the application', async () => {
  await page.goto(`/applications/${SLUG}?tab=configuration`);
  const remove = button(`Remove ${NAME}`);
  await remove.click();
  await page.locator('dialog[open]').getByRole('button', { name: 'Cancel' }).click();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(SLUG));

  await remove.click();
  await page.locator('dialog[open]').getByRole('button', { name: 'Remove application' }).click();
  await expect(page).toHaveURL(/\/applications(\?|$)/);
  await expect(page.locator('main')).not.toContainText(NAME);
});

test('signing out ends the session', async () => {
  await button('Sign out').click();
  await expect(page).toHaveURL(/sign-in/);
  await page.goto('/system');
  await expect(page).toHaveURL(/sign-in/);
});

test('no script or console errors along the way', () => {
  // The browser reports two refusals the tests caused on purpose as failed resource loads: the
  // wrong token (401) and the metadata address (422). Nothing else may appear.
  const unexpected = problems.filter(
    (p) => !/status of (401 \(Unauthorized\)|422 \(Unprocessable Content\))/.test(p),
  );
  expect(unexpected).toEqual([]);
});
