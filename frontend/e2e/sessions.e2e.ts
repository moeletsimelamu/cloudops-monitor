// Ending every session from the System page, seen from two separate browsers (Phase 8).
import { expect, test } from '@playwright/test';

import { signIn, watchForErrors } from './support';

test('"Sign out everywhere" asks first, then ends the session in every browser', async ({
  browser,
}) => {
  const laptop = await browser.newContext();
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const here = await laptop.newPage();
  const there = await phone.newPage();
  const problems = watchForErrors(here);
  await signIn(here, '/system');
  await signIn(there, '/overview');

  const open = here.getByRole('button', { name: 'Sign out everywhere' });
  const dialog = here.locator('dialog[open]');

  // Cancel changes nothing.
  await open.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  await there.reload();
  await expect(there).toHaveURL(/\/overview/);

  // Confirming ends this browser's session and the other one's.
  await open.click();
  await dialog.getByRole('button', { name: 'Sign out everywhere' }).click();
  await expect(here).toHaveURL(/sign-in/);
  await there.goto('/system');
  await expect(there).toHaveURL(/sign-in/);

  // The old session cookie is no use either: the API refuses it.
  const replay = await there.request.get('/api/system/config');
  expect(replay.status()).toBe(401);

  // Signing in again works as before.
  await signIn(here, '/system');
  await expect(here.getByRole('heading', { name: 'Sessions' })).toBeVisible();
  // A poll already on its way when the session ended may come back 401; the browser reports
  // that as a failed resource load. Nothing else may appear.
  expect(problems.filter((p) => !/status of 401 \(Unauthorized\)/.test(p))).toEqual([]);

  await laptop.close();
  await phone.close();
});
