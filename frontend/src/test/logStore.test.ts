import { describe, it, expect, beforeEach } from 'vitest'
import { useLogStore, GAP_SEPARATOR_TEXT } from '../store/log'
import type { LogLine } from '../store/log'
import { LOG_CAPACITY } from '../config'

const mk = (id: string, text = `m-${id}`): LogLine => ({
  id,
  kind: 'log',
  event: 'message',
  text,
  ts: 0,
})

describe('logStore', () => {
  beforeEach(() => {
    useLogStore.getState().clear()
  })

  it('append 追加单条事件', () => {
    useLogStore.getState().append(mk('1'))
    const { lines } = useLogStore.getState()
    expect(lines).toHaveLength(1)
    expect(lines[0].id).toBe('1')
    expect(lines[0].text).toBe('m-1')
  })

  it('append 按 id 去重：同 id 二次 append 不重复插入（水位线判定）', () => {
    useLogStore.getState().append(mk('5'))
    useLogStore.getState().append(mk('5'))
    // 重放旧 id（<= 水位线）也被丢弃
    useLogStore.getState().append(mk('3'))
    expect(useLogStore.getState().lines).toHaveLength(1)
    expect(useLogStore.getState().watermark).toBe(5)
  })

  it('appendBatch 批量插入：单次 store 更新且对内部/已有事件去重', () => {
    let updates = 0
    const unsubscribe = useLogStore.subscribe(() => {
      updates += 1
    })
    try {
      useLogStore.getState().append(mk('1'))
      updates = 0
      useLogStore
        .getState()
        .appendBatch([mk('1'), mk('2'), mk('3'), mk('2'), mk('4')])
      // 批量去重后仍有新增 → 恰好一次 set
      expect(updates).toBe(1)
      expect(useLogStore.getState().lines.map((l) => l.id)).toEqual([
        '1',
        '2',
        '3',
        '4',
      ])
    } finally {
      unsubscribe()
    }
  })

  it('appendBatch 全部为重复事件时不触发 store 更新', () => {
    useLogStore.getState().append(mk('1'))
    let updates = 0
    const unsubscribe = useLogStore.subscribe(() => {
      updates += 1
    })
    try {
      useLogStore.getState().appendBatch([mk('1'), mk('1')])
      expect(updates).toBe(0)
      expect(useLogStore.getState().lines).toHaveLength(1)
    } finally {
      unsubscribe()
    }
  })

  it('容量截断：超过 capacity 时保留末尾 N 条，水位线不回退', () => {
    const list: LogLine[] = []
    for (let i = 0; i < LOG_CAPACITY + 50; i++) list.push(mk(String(i)))
    useLogStore.getState().appendBatch(list)
    const state = useLogStore.getState()
    expect(state.lines).toHaveLength(LOG_CAPACITY)
    // 首条是被截掉 50 条后的第一条
    expect(state.lines[0].id).toBe('50')
    expect(state.lines[state.lines.length - 1].id).toBe(
      String(LOG_CAPACITY + 49),
    )
    // 截断不回退水位线：重放已滚出的旧 id 仍被丢弃
    expect(state.watermark).toBe(LOG_CAPACITY + 49)
    useLogStore.getState().append(mk('10'))
    expect(useLogStore.getState().lines).toHaveLength(LOG_CAPACITY)
  })

  it('gap 事件：重置水位线 + 插入分隔行（新会话 id 从 1 重排不被误判丢弃）', () => {
    // 旧会话：id 1..3 已消费，水位线=3
    useLogStore.getState().appendBatch([mk('1'), mk('2'), mk('3')])
    expect(useLogStore.getState().watermark).toBe(3)

    // 断流重启，服务端推 event: gap
    useLogStore.getState().applyGap()
    let state = useLogStore.getState()
    expect(state.watermark).toBeNull()
    const gapLine = state.lines[state.lines.length - 1]
    expect(gapLine.kind).toBe('gap')
    expect(gapLine.text).toBe(GAP_SEPARATOR_TEXT)

    // 新会话事件 id 从 1 重新开始：若未重置水位线会被判重丢弃（fail case）
    useLogStore.getState().appendBatch([mk('1'), mk('2')])
    state = useLogStore.getState()
    expect(state.lines.map((l) => l.id)).toEqual(['1', '2', '3', gapLine.id, '1', '2'])
    expect(state.watermark).toBe(2)
  })

  it('clear 清空事件并复位水位线', () => {
    useLogStore.getState().append(mk('1'))
    useLogStore.getState().clear()
    const state = useLogStore.getState()
    expect(state.lines).toHaveLength(0)
    expect(state.watermark).toBeNull()
  })
})
