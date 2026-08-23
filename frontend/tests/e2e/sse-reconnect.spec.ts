import { expect, test } from '@playwright/test'
import * as http from 'node:http'

/**
 * fail case 实证（真浏览器 EventSource + 真 SSE 服务断流重启）：
 *  会话 A 推 id 1..3 → 杀服务 → 浏览器原生自动重连 →
 *  会话 B 先推 event: gap（不带 id）再从 id 1 重新递增。
 *  断言：分隔行出现；gap 后 id 1..3 不被旧水位线误判丢弃；
 *        全流无重复渲染（每 id 恰好 pre/post 各一次）。
 *
 * 复用 vite.config.ts 的固定代理目标 127.0.0.1:8000（todo 7 约定，不改）。
 * 若端口被真实后端占用则跳过（逻辑由 src/test/sse.test.ts 兜底覆盖）。
 */

const PORT = 8000

type ServerFactory = (onStream: (res: http.ServerResponse) => void) => Promise<http.Server>

const startServer: ServerFactory = (onStream) =>
  new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      if (req.url?.startsWith('/api/stream')) {
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        })
        onStream(res)
        return
      }
      // /api/health 等探针：非 401 即可（避免误触发跳登录）
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end('{}')
    })
    server.once('error', reject)
    server.listen(PORT, '127.0.0.1', () => resolve(server))
  })

async function killServer(server: http.Server): Promise<void> {
  // 断开全部 SSE 长连接，浏览器侧立即感知断流并按 retry 字段重连
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
}

function streamSession(res: http.ServerResponse, opts: {
  gapFirst: boolean
  count: number
  prefix: string
}): void {
  res.write('retry: 300\n\n')
  if (opts.gapFirst) {
    // gap 不带 id（todo 5 契约），客户端据此重置去重水位线
    res.write(`event: gap\ndata: {"separator":true}\n\n`)
  }
  let n = 0
  const timer = setInterval(() => {
    n += 1
    res.write(
      `id: ${n}\nevent: message\ndata: ${JSON.stringify({ msg: `${opts.prefix}-${n}` })}\n\n`,
    )
    if (n >= opts.count) clearInterval(timer)
  }, 80)
  res.on('close', () => clearInterval(timer))
}

test('SSE 断流重启：自动重连 + gap 重置水位线 + 日志无重复 id', async ({ page }) => {
  let serverA: http.Server
  try {
    serverA = await startServer((res) =>
      streamSession(res, { gapFirst: false, count: 3, prefix: 'hit-a' }),
    )
  } catch {
    test.skip(true, '127.0.0.1:8000 被占用（真实后端在跑），SSE 场景由 vitest sse.test.ts 覆盖')
    return
  }

  try {
    await page.goto('/battle')

    // 会话 A：id 1..3 全部到达
    await expect(page.getByTestId('log-line-3')).toBeVisible({ timeout: 10_000 })

    // 断流：杀掉会话 A 服务
    await killServer(serverA)

    // 会话 B 同端口重启：先 gap 再从 id 1 重新递增（模拟 buffer 滚出后的新会话）
    const serverB = await startServer((res) =>
      streamSession(res, { gapFirst: true, count: 3, prefix: 'hit-b' }),
    )
    try {
      // 浏览器原生自动重连（retry: 300ms）→ 收到 gap 分隔行
      await expect(page.getByTestId('log-gap')).toBeVisible({ timeout: 15_000 })

      // gap 后新会话事件 id 从 1 重排：必须全部渲染而非被旧水位丢弃
      await expect(page.getByTestId('log-line-3')).toHaveCount(2, { timeout: 15_000 })

      // 全流顺序：pre-gap 1..3 → gap → post-gap 1..3；每个 id 恰好出现两次（无重复推送）
      const ids = await page.$$eval(
        '[data-testid^="log-line-"]',
        (els) =>
          els.map((e) => e.getAttribute('data-testid')!.replace('log-line-', '')),
      )
      expect(ids).toEqual(['1', '2', '3', '1', '2', '3'])

      // post-gap 段内容来自会话 B（文本前缀 hit-b-*），证明不是旧缓存
      const texts = await page.$$eval('[data-testid^="log-line-"]', (els) =>
        els.map((e) => e.textContent ?? ''),
      )
      expect(texts.slice(3).join(' ')).toContain('hit-b')
      expect(texts.slice(0, 3).join(' ')).toContain('hit-a')
    } finally {
      await killServer(serverB)
    }
  } finally {
    await killServer(serverA).catch(() => {})
  }
})
