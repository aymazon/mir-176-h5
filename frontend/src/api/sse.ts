import { useLogStore } from '../store/log'
import type { LogLine } from '../store/log'
import { useGameStore } from '../store/game'
import { MAPS } from '../data/gamedata'
import { handleUnauthorized } from './rest'

/**
 * SSE 客户端封装（GET /api/stream，todo 5 契约）：
 *  - 浏览器原生 EventSource 自动重连（onerror 不接管重连逻辑）
 *  - 事件按 id 走 log store 水位线去重
 *  - 收到 named event: gap → 先重置去重水位线，再插入「期间部分日志丢失」分隔行
 *  - 同一事件循环内的多条消息合并为一次 appendBatch（单次 store 更新防重渲染风暴）；
 *    todo 31 起结构化事件在同一微任务边界批量分发：
 *      stat → 人物血条/面板；zone_change → 地图切换；log(kind) → 怪物血条派生；
 *      drop → 地面掉落池。重放风暴下每帧至多一次 log set + 一次 game set。
 *  - onerror 时探测 /api/health：若 401 → 清登录态并跳登录页；
 *    其余错误（断流/重启中）静默，交给浏览器自动重连
 */

export const SSE_STREAM_PATH = '/api/stream'
/** 与 todo 7 契约的事件类型清单对齐；gap 单独处理 */
const SSE_EVENT_NAMES = ['message', 'log', 'stat', 'drop', 'exp', 'disconnect']
/** 401 探测端点（todo 3 提供 /api/health） */
const HEALTH_PROBE_PATH = '/api/health'

interface PendingEntry {
  /** null = 面板化事件不落日志行（id 仍推进去重水位线） */
  line: LogLine | null
  lineId: string
  event: string
  payload: Record<string, unknown>
}

export class SseClient {
  private es: EventSource | null = null
  /** 待批量提交缓冲 + 微任务调度标记 */
  private pending: PendingEntry[] = []
  private flushScheduled = false

  connect(url: string = SSE_STREAM_PATH): void {
    if (this.es) return
    const es = new EventSource(url, { withCredentials: true })
    this.es = es

    for (const name of SSE_EVENT_NAMES) {
      es.addEventListener(name, (ev) => {
        this.enqueue(name, (ev as MessageEvent<string>).lastEventId, ev.data as string)
      })
    }

    // gap 不带 id：先重置水位线再插入分隔行（原子落在 store.applyGap）
    es.addEventListener('gap', () => {
      useLogStore.getState().applyGap()
    })

    es.onerror = () => {
      void this.probeUnauthorized()
      // 浏览器原生自动重连，此处不手动重建连接
    }
  }

  close(): void {
    this.es?.close()
    this.es = null
    this.pending = []
    this.flushScheduled = false
  }

  /**
   * 收包入队；微任务边界统一 flush → appendBatch 单次 set。
   * 重放窗口一次性涌入数百条时只触发一次 React render。
   */
  private enqueue(event: string, lastEventId: string, rawData: string): void {
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(rawData) as Record<string, unknown>
    } catch {
      payload = { raw: rawData }
    }
    const text = renderText(event, payload)
    this.pending.push({
      line:
        text === null
          ? null
          : { id: lastEventId, kind: 'log', event, text, ts: Date.now() },
      lineId: lastEventId,
      event,
      payload,
    })
    if (!this.flushScheduled) {
      this.flushScheduled = true
      queueMicrotask(() => this.flush())
    }
  }

  private flush(): void {
    this.flushScheduled = false
    if (this.pending.length === 0) return
    const batch = this.pending
    this.pending = []

    const lines: LogLine[] = []
    const skippedIds: string[] = []
    for (const e of batch) {
      if (e.line === null) skippedIds.push(e.lineId)
      else lines.push(e.line)
    }
    if (lines.length > 0) {
      useLogStore.getState().appendBatch(lines)
    }
    if (skippedIds.length > 0) {
      useLogStore.getState().advanceBatch(skippedIds)
    }

    // --- 结构化事件批量分发（同一帧内完成，React 自动批合并渲染） ---------------
    const game = useGameStore.getState()
    const combatEntries: Array<{ kind: string; text: string }> = []
    const lootEntries: Array<Record<string, unknown>> = []
    let lastStat: Record<string, unknown> | null = null

    for (const { event, payload } of batch) {
      if (event === 'zone_change' && typeof payload.map_id === 'string') {
        game.applyZoneChange(payload.map_id)
      } else if (event === 'log' && typeof payload.kind === 'string') {
        combatEntries.push({ kind: payload.kind, text: String(payload.text ?? '') })
      } else if (event === 'drop' && Array.isArray(payload.loot)) {
        lootEntries.push(...(payload.loot as Array<Record<string, unknown>>))
      } else if (event === 'stat') {
        lastStat = payload // 快照语义：一帧多条只取最后一条
      }
    }
    if (combatEntries.length > 0) game.applyCombatLogs(combatEntries)
    if (lootEntries.length > 0) game.applyDropLoot(lootEntries)
    if (lastStat !== null) game.applyStat(lastStat)
  }

  /** EventSource 不暴露 HTTP 状态码：出错时用 REST 探针区分 401 与普通断流 */
  private async probeUnauthorized(): Promise<void> {
    try {
      const res = await fetch(HEALTH_PROBE_PATH, {
        credentials: 'include',
        method: 'GET',
      })
      if (res.status === 401) {
        handleUnauthorized()
      }
    } catch {
      // 网络不可达/后端重启中：非 401 场景，交由浏览器自动重连
    }
  }
}

/**
 * 事件 → 日志行文本：
 *  - message/log 取 msg/text 字段；
 *  - stat/exp 不再落原始 JSON 行（血条/经验条已可视化，kill 日志自带经验数字）；
 *  - zone_change/drop/disconnect 合成为人话短句。
 */
function renderText(event: string, payload: Record<string, unknown>): string | null {
  if (event === 'message' || event === 'log') {
    if (typeof payload.msg === 'string') return payload.msg
    if (typeof payload.text === 'string') return payload.text
    return JSON.stringify(payload)
  }
  if (event === 'zone_change') {
    const mapId = typeof payload.map_id === 'string' ? payload.map_id : ''
    const name = MAPS[mapId]?.name ?? mapId
    return `前往 ${name || '未知区域'}`
  }
  if (event === 'drop') {
    const parts: string[] = []
    const loot = Array.isArray(payload.loot)
      ? (payload.loot as Array<Record<string, unknown>>)
      : []
    for (const entry of loot) {
      if (entry.gold !== undefined) parts.push(`金币×${String(entry.gold)}`)
      else if (entry.picked === true)
        parts.push(`${String(entry.item_id)}×${String(entry.qty ?? 1)} 已入包`)
      else if (entry.picked === false)
        parts.push(`${String(entry.item_id)}×${String(entry.qty ?? 1)} 落地`)
    }
    return parts.length > 0 ? `战利品：${parts.join('、')}` : null
  }
  if (event === 'disconnect') return '连接已断开'
  // stat / exp / 其余结构化事件由对应面板呈现，不进日志流
  return null
}

/** 应用级单例（AppShell 挂载时 connect） */
export const sseClient = new SseClient()
