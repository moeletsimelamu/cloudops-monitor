import AxeBuilder from '@axe-core/playwright';
import { expect, type APIRequestContext, type Page } from '@playwright/test';

export const API_PORT = 8320;
export const SANDBOX_PORT = 8330;
export const SANDBOX = `http://127.0.0.1:${SANDBOX_PORT}`;
// A test value for this throwaway server, not a secret.
export const OPERATOR_TOKEN = 'e2e-operator-token-0123456789-abcdefghijkl';

export type SandboxMode = 'healthy' | 'degraded' | 'errors' | 'slow' | 'unavailable' | 'flaky';

/** Switch the sandbox's real behaviour (allowed from this machine without a token). */
export async function setSandboxMode(request: APIRequestContext, mode: SandboxMode): Promise<void> {
  const response = await request.put(`${SANDBOX}/control/mode`, { data: { mode } });
  expect(response.status()).toBe(200);
}

/** Call the CloudOps API as the operator, outside the browser (for setting up data). */
export async function api(
  request: APIRequestContext,
  method: 'GET' | 'POST' | 'DELETE',
  path: string,
  data?: unknown,
): Promise<unknown> {
  const response = await request.fetch(path, {
    method,
    data,
    headers: { Authorization: `Bearer ${OPERATOR_TOKEN}` },
  });
  expect(response.ok(), `${method} ${path}: ${response.status()}`).toBe(true);
  return response.status() === 204 ? null : response.json();
}

/** The worker checks a newly registered application straight away; wait for that check. */
export async function firstCheck(request: APIRequestContext, slug: string): Promise<void> {
  await expect
    .poll(async () => {
      const page = (await api(request, 'GET', `/api/applications/${slug}/checks?limit=1`)) as {
        items: unknown[];
      };
      return page.items.length;
    })
    .toBe(1);
}

export async function signIn(page: Page, path = '/overview'): Promise<void> {
  await page.goto(path);
  await page.locator('#token').fill(OPERATOR_TOKEN);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).not.toHaveURL(/sign-in/);
}

/** Problems a user would never see but should not exist: script errors and console errors. */
export function watchForErrors(page: Page): string[] {
  const problems: string[] = [];
  page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(`console: ${message.text()}`);
  });
  return problems;
}

/** Serious and critical accessibility violations on the current screen. */
export async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.length} element(s): ${v.help}`);
}
