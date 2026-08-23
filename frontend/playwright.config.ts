import { defineConfig, devices } from '@playwright/test'

// 手机竖屏视口（计划验收：390×844 六 Tab 可见不溢出）。
const VIEWPORT = { width: 390, height: 844 }

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    viewport: VIEWPORT,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium-mobile',
      use: { ...devices['Pixel 7'], viewport: VIEWPORT },
    },
  ],
  // 只启动 vite dev server；后端 :8000 不由 playwright 拉起
  // （六 tab 占位页走 hash 路由，不依赖 backend）。
  webServer: {
    command: 'pnpm exec vite --port 5173 --strictPort',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
