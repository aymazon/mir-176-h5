import { expect, test } from '@playwright/test'

/**
 * F3: Real manual QA on dell1 — full flow
 */

test.use({
  baseURL: 'https://dell1:8443',
  ignoreHTTPSErrors: true,
})

test.setTimeout(600_000)

const PASSWORD = 'e2e-f3-123'
const SCREENSHOT_DIR = '../.omo/evidence/f3-mir-176-h5-mvp'

test('F3 完整流程真机验收', async ({ page }) => {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
  const accounts = {
    warrior: { name: `f3a_${suffix}`, char: `烟A${suffix.slice(-4)}` },
    wizard:  { name: `f3b_${suffix}`, char: `烟B${suffix.slice(-4)}` },
    taoist:  { name: `f3c_${suffix}`, char: `烟C${suffix.slice(-4)}` },
  }

  // Step 1: 注册三账号
  for (const cls of ['warrior', 'wizard', 'taoist'] as const) {
    const reg = await page.request.post('/api/auth/register', {
      data: { username: accounts[cls].name, password: PASSWORD },
    })
    expect(reg.status(), `${cls} 注册应 201`).toBe(201)
    const login = await page.request.post('/api/auth/login', {
      data: { username: accounts[cls].name, password: PASSWORD },
    })
    expect(login.status(), `${cls} 登录应 200`).toBe(200)
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-01-registered-3-accounts.png` })

  // Step 2: 三账号建不同职业
  for (const cls of ['warrior', 'wizard', 'taoist'] as const) {
    await page.request.post('/api/auth/login', {
      data: { username: accounts[cls].name, password: PASSWORD },
    })
    const c = await page.request.post('/api/character', {
      data: { name: accounts[cls].char, class: cls },
    })
    expect(c.status(), `${cls} 建号应 201`).toBe(201)
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-02-three-classes-created.png` })

  // Step 3: 单账号单角色约束验证 (wizard 已建号, 再建应 409)
  await page.request.post('/api/auth/login', {
    data: { username: accounts.wizard.name, password: PASSWORD },
  })
  const dup = await page.request.post('/api/character', {
    data: { name: accounts.wizard.char + '_2', class: 'taoist' },
  })
  expect(dup.status(), '同账号二次建号应 409').toBe(409)

  // Step 4: UI 登录 warrior
  await page.context().clearCookies()
  await page.goto('/login')
  await page.getByTestId('login-username').fill(accounts.warrior.name)
  await page.getByTestId('login-password').fill(PASSWORD)
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/battle$/)

  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-03-warrior-battle-start.png` })

  // Step 5: 60s 挂机
  const logBox = page.getByTestId('log-scroll-box')
  await expect
    .poll(
      async () => logBox.locator('[data-testid^="log-line-"]').count(),
      { timeout: 20_000, intervals: [1_000] },
    )
    .toBeGreaterThan(0)
  const linesBefore = await logBox.locator('[data-testid^="log-line-"]').count()
  await page.waitForTimeout(60_000)
  const linesAfter = await logBox.locator('[data-testid^="log-line-"]').count()
  expect(linesAfter, '60s 挂机日志应持续滚动').toBeGreaterThan(linesBefore)

  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-04-battle-after-60s.png` })

  // Step 6: 背包 tab
  await page.goto('/inventory')
  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-05-inventory-tab.png` })

  // Step 7: 装备 tab
  await page.goto('/equip')
  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-06-equip-tab.png` })

  // Step 8: 技能 tab
  await page.goto('/skill')
  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-07-skill-tab.png` })

  // Step 9: 任务 tab
  await page.goto('/quest')
  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-08-quest-tab.png` })

  // Step 10: 传送 tab
  await page.goto('/teleport')
  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-09-teleport-tab.png` })

  // Step 11: 断网 10s 重连
  await page.goto('/battle')
  await expect(page.getByTestId('log-scroll-box')).toBeVisible()

  await page.context().setOffline(true)
  await page.waitForTimeout(10_000)
  await page.context().setOffline(false)

  const linesBeforeReconnect = await logBox.locator('[data-testid^="log-line-"]').count()
  await page.waitForTimeout(15_000)
  const linesAfterReconnect = await logBox.locator('[data-testid^="log-line-"]').count()
  expect(
    linesAfterReconnect,
    '断网 10s 重连后日志应继续增长（战斗继续）',
  ).toBeGreaterThan(linesBeforeReconnect)

  await page.screenshot({ path: `${SCREENSHOT_DIR}/f3-10-after-reconnect.png` })
})