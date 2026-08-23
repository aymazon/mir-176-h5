import { useLogStore } from '../store/log'
import type { LogLine } from '../store/log'
import { handleUnauthorized } from './rest'

/**
 * SSE 客户端封装（GET /api/stream，todo 5 契约）：
 *  - 浏览器原生 EventSource 自动重连（onerror 不接管重连逻辑）
 *  - 事件按 id 走 log store 水位线去重
 *  - 收到 named event: gap → 先重置去重水位线，再插入「期间部分日志丢失」分隔行
 *  - 同一事件循环内的多条消息合并为一次 appendBatch（单次 store 更新防重渲染风暴）
 *  - onerror 时探测 /api/health：若 401 → 清登录态并跳登录页；
 *    其余错误（断流/重启中）静默，交给浏览器自动重连
 */

export const SSE_STREAM_PATH = '/api/stream'
/** 与 todo 7 契约的事件类型清单对齐；gap 单独处理 */
const SSE_EVENT_NAMES = ['message', 'log', 'stat', 'drop', 'exp', 'disconnect']
/** 401 探测端点（todo 3 提供 /api/health） */
const HEALTH_PROBE_PATH = '/api/health'

export class SseClient {
  private es: EventSource | null = null
  /** 待批量提交缓冲 + 微任务调度标记 */
  private pending: LogLine[] = []
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
    const text =
      typeof payload.msg === 'string'
        ? payload.msg
        : typeof payload.text === 'string'
          ? payload.text
          : JSON.stringify(payload)
    this.pending.push({
      id: lastEventId,
      kind: 'log',
      event,
      text,
      ts: Date.now(),
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
    useLogStore.getState().appendBatch(batch)
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

/** 应用级单例（AppShell 挂载时 connect） */
export const sseClient = new SseClient()
