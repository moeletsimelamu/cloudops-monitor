// Every screen, on a desktop and a phone, in light and dark: no script or console errors, no failed
// API requests, no horizontal scrolling, and no serious or critical accessibility violations.
// The data is real: the sandbox is registered and checked through the API first.
import { expect, test } from '@playwright/test';

import {
  SANDBOX_PORT,
  api,
  firstCheck,
  seriousViolations,
  setSandboxMode,
  signIn,
} from './support';

const SLUG = 'screens-sandbox';
const SCREENS = [
  '/overview',
  '/applications',
  '/applications/new',
  `/applications/${SLUG}`,
  `/applications/${SLUG}?tab=deployments`,
  `/applications/${SLUG}?tab=logs`,
  `/applications/${SLUG}?tab=configuration`,
  '/health',
  '/deployments',
  '/logs',
  '/events',
  '/system',
  '/no-such-page',
];
const SIZES = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'phone', width: 390, height: 844 },
];

test.beforeAll(async ({ request }) => {
  await setSandboxMode(request, 'healthy');
  await api(request, 'POST', '/api/applications', {
    slug: SLUG,
    name: 'Screens sandbox',
    environment: 'e2e',
    check_url: `http://127.0.0.1:${SANDBOX_PORT}/health`,
    interval_seconds: 300,
  });
  await firstCheck(request, SLUG);
});

test.afterAll(async ({ request }) => {
  await api(request, 'DELETE', `/api/applications/${SLUG}`);
});

for (const scheme of ['light', 'dark'] as const) {
  for (const size of SIZES) {
    test(`every screen, ${size.name}, ${scheme}`, async ({ browser }) => {
      const context = await browser.newContext({
        viewport: { width: size.width, height: size.height },
        colorScheme: scheme,
      });
      const page = await context.newPage();
      const problems: string[] = [];
      let screen = 'sign-in';
      page.on('pageerror', (e) => problems.push(`${screen}: page error: ${e.message}`));
      page.on('console', (m) => {
        if (m.type() === 'error') problems.push(`${screen}: console: ${m.text()}`);
      });
      page.on('response', (r) => {
        if (
          r.url().includes('/api/') &&
          r.status() >= 400 &&
          !r.url().endsWith('/api/auth/session')
        ) {
          problems.push(`${screen}: HTTP ${r.status()} ${r.url()}`);
        }
      });

      await signIn(page);
      for (const path of SCREENS) {
        screen = path;
        await page.goto(path);
        await expect(page.locator('h1').first()).toBeVisible();
        await page.waitForLoadState('networkidle');
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `${path} scrolls sideways`).toBeLessThanOrEqual(0);
        expect(await seriousViolations(page), `${path} accessibility`).toEqual([]);
      }
      expect(problems).toEqual([]);
      await context.close();
    });
  }
}
