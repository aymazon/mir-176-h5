// W1T8 / M0 ③ acceptance e2e: two full players enter Biqi city on the same
// cell, see each other's sprites (via the __mir176.entities hook), and A's
// chat message crosses over to B's ChatPanel (and back to A's own panel).

import { expect, test, type Browser, type Page } from '@playwright/test';

declare const crypto: { getRandomValues: (array: Uint8Array) => Uint8Array };

const PASSWORD = 'e2epass123';

interface DebugEntity {
  id: number;
  name: string;
  position: { x: number; y: number };
}

interface SpawnProbe {
  id: number;
  map_id: string;
  x: number;
  y: number;
}

function randomHex8(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function readChar(page: Page) {
  return page.evaluate<SpawnProbe | null>(() => {
    const character = window.__mir176?.char;
    if (character === undefined) return null;
    return { id: character.id, map_id: character.map_id, x: character.x, y: character.y };
  });
}

function readEntities(page: Page): Promise<DebugEntity[]> {
  return page.evaluate(() => (window.__mir176?.entities ?? []) as DebugEntity[]);
}

async function journeyIntoCity(browser: Browser, username: string): Promise<Page> {
  const context = await browser.newContext({ viewport: { width: 375, height: 667 } });
  const page = await context.newPage();

  await page.goto('/');
  await page.getByRole('tab', { name: '注册' }).click();
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(PASSWORD);
  await page.getByRole('button', { name: '注册', exact: true }).click();

  await expect(page.getByRole('heading', { name: '角色选择' })).toBeVisible();

  await page.getByLabel('角色名').first().fill(username);
  await page.getByLabel('职业').selectOption('warrior');
  await page.getByRole('button', { name: '创建', exact: true }).click();
  await expect(page.getByRole('button', { name: '进入' })).toBeVisible();

  await page.getByRole('button', { name: '进入' }).click();
  await expect(page.locator('canvas')).toBeVisible();

  // New characters spawn at Biqi city center M3(150,150) (D23).
  await expect
    .poll(() => readChar(page), { timeout: 15_000 })
    .toEqual(expect.objectContaining({ map_id: 'm3', x: 150, y: 150 }));

  return page;
}

test('two players see each other and chat crosses over', async ({ browser }) => {
  test.setTimeout(120_000);

  const usernameA = `e2e_${randomHex8()}`;
  const usernameB = `e2e_${randomHex8()}`;
  const pages: Page[] = [];

  try {
    const pageA = await journeyIntoCity(browser, usernameA);
    pages.push(pageA);
    const pageB = await journeyIntoCity(browser, usernameB);
    pages.push(pageB);

    const charA = (await readChar(pageA)) as SpawnProbe;
    const charB = (await readChar(pageB)) as SpawnProbe;
    // Both spawn on the same cell (150,150): each is inside the other's
    // vision radius of 12, so snapshots carry the other within ~200 ms.
    await expect
      .poll(async () => (await readEntities(pageA)).some((entity) => entity.id === charB.id), {
        timeout: 15_000,
      })
      .toBeTruthy();
    await expect
      .poll(async () => (await readEntities(pageB)).some((entity) => entity.id === charA.id), {
        timeout: 15_000,
      })
      .toBeTruthy();

    const chatInput = pageA.getByLabel('聊天');
    await chatInput.fill('你好 e2e');
    await chatInput.press('Enter');

    // B's panel shows the line with A's character name; A's own panel
    // shows its own message too (chat broadcasts to all in-game sessions).
    await expect
      .poll(() => pageB.locator('.chat__log').textContent(), { timeout: 10_000 })
      .toContain(`${usernameA}: 你好 e2e`);
    await expect
      .poll(() => pageA.locator('.chat__log').textContent(), { timeout: 10_000 })
      .toContain(`${usernameA}: 你好 e2e`);

    await pageA.screenshot({ path: 'e2e/artifacts/chat_two_clients.png' });
  } finally {
    for (const page of pages) {
      await page.context().close().catch(() => undefined);
    }
  }
});
