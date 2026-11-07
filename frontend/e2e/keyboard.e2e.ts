// The interface with a keyboard only (Phase 8): signing in, skipping the navigation, moving
// between pages, and a confirmation dialog that keeps and returns focus.
import { expect, test } from '@playwright/test';

import { OPERATOR_TOKEN } from './support';

test('signing in, skipping to content and moving between pages without a mouse', async ({
  page,
}) => {
  await page.goto('/overview');
  await expect(page).toHaveURL(/sign-in/);
  await page.locator('#token').focus();
  await page.keyboard.type(OPERATOR_TOKEN);
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/overview/);

  // The first Tab reaches the skip link, which is visible while focused and moves focus to main.
  await page.locator('body').focus();
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page.locator('main#main')).toBeFocused();

  // Navigation links are reachable with Tab and followed with Enter.
  const systemLink = page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'System' });
  for (
    let i = 0;
    i < 30 && !(await systemLink.evaluate((el) => el === document.activeElement));
    i++
  ) {
    await page.keyboard.press('Shift+Tab');
  }
  await expect(systemLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/system/);
  await expect(page.getByRole('heading', { name: 'System', level: 1 })).toBeVisible();
});

test('a confirmation dialog takes focus, keeps it, and gives it back on Escape', async ({
  page,
}) => {
  await page.goto('/system');
  await page.locator('#token').fill(OPERATOR_TOKEN);
  await page.keyboard.press('Enter');
  const open = page.getByRole('button', { name: 'Sign out everywhere' });
  await open.focus();
  await page.keyboard.press('Enter');

  const dialog = page.locator('dialog[open]');
  await expect(dialog).toBeVisible();
  // Focus starts on the harmless choice, and Tab never reaches the page behind the dialog. (Past
  // the last button the browser moves focus to its own controls, leaving the document's focus on
  // <body>; then back to the dialog.)
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab');
    const escaped = await dialog.evaluate((el) => {
      const active = document.activeElement;
      return active !== null && active !== document.body && !el.contains(active);
    });
    expect(escaped, `Tab ${i + 1} left the dialog`).toBe(false);
  }

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(open).toBeFocused();
  // Nothing was ended: the page still works.
  await page.reload();
  await expect(page).toHaveURL(/\/system/);
});
