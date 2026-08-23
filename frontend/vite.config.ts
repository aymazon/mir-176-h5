/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const API_TARGET = 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // SSE 长连接（todo 5 /api/stream）：必须独立于 '/api' 配置。
      // http-proxy 基于 Node http 模块，默认即 HTTP/1.1 keep-alive；
      // 不设 timeout/proxyTimeout，避免 SSE 长连接被过早关闭。
      '/api/stream': {
        target: API_TARGET,
        changeOrigin: true,
        ws: false,
        // 浏览器直接导航（accept: text/html）误入 /api/stream 时绕过代理、
        // 回退到 SPA 入口，而不是挂起一个永远等不到事件的 SSE 连接。
        bypass(req) {
          if ((req.headers.accept ?? '').includes('text/html')) {
            return '/index.html'
          }
        },
      },
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    globals: true,
  },
})
