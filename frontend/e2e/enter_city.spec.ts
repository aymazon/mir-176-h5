// W1T7 acceptance e2e: register → create character → enter city (M3 Biqi) →
// real click-move → portal walk to M1 → screenshot + 10s rAF fps evidence.

import * as fs from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

declare const crypto: { getRandomValues: (array: Uint8Array) => Uint8Array };

function randomHex8(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

interface SpawnProbe {
  map_id: string;
  x: number;
  y: number;
  level: number;
}

function readChar(page: Page) {
  return page.evaluate<SpawnProbe | null>(() => {
    const character = window.__mir176?.char;
    if (character === undefined) return null;
    return { map_id: character.map_id, x: character.x, y: character.y, level: character.level };
  });
}

test('register → create warrior → enter city → click-move → portal to M1', async ({ page }) => {
  test.setTimeout(180_000);
  const suffix = randomHex8();
  const username = `e2e_${suffix}`;
  const password = 'e2epass123';
  const charName = `e2e_${suffix}`;

  await page.goto('/');

  await page.getByRole('tab', { name: '注册' }).click();
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: '注册', exact: true }).click();

  await expect(page.getByRole('heading', { name: '角色选择' })).toBeVisible();

  await page.getByLabel('角色名').first().fill(charName);
  await page.getByLabel('职业').selectOption('warrior');
  await page.getByRole('button', { name: '创建', exact: true }).click();
  await expect(page.getByRole('button', { name: '进入' })).toBeVisible();

  await page.getByRole('button', { name: '进入' }).click();

  const canvas = page.locator('canvas');
  await expect(canvas).toBeVisible();

  // New characters spawn at Biqi city center M3(150,150) — never M1 (D23).
  await expect
    .poll(() => readChar(page), { timeout: 15_000 })
    .toEqual({ map_id: 'm3', x: 150, y: 150, level: 1 });

  // Real click 3 tiles left of the centered character → server path → snapshots.
  const box = (await canvas.boundingBox()) as { x: number; y: number; width: number; height: number };
  await page.mouse.click(box.x + box.width / 2 - 96, box.y + box.height / 2);
  await expect
    .poll(async () => (await readChar(page))?.x ?? 9999, { timeout: 10_000 })
    .toBeLessThan(150);

  // Portal walk: a screen click cannot reach M3(12,150) from (150,150).
  await page.evaluate(() => {
    window.__mir176?.move(12, 150);
  });
  await expect
    .poll(() => readChar(page), { timeout: 90_000 })
    .toEqual(expect.objectContaining({ map_id: 'm1', x: 55, y: 30 }));

  await page.screenshot({ path: 'e2e/artifacts/enter_city.png' });

  const fps = await page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        let frames = 0;
        const start = performance.now();
        const tick = (): void => {
          frames += 1;
          const elapsed = performance.now() - start;
          if (elapsed < 10_000) {
            requestAnimationFrame(tick);
          } else {
            resolve(frames / (elapsed / 1000));
          }
        };
        requestAnimationFrame(tick);
      }),
  );
  fs.mkdirSync('e2e/artifacts', { recursive: true });
  fs.writeFileSync('e2e/artifacts/fps.json', `${JSON.stringify({ fps }, null, 2)}\n`);
  expect(fps).toBeGreaterThanOrEqual(30);
});
