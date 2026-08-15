import { expect, test } from '@playwright/test';

test('homepage renders the title', async ({ page }) => {
  await page.goto('/');
  const heading = page.getByRole('heading', { name: 'Mir 1.76 H5' });
  await expect(heading).toBeVisible();
  await page.screenshot({ path: 'e2e/artifacts/smoke.png' });
});
