import { expect, test } from '@playwright/test'

/**
 * todo 31 真机冒烟（390×844，真实后端 127.0.0.1:8000 经 vite proxy）：
 *  注册 → UI 登录 → API 建角色 → 战斗页挂机 ≥10s（日志滚动 + 血条变化 + 怪物血条）
 *  → 攒金币买药（POST /api/shop/buy）→ 背包页数量+1 → 买木刃穿戴 → 装备页显示 → 三页截图
 *
 * 商店无独立 Tab（六 Tab 契约），买入按验收口径直接走 REST；
 * 页面断言全部经由 UI（背包格/动作单/装备槽）。
 */

test.setTimeout(300_000)

const PASSWORD = 'e2e-pass-123'
const POTION_PRICE = 40 // healer_npc potion_hp_small
const SWORD_PRICE = 50 // yinxing_blacksmith wooden_sword

async function bagGold(request: import('@playwright/test').APIRequestContext): Promise<number> {
  const res = await request.get('/api/inventory/bag')
  if (!res.ok()) return -1
  const bag = (await res.json()) as { gold: number }
  return bag.gold
}

async function waitGoldAtLeast(
  request: import('@playwright/test').APIRequestContext,
  target: number,
  timeoutMs: number,
): Promise<void> {
  await expect
    .poll(() => bagGold(request), { timeout: timeoutMs, intervals: [2_000, 5_000] })
    .toBeGreaterThanOrEqual(target)
}

test('挂机冒烟：登录→战斗10s→买药→背包+1→穿戴武器→装备页', async ({ page }) => {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
  const username = `e2e_${suffix}`
  const charName = `勇${suffix.slice(-4)}`

  // --- 注册（register 不发 cookie，登录才发——learnings task 7）---
  const reg = await page.request.post('/api/auth/register', {
    data: { username, password: PASSWORD },
  })
  expect(reg.status(), '注册应成功（随机后缀防撞名）').toBe(201)

  // --- UI 登录 ---
  await page.goto('/login')
  await page.getByTestId('login-username').fill(username)
  await page.getByTestId('login-password').fill(PASSWORD)
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/battle$/)

  // --- API 建角色（cookie 已在浏览器上下文；page.request 共享）---
  const created = await page.request.post('/api/character', {
    data: { name: charName, class: 'warrior' },
  })
  expect(created.status()).toBe(201)

  // 重载：AppShell 会话恢复拿到角色 → SSE 连接 → 服务端 tick loop 启动
  await page.goto('/battle')
  await expect(page.getByTestId('header-character')).toContainText(charName)

  // --- 挂机 ≥10s：日志流滚动 ---
  const logBox = page.getByTestId('log-scroll-box')
  await expect
    .poll(
      async () => logBox.locator('[data-testid^="log-line-"]').count(),
      { timeout: 20_000, intervals: [1_000] },
    )
    .toBeGreaterThan(0)
  const linesBefore = await logBox.locator('[data-testid^="log-line-"]').count()
  await page.waitForTimeout(10_000)
  const linesAfter = await logBox.locator('[data-testid^="log-line-"]').count()
  expect(linesAfter, '10s 内日志持续滚动').toBeGreaterThan(linesBefore)

  // --- 血条变化：stat 心跳驱动 HP 数值变动（承伤/回复每 tick 发生）---
  const hpInitial = await page.getByTestId('stat-hp').innerText()
  await expect
    .poll(async () => page.getByTestId('stat-hp').innerText(), {
      timeout: 30_000,
      intervals: [1_000],
    })
    .not.toBe(hpInitial)

  // 怪物出现在月华村（scene-monster-count 文本变为「怪物 ×N」）
  await expect(page.getByTestId('scene-monster-count')).toContainText('怪物', { timeout: 60_000 })

  // 地图名：月华村
  await expect(page.getByTestId('scene-map-name')).toContainText('月华村')

  await page.screenshot({ path: '../.omo/evidence/task-31-battle.png' })

  // --- 攒金币买药（挂机继续，金币来自鸡掉落）---
  await waitGoldAtLeast(page.request, POTION_PRICE, 240_000)
  const bought = await page.request.post('/api/shop/buy', {
    data: { npc_id: 'healer_npc', item_id: 'potion_hp_small', quantity: 1 },
  })
  expect(bought.status(), JSON.stringify(await bought.json())).toBe(200)
  const buyOut = (await bought.json()) as { quantity_in_bag: number }

  // --- 背包页：止血草药数量 = quantity_in_bag ---
  await page.getByTestId('tab-inventory').click()
  await expect(page.getByTestId('tab-panel-inventory')).toBeVisible()
  const potionCell = page
    .locator('[data-testid^="bag-cell-"]', { hasText: '止血草药（小）' })
    .first()
  await expect(potionCell).toBeVisible({ timeout: 10_000 })
  if (buyOut.quantity_in_bag > 1) {
    // 堆叠数量角标（qty=1 不渲染角标）
    await expect(potionCell.getByTestId(/^bag-qty-/)).toContainText(
      `×${buyOut.quantity_in_bag}`,
    )
  }
  // 空格计数可见
  await expect(page.getByTestId('bag-empty-count')).toContainText(/\d+/)

  await page.screenshot({ path: '../.omo/evidence/task-31-inventory.png' })

  // --- 买木刃并穿戴（背包动作单点击式）---
  await waitGoldAtLeast(page.request, SWORD_PRICE, 240_000)
  const swordBought = await page.request.post('/api/shop/buy', {
    data: { npc_id: 'yinxing_blacksmith', item_id: 'wooden_sword', quantity: 1 },
  })
  expect(swordBought.status(), JSON.stringify(await swordBought.json())).toBe(200)

  // 重进背包页触发 refreshBag 拉新行
  await page.getByTestId('tab-battle').click()
  await page.getByTestId('tab-inventory').click()
  const swordCell = page
    .locator('[data-testid^="bag-cell-"]', { hasText: '木刃' })
    .first()
  await expect(swordCell).toBeVisible({ timeout: 10_000 })
  await swordCell.click()
  await expect(page.getByTestId('action-sheet')).toBeVisible()
  await page.getByTestId('act-wear').click()
  await expect(page.getByTestId('toast')).toContainText('已穿戴 木刃')

  // --- 装备页：武器槽显示木刃 + 耐久条 ---
  await page.getByTestId('tab-equip').click()
  await expect(page.getByTestId('equip-item-weapon')).toContainText('木刃')
  await expect(page.getByTestId('durability-text-weapon')).toContainText('/4000')
  await expect(page.getByTestId('unequip-weapon')).toBeVisible()

  await page.screenshot({ path: '../.omo/evidence/task-31-equip.png' })
})
