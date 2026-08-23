import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { SseClient } from '../api/sse'
import { useLogStore } from '../store/log'

/**
 * MockEventSource：jsdom 无 EventSource，用可编程替身模拟浏览器原生行为
 * （自动重连由浏览器负责——测试里以「同一连接继续收包」或「新实例」表达重连后的流）。
 */
class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  withCredentials: boolean
  readyState = 1
  private listeners = new Map<
    string,
    Array<(ev: { lastEventId: string; data: string }) => void>
  >()

  constructor(url: string, options?: { withCredentials?: boolean }) {
    this.url = url
    this.withCredentials = options?.withCredentials ?? false
    MockEventSource.instances.push(this)
  }

  addEventListener(
    name: string,
    fn: (ev: { lastEventId: string; data: string }) => void,
  ): void {
    const list = this.listeners.get(name) ?? []
    list.push(fn)
    this.listeners.set(name, list)
  }

  /** 测试辅助：模拟服务端推来一条事件 */
  emit(name: string, lastEventId: string, data: string): void {
    for (const fn of this.listeners.get(name) ?? []) {
      fn({ lastEventId, data })
    }
  }

  close(): void {
    this.readyState = 2
  }
}

const msg = (n: number | string) =>
  JSON.stringify({ msg: `hit-${n}`, kind: 'combat' })

async function drain(): Promise<void> {
  // 微任务边界：等待批量 flush 完成
  await Promise.resolve()
  await Promise.resolve()
}

describe('SseClient', () => {
  let client: SseClient

  beforeEach(() => {
    vi.stubGlobal('EventSource', MockEventSource)
    MockEventSource.instances = []
    useLogStore.getState().clear()
    client = new SseClient()
  })

  afterEach(() => {
    client.close()
    vi.unstubAllGlobals()
  })

  it('connect 建立 withCredentials 连接', () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    expect(es).toBeDefined()
    expect(es.url).toBe('/api/stream')
    expect(es.withCredentials).toBe(true)
  })

  it('同一事件循环内多条消息合并为一次 appendBatch（单次 store 更新）', async () => {
    let updates = 0
    const unsubscribe = useLogStore.subscribe(() => {
      updates += 1
    })
    try {
      client.connect('/api/stream')
      const es = MockEventSource.instances[0]
      es.emit('message', '1', msg(1))
      es.emit('message', '2', msg(2))
      es.emit('log', '3', msg(3))
      expect(updates).toBe(0) // 尚未到微任务边界

      await drain()
      expect(updates).toBe(1)
      const state = useLogStore.getState()
      expect(state.lines.map((l) => l.id)).toEqual(['1', '2', '3'])
    } finally {
      unsubscribe()
    }
  })

  it('事件按 id 去重：Last-Event-ID 重放不产生重复行', async () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    es.emit('message', '1', msg(1))
    es.emit('message', '2', msg(2))
    await drain()

    // 断线重连后服务端从 Last-Event-ID=2 重放 → id 1/2 被水位线丢弃
    es.emit('message', '1', msg(1))
    es.emit('message', '2', msg(2))
    es.emit('message', '3', msg(3))
    await drain()

    const ids = useLogStore.getState().lines.map((l) => l.id)
    expect(ids).toEqual(['1', '2', '3'])
  })

  it('收到 event: gap → 先重置水位线再插入分隔行；新会话 id 重排不被误判丢弃', async () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]

    // 会话 A：id 1..3
    es.emit('message', '1', msg(1))
    es.emit('message', '2', msg(2))
    es.emit('message', '3', msg(3))
    await drain()
    expect(useLogStore.getState().watermark).toBe(3)

    // 服务端重启（buffer 滚出）→ 推 event: gap（不带 id）
    es.emit('gap', '', JSON.stringify({ separator: true }))
    await drain()
    let state = useLogStore.getState()
    expect(state.watermark).toBeNull()
    expect(state.lines[state.lines.length - 1].kind).toBe('gap')

    // 会话 B：id 从 1 重新递增——未重置水位线时这里会被全部误判丢弃（fail case）
    es.emit('message', '1', msg('b1'))
    es.emit('message', '2', msg('b2'))
    await drain()

    state = useLogStore.getState()
    const texts = state.lines.map((l) => l.text)
    expect(texts.filter((t) => t === 'hit-b1')).toHaveLength(1)
    expect(texts.filter((t) => t === 'hit-b2')).toHaveLength(1)
    // 分隔行恰好一条，且位于会话 B 事件之前
    const gapIdx = state.lines.findIndex((l) => l.kind === 'gap')
    expect(gapIdx).toBeGreaterThan(-1)
    expect(state.lines[gapIdx + 1].text).toBe('hit-b1')
  })

  it('非 JSON payload 包装为 { raw } 展示', async () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    es.emit('message', '9', 'plain-text-not-json')
    await drain()
    const line = useLogStore.getState().lines[0]
    expect(line.id).toBe('9')
    expect(line.text).toContain('plain-text-not-json')
  })
})
