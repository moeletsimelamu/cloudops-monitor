// What the interface does when things go wrong around it, and with times in another zone.
import { expect, test } from '@playwright/test';

import { SANDBOX_PORT, api, firstCheck, setSandboxMode, signIn } from './support';

const SLUG = 'resilience-sandbox';

test.beforeAll(async ({ request }) => {
  await setSandboxMode(request, 'healthy');
  await api(request, 'POST', '/api/applications', {
    slug: SLUG,
    name: 'Resilience sandbox',
    environment: 'e2e',
    check_url: `http://127.0.0.1:${SANDBOX_PORT}/health`,
    interval_seconds: 300,
  });
  await firstCheck(request, SLUG);
});

test.afterAll(async ({ request }) => {
  await api(request, 'DELETE', `/api/applications/${SLUG}`);
});

test('losing the API keeps what is on screen, says so, and Retry recovers', async ({ page }) => {
  await signIn(page);
  await expect(page.locator('main')).toContainText('Resilience sandbox');

  // The browser stops reaching the API (as if the server went away). This is imitated in the
  // browser; the server itself keeps running.
  await page.route('**/api/**', (route) => route.abort('connectionrefused'));
  await expect(page.locator('co-error')).toBeVisible({ timeout: 45_000 });
  await expect(page.locator('main')).toContainText('Resilience sandbox');

  await page.unroute('**/api/**');
  await page.locator('co-error').getByRole('button', { name: 'Retry' }).first().click();
  await expect(page.locator('co-error')).toHaveCount(0);
});

test('a session ended elsewhere sends the next request back to sign-in', async ({
  page,
  context,
}) => {
  await signIn(page, `/applications/${SLUG}`);
  await context.clearCookies(); // the session cookie is gone, as after sign-out elsewhere or expiry
  await page.goto('/events');
  await expect(page).toHaveURL(/sign-in/);
});

test.describe('in a time zone 14 hours ahead of UTC', () => {
  test.use({ timezoneId: 'Pacific/Kiritimati' });

  test('times are shown in local time, or in UTC when chosen, and the choice is kept', async ({
    page,
    request,
  }) => {
    const checks = (await api(request, 'GET', `/api/applications/${SLUG}/checks?limit=1`)) as {
      items: { started_at: string }[];
    };
    const startedAt = new Date(checks.items[0].started_at);
    const hhmm = (d: Date, timeZone: string) =>
      d.toLocaleTimeString('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
        timeZone,
      });

    await signIn(page, `/applications/${SLUG}`);
    const firstTime = page.locator('co-checks-panel tbody tr.main co-time').first();
    await expect(firstTime).toContainText(hhmm(startedAt, 'Pacific/Kiritimati'));

    await page.getByRole('button', { name: /^Times:/ }).click();
    await expect(page.getByRole('button', { name: 'Times: UTC' })).toBeVisible();
    await expect(firstTime).toContainText(hhmm(startedAt, 'UTC'));
    await page.reload();
    await expect(page.getByRole('button', { name: 'Times: UTC' })).toBeVisible();
  });
});
