import { expect, test } from '@playwright/test'

/**
 * todo 14 战斗场景 E2E（390×844，真实后端 127.0.0.1:8000 经 vite proxy）：
 *  注册 → UI 登录 → API 建角色 → 战斗页挂机：
 *    1. battle-scene 可见
 *    2. 场景地图名正确（scene-map-name）
 *    3. scene-monster-count 文本变为「怪物 ×N」（scene store 收到怪物快照）
 *    4. HP 条宽度 < 100%（stat-hp-bar，战斗承伤发生）
 *    5. 击杀事件（日志出现「击杀」or 怪物计数归零）
 *    6. 全页截图留证
 */
test.setTimeout(300_000)

const PASSWORD = 'e2e-pass-14'

test('战斗场景：登录→挂机→怪物出现→承伤→击杀→截图', async ({ page }) => {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
  const username = `e2e_14_${suffix}`
  const charName = `斗${suffix.slice(-4)}`

  // --- 注册（register 不发 cookie，登录才发）---
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

  // --- API 建角色 ---
  const created = await page.request.post('/api/character', {
    data: { name: charName, class: 'warrior' },
  })
  expect(created.status()).toBe(201)

  // 重载：AppShell 会话恢复拿到角色 → SSE 连接 → 服务端 tick loop 启动
  await page.goto('/battle')
  await expect(page.getByTestId('header-character')).toContainText(charName)

  // --- 1. battle-scene 容器可见 ---
  await expect(page.getByTestId('battle-scene')).toBeVisible({ timeout: 10_000 })

  // --- 2. 场景地图名正确 ---
  await expect(page.getByTestId('scene-map-name')).toContainText('月华村')

  // --- 3. 等待怪物出现（scene-monster-count 文本变为「怪物 ×N」，最多 60s）---
  await expect(page.getByTestId('scene-monster-count')).toContainText('怪物', {
    timeout: 60_000,
  })

  // --- 4. HP 条宽度 < 100%（战斗承伤发生，stat-hp-bar 来自 BattleTab StatBar）---
  const hpBar = page.getByTestId('stat-hp-bar')
  await expect(hpBar).toBeVisible()

  await expect
    .poll(
      async () => {
        const width = await hpBar.evaluate((el) => {
          const style = el.getAttribute('style') ?? ''
          const match = style.match(/width:\s*([\d.]+)%/)
          return match ? parseFloat(match[1]) : 100
        })
        return width
      },
      { timeout: 60_000, intervals: [1_000, 2_000] },
    )
    .toBeLessThan(100)

  // --- 5. 等待击杀事件：日志含「击杀」or 怪物计数归零 ---
  const logBox = page.getByTestId('log-scroll-box')
  await expect
    .poll(
      async () => {
        // 条件 A：日志中出现「击杀」文本
        const logText = await logBox.innerText().catch(() => '')
        if (logText.includes('击杀')) return true

        // 条件 B：scene-monster-count 恢复「暂无交战目标」
        const countText = await page.getByTestId('scene-monster-count').innerText()
        if (countText.includes('暂无交战目标')) return true

        return false
      },
      { timeout: 60_000, intervals: [2_000, 3_000] },
    )
    .toBe(true)

  // --- 6. 全页截图 ---
  await page.screenshot({ path: '../.omo/evidence/task-14-battle-scene-ui.png', fullPage: true })
})
