import { create } from 'zustand'
import { LOG_CAPACITY } from '../config'

/** 日志行类型：普通事件行 / gap 分隔行 */
export type LogLineKind = 'log' | 'gap'

export interface LogLine {
  /** SSE 事件 id（gap 分隔行为本地生成的 gap-<ts>，服务端 gap 不带 id） */
  id: string
  kind: LogLineKind
  /** SSE event 字段名（message/log/stat/drop/exp/disconnect…） */
  event: string
  /** 展示文本 */
  text: string
  ts: number
}

export const GAP_SEPARATOR_TEXT = '期间部分日志丢失'

interface LogStore {
  lines: LogLine[]
  capacity: number
  /**
   * 去重水位线 = 已接受的最大数字事件 id；null 表示尚未建立水位。
   * 数字 id <= 水位线 → 判为重放/重复丢弃；
   * 收到 event: gap → 重置为 null（防新会话 id 从 1 重排被旧水位误判），
   * 非数字 id（gap 行等）走 seenNonNumeric 集合去重。
   */
  watermark: number | null
  seenNonNumeric: Set<string>
  append: (line: LogLine) => void
  /** 批量插入：单次 set → 单次 React render（重放/批量事件专用） */
  appendBatch: (lines: LogLine[]) => void
  /** 收到 event: gap：重置水位线 + 插入分隔行（单次 set 原子完成） */
  applyGap: (text?: string) => void
  clear: () => void
}

/** 数字序号判定：契约规定事件 id 为每会话单调递增序号（十进制字符串） */
function toSeq(id: string): number | null {
  if (id === '') return null
  const n = Number(id)
  return Number.isFinite(n) ? n : null
}

export const useLogStore = create<LogStore>((set) => ({
  lines: [],
  capacity: LOG_CAPACITY,
  watermark: null,
  seenNonNumeric: new Set<string>(),

  append: (line) =>
    set((state) => {
      const accepted = acceptLine(state, line)
      if (!accepted) return state
    return {
      ...commit(state, [accepted.line]),
      watermark: accepted.watermark,
      seenNonNumeric: accepted.seen,
    }
  }),

  appendBatch: (lines) =>
    set((state) => {
      if (lines.length === 0) return state
      const acceptedLines: LogLine[] = []
      let watermark = state.watermark
      const seen = state.seenNonNumeric
      for (const line of lines) {
        const seq = toSeq(line.id)
        if (seq !== null) {
          if (watermark !== null && seq <= watermark) continue
          watermark = seq
        } else {
          if (seen.has(line.id)) continue
          seen.add(line.id)
        }
        acceptedLines.push(line)
      }
      if (acceptedLines.length === 0) return state
      return {
        lines: truncate([...state.lines, ...acceptedLines], state.capacity),
        watermark,
        seenNonNumeric: seen,
      }
    }),

  applyGap: (text = GAP_SEPARATOR_TEXT) =>
    set((state) => {
      // 先重置水位线再插入分隔行——同一事务内原子完成：
      // 后续新会话事件 id 从头递增时不会被旧水位误判为已读。
      const gapLine: LogLine = {
        id: `gap-${Date.now()}`,
        kind: 'gap',
        event: 'gap',
        text,
        ts: Date.now(),
      }
      return {
        lines: truncate([...state.lines, gapLine], state.capacity),
        watermark: null,
        seenNonNumeric: new Set<string>(),
      }
    }),

  clear: () =>
    set({ lines: [], watermark: null, seenNonNumeric: new Set<string>() }),
}))

/** 单条接受判定，返回更新后的水位/集合与待插入行；拒绝返回 null */
function acceptLine(
  state: Pick<LogStore, 'watermark' | 'seenNonNumeric'>,
  line: LogLine,
): { line: LogLine; watermark: number | null; seen: Set<string> } | null {
  const seq = toSeq(line.id)
  if (seq !== null) {
    if (state.watermark !== null && seq <= state.watermark) return null
    return { line, watermark: seq, seen: state.seenNonNumeric }
  }
  if (state.seenNonNumeric.has(line.id)) return null
  const seen = new Set(state.seenNonNumeric)
  seen.add(line.id)
  return { line, watermark: state.watermark, seen }
}

function commit(
  state: Pick<LogStore, 'lines' | 'capacity'>,
  newLines: LogLine[],
): Partial<LogStore> {
  return { lines: truncate([...state.lines, ...newLines], state.capacity) }
}

/** 容量截断：保留末尾 N 条；被截掉的数字 id 不回退水位线（单调性保证） */
function truncate(lines: LogLine[], capacity: number): LogLine[] {
  return lines.length > capacity ? lines.slice(-capacity) : lines
}
