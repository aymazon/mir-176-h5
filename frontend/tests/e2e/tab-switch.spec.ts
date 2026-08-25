import { expect, test } from '@playwright/test'

// 六个底部 Tab（ui/layout.tsx TABS 同源清单）
const TABS = ['battle', 'inventory', 'equip', 'skill', 'quest', 'teleport'] as const

// 占位页不依赖后端：拦截会话恢复与健康探针，避免并行开发时后端缺席/401 干扰
test.beforeEach(async ({ context }) => {
  await context.route('**/api/character/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    }),
  )
  await context.route('**/api/health', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )
  // todo 31 起背包/装备页挂载时拉全量背包（真实后端在跑会返回 401 → 跳登录干扰）
  await context.route('**/api/inventory/bag', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], gold: 0, capacity: 40, used: 0 }),
    }),
  )
})

for (const tab of TABS) {
  test(`tab-switch: ${tab} 渲染且不溢出 (390x844)`, async ({ page }) => {
    await page.goto(`/${tab}`)

    // 底部 Tab Bar 对应项可见
    await expect(page.locator(`[data-testid="tab-${tab}"]`)).toBeVisible()
    await expect(page.locator(`[data-testid="tab-panel-${tab}"]`)).toBeVisible()

    // 主内容区宽度不超过视口；页面无横向溢出
    const main = page.locator('[data-testid="app-shell-main"]')
    await expect(main).toBeVisible()
    const box = await main.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.width).toBeLessThanOrEqual(390)
    const scrollWidth = await page.evaluate(
      () => document.documentElement.scrollWidth,
    )
    expect(scrollWidth).toBeLessThanOrEqual(390)
  })
}

test('六 Tab 连续切换后落地战斗页并留存冒烟截图', async ({ page }) => {
  await page.goto('/battle')

  for (const tab of TABS) {
    await page.locator(`[data-testid="tab-${tab}"]`).click()
    await expect(page).toHaveURL(new RegExp(`/${tab}$`))
    await expect(page.locator(`[data-testid="tab-panel-${tab}"]`)).toBeVisible()
  }

  // 回到战斗页作为代表帧
  await page.locator('[data-testid="tab-battle"]').click()
  await expect(page.locator('[data-testid="tab-panel-battle"]')).toBeVisible()

  await page.screenshot({
    path: '../.omo/evidence/task-6-mir-176-h5-mvp.png',
    fullPage: false,
  })
})
