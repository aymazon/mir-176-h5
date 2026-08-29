import { expect, test } from '@playwright/test'

/**
 * todo 36/38 部署实证：dell1 生产环境真机冒烟（https://dell1:8443，自签证书）
 *  注册（API）→ UI 登录 → API 建号 → 战斗页挂机 30s（SSE 日志滚动 + HP 心跳变化）
 *  → 截图留证
 *
 * 与 idle-smoke.spec.ts 的差异：本 spec 打真实部署（nginx 8443 → uvicorn 127.0.0.1:8000），
 * 不走 vite dev server；baseURL/ignoreHTTPSErrors 用 test.use 覆盖全局配置
 * （全局 config 的 webServer 仍会拉起 5173，本 spec 不访问它——playwright 限制，
 *  CLI 无法跳过 webServer，保留以维持 `pnpm exec playwright test <spec>` 命令形态）。
 */
test.use({
  baseURL: 'https://dell1:8443',
  ignoreHTTPSErrors: true,
})

test.setTimeout(180_000)

const PASSWORD = 'e2e-dell1-123'

test('dell1 冒烟：注册→登录→建号→战斗页挂机30s→截图', async ({ page }) => {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
  const username = `d38_${suffix}`
  const charName = `烟${suffix.slice(-4)}`

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

  // 重载进战斗页：AppShell 会话恢复拿到角色 → SSE 连接 → 服务端 tick loop 启动
  await page.goto('/battle')
  await expect(page.getByTestId('header-character')).toContainText(charName)

  // --- 挂机 30s：SSE 日志流持续滚动（经 nginx 8443 的真实链路逐条到达）---
  const logBox = page.getByTestId('log-scroll-box')
  await expect
    .poll(
      async () => logBox.locator('[data-testid^="log-line-"]').count(),
      { timeout: 20_000, intervals: [1_000] },
    )
    .toBeGreaterThan(0)
  const linesBefore = await logBox.locator('[data-testid^="log-line-"]').count()
  await page.waitForTimeout(30_000)
  const linesAfter = await logBox.locator('[data-testid^="log-line-"]').count()
  expect(linesAfter, '30s 挂机日志持续滚动（SSE live 实证）').toBeGreaterThan(linesBefore)

  // stat 心跳驱动 HP 数值变动（承伤/回复每 tick 发生）
  await expect(page.getByTestId('stat-hp')).toBeVisible()
  // 怪物血条出现（银杏村鸡群交战）
  await expect(page.getByTestId('monster-list')).toBeVisible({ timeout: 60_000 })

  await page.screenshot({ path: '../.omo/evidence/task-38-mir-176-h5-mvp-battle.png' })
})
