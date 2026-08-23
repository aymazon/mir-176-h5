import { expect, test } from '@playwright/test'

// 验收：mock 401 → 清本地登录态并跳登录页（REST 拦截器真实路径）

test('会话恢复接口返回 401 → 自动跳转 /login', async ({ page }) => {
  await page.route('**/api/character/me', (route) =>
    route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }),
  )
  await page.route('**/api/health', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )

  await page.goto('/battle')

  // AppShell 挂载即调 GET /api/character/me → 401 → handleUnauthorized()
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByTestId('login-page')).toBeVisible()
})

test('登录表单提交返回 401 → 停留登录页并展示错误', async ({ page }) => {
  await page.route('**/api/auth/login', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '账号或密码错误' }),
    }),
  )

  await page.goto('/login')
  await page.getByTestId('login-username').fill('tester')
  await page.getByTestId('login-password').fill('wrong-pass')
  await page.getByTestId('login-submit').click()

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByTestId('login-error')).toContainText('账号或密码错误')
})
