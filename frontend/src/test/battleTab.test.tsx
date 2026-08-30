import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import BattleTab from '../ui/BattleTab'
import { useLogStore } from '../store/log'
import { useGameStore } from '../store/game'
import type { LogLine } from '../store/log'

function seedLog(lines: LogLine[]): void {
  useLogStore.getState().clear()
  useLogStore.getState().appendBatch(lines)
}

const line = (id: string, text: string): LogLine => ({
  id,
  kind: 'log',
  event: 'message',
  text,
  ts: 0,
})

beforeEach(() => {
  useLogStore.getState().clear()
  useGameStore.getState().clearAuth()
  useGameStore.getState().setGame({
    player: { id: 'p1', username: 'tester' },
    character: {
      id: 'c1',
      name: '勇者',
      class: 'warrior',
      level: 1,
      hp: 20,
      mp: 8,
      gold: 120,
      bindCity: 'yin-xing-cun',
      mapId: 'yin-xing-cun',
      exp: 40,
      maxHp: 24,
      maxMp: 10,
    },
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('BattleTab', () => {
  it('渲染人物 HP/MP/经验条与金币（stat 快照可视化）', () => {
    render(<BattleTab />)
    expect(screen.getByTestId('stat-hp')).toHaveTextContent('20/24')
    expect(screen.getByTestId('stat-mp')).toHaveTextContent('8/10')
    expect(screen.getByTestId('stat-exp')).toHaveTextContent('40/100')
    expect(screen.getByTestId('stat-gold')).toHaveTextContent('120 金')
    // 血条宽度按百分比渲染
    expect(screen.getByTestId('stat-hp-bar').style.width).toBe(
      `${(20 / 24) * 100}%`,
    )
  })

  it('当前地图高亮 + 邻接区域节点（纯 CSS 抽象图）', () => {
    render(<BattleTab />)
    expect(screen.getByTestId('map-current')).toHaveTextContent('月华村')
    // 月华村邻接：边境原野
    expect(screen.getByTestId('map-node-biqi-wild')).toHaveTextContent('边境原野')
  })

  it('日志流渲染普通行与 gap 分隔行', () => {
    seedLog([
      line('1', '你命中小野雉'),
      {
        id: 'gap-1',
        kind: 'gap',
        event: 'gap',
        text: '期间部分日志丢失',
        ts: 0,
      },
      line('2', '获得经验'),
    ])
    render(<BattleTab />)
    expect(screen.getByTestId('log-line-1')).toHaveTextContent('你命中小野雉')
    expect(screen.getByTestId('log-gap')).toHaveTextContent('期间部分日志丢失')
    expect(screen.getByTestId('log-line-2')).toHaveTextContent('获得经验')
  })

  it('空日志展示占位行', () => {
    render(<BattleTab />)
    expect(screen.getByTestId('log-empty')).toBeInTheDocument()
  })

  it('用户上滑暂停自动滚动并出现「回到底部」，点击恢复', () => {
    seedLog([line('1', 'a'), line('2', 'b')])
    render(<BattleTab />)
    const box = screen.getByTestId('log-scroll-box')
    Object.defineProperty(box, 'scrollHeight', {
      value: 500,
      configurable: true,
    })
    Object.defineProperty(box, 'clientHeight', {
      value: 100,
      configurable: true,
    })
    Object.defineProperty(box, 'scrollTop', {
      value: 0,
      writable: true,
      configurable: true,
    })

    // 模拟向上滚动离开底部
    ;(box as HTMLElement).scrollTop = 50
    fireEvent.scroll(box)
    expect(screen.getByTestId('log-resume')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('log-resume'))
    expect(screen.queryByTestId('log-resume')).not.toBeInTheDocument()
  })
})
