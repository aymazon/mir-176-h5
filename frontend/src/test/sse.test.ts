import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { SseClient } from '../api/sse'
import { useLogStore } from '../store/log'
import { useGameStore } from '../store/game'
import { useSceneStore } from '../store/scene'

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
    useGameStore.getState().clearAuth()
    useSceneStore.getState().resetScene()
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

  it('scene 事件 → scene store 快照整体入账（不落日志行，仅推进水位线）', async () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    es.emit(
      'scene',
      '7',
      JSON.stringify({
        map_id: 'biqi-wild',
        monsters: [
          { id: 'm1', monster_id: 'pheasant', name: '小野雉', hp: 5, max_hp: 8, boss: false, is_target: true },
          { id: 'm2', monster_id: 'pheasant', name: '小野雉', hp: 8, max_hp: 8, boss: false, is_target: false },
        ],
        pets: [{ id: 'p1', type: 'dog', name: '旺财', hp: 10, max_hp: 10 }],
      }),
    )
    await drain()

    const s = useSceneStore.getState()
    expect(s.mapId).toBe('biqi-wild')
    expect(s.monsters.map((m) => m.id)).toEqual(['m1', 'm2'])
    expect(s.monsters[0]).toMatchObject({ name: '小野雉', hp: 5, maxHp: 8, isTarget: true })
    expect(s.pets).toHaveLength(1)
    // 面板化事件不落日志行，但 id 推进去重水位线
    expect(useLogStore.getState().lines).toHaveLength(0)
    expect(useLogStore.getState().watermark).toBe(7)
  })

  it('log 事件 kind → 场景动作入队；未知 kind 忽略', async () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    es.emit(
      'log',
      '1',
      JSON.stringify({ kind: 'attack', target_id: 'm1', dmg: 3, text: '你命中小野雉，造成 3 点伤害（剩余 2）' }),
    )
    es.emit('log', '2', JSON.stringify({ kind: 'system', text: '欢迎回来' }))
    await drain()

    const actions = useSceneStore.getState().actions
    expect(actions).toHaveLength(1)
    expect(actions[0]).toMatchObject({ kind: 'attack', targetId: 'm1', dmg: 3 })
    // log 文本渲染不受影响：两条均落日志行
    expect(useLogStore.getState().lines.map((l) => l.text)).toEqual([
      '你命中小野雉，造成 3 点伤害（剩余 2）',
      '欢迎回来',
    ])
  })

  it('zone_change → game 切图 + scene resetScene 清空场景', async () => {
    useGameStore.getState().setGame({
      player: { id: 'p1', username: 'tester' },
      character: {
        id: 'c1',
        name: '勇者',
        class: 'warrior',
        level: 1,
        hp: 20,
        mp: 8,
        gold: 0,
        bindCity: 'yin-xing-cun',
        mapId: 'yin-xing-cun',
      },
    })
    useSceneStore.getState().applyScene({
      map_id: 'yin-xing-cun',
      monsters: [
        { id: 'm1', monster_id: 'pheasant', name: '小野雉', hp: 8, max_hp: 8, boss: false, is_target: true },
      ],
      pets: [],
    })
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    es.emit('zone_change', '20', JSON.stringify({ map_id: 'biqi-wild' }))
    await drain()

    expect(useGameStore.getState().character?.mapId).toBe('biqi-wild')
    const s = useSceneStore.getState()
    expect(s.mapId).toBeNull()
    expect(s.monsters).toEqual([])
    expect(s.pets).toEqual([])
    expect(s.actions).toEqual([])
  })

  it('gap → 重置水位线并 resetScene；disconnect 不重置场景（重连靠 replay + 权威快照自愈）', async () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    const seed = () =>
      useSceneStore.getState().applyScene({
        map_id: 'yin-xing-cun',
        monsters: [
          { id: 'm1', monster_id: 'pheasant', name: '小野雉', hp: 8, max_hp: 8, boss: false, is_target: true },
        ],
        pets: [],
      })
    seed()

    // gap 同步处理：applyGap（重置水位线 + 分隔行）之后 resetScene
    es.emit('gap', '', JSON.stringify({ separator: true }))
    expect(useSceneStore.getState().mapId).toBeNull()
    expect(useSceneStore.getState().monsters).toEqual([])
    expect(useLogStore.getState().watermark).toBeNull()

    // disconnect 不 reset：断线宽限内事件服务端续存，重连 replay + 权威快照自愈
    seed()
    es.emit('disconnect', '30', JSON.stringify({ reason: 'server-bye' }))
    await drain()
    expect(useSceneStore.getState().monsters).toHaveLength(1)
    expect(useLogStore.getState().lines.at(-1)?.text).toBe('连接已断开')
  })

  it('travel_start/pet_summon/pet_died/pet_dispersed 已注册：不落日志行、仅推进水位线', async () => {
    client.connect('/api/stream')
    const es = MockEventSource.instances[0]
    es.emit('travel_start', '1', JSON.stringify({ to: 'biqi-wild' }))
    es.emit('pet_summon', '2', JSON.stringify({ id: 'p1', type: 'dog' }))
    es.emit('pet_died', '3', JSON.stringify({ id: 'p1' }))
    es.emit('pet_dispersed', '4', JSON.stringify({ id: 'p1' }))
    await drain()

    expect(useLogStore.getState().lines).toHaveLength(0)
    expect(useLogStore.getState().watermark).toBe(4)
  })
})
