import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, act, within } from '@testing-library/react'
import MonsterRow from '../ui/scene/MonsterRow'
import { useSceneStore, DYING_LINGER_MS } from '../store/scene'
import type { SceneSnapshotPayload } from '../store/scene'

const T0 = 1_000_000

type MonsterRowPayload = SceneSnapshotPayload['monsters'][number]

const mkMonster = (
  id: string,
  over: Partial<MonsterRowPayload> = {},
): MonsterRowPayload => ({
  id,
  monster_id: 'chicken',
  name: `怪-${id}`,
  hp: 50,
  max_hp: 100,
  boss: false,
  is_target: false,
  ...over,
})

const mkScene = (monsters: MonsterRowPayload[]): SceneSnapshotPayload => ({
  map_id: 'wild',
  monsters,
  pets: [],
})

const apply = (monsters: MonsterRowPayload[]): void => {
  act(() => {
    useSceneStore.getState().applyScene(mkScene(monsters))
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(T0)
  useSceneStore.getState().resetScene()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('MonsterRow', () => {
  it('8 只怪全部渲染，无溢出 chip；新挂载挂 scene-spawn-pop', () => {
    apply(Array.from({ length: 8 }, (_, i) => mkMonster(`m${i + 1}`)))
    render(<MonsterRow />)
    for (let i = 1; i <= 8; i++) {
      expect(screen.getByTestId(`scene-monster-m${i}`)).toBeInTheDocument()
    }
    expect(
      screen.queryByTestId('scene-monster-overflow'),
    ).not.toBeInTheDocument()
    expect(screen.getByTestId('scene-monster-m1').className).toContain(
      'scene-spawn-pop',
    )
  })

  it('9 只怪渲染前 8 只 + 「+1 只」溢出 chip', () => {
    apply(Array.from({ length: 9 }, (_, i) => mkMonster(`m${i + 1}`)))
    render(<MonsterRow />)
    for (let i = 1; i <= 8; i++) {
      expect(screen.getByTestId(`scene-monster-m${i}`)).toBeInTheDocument()
    }
    expect(screen.queryByTestId('scene-monster-m9')).not.toBeInTheDocument()
    expect(screen.getByTestId('scene-monster-overflow')).toHaveTextContent(
      '+1 只',
    )
  })

  it('boss（payload.boss 强制升档）金环 + 👑 + 金色名字；target 琥珀环 + scale-105 + 目标标签', () => {
    apply([
      // 视觉表 chicken=normal，payload.boss=true → 强制 boss 呈现
      mkMonster('boss', { monster_id: 'chicken', name: '鸡皇', boss: true, hp: 40, max_hp: 80 }),
      mkMonster('tgt', { is_target: true }),
      mkMonster('plain'),
    ])
    render(<MonsterRow />)

    const boss = screen.getByTestId('scene-monster-boss')
    // 👑 冠标 + 金环（emoji 身体）+ 金色名字
    expect(within(boss).getByText('👑')).toBeInTheDocument()
    expect(boss.querySelector('.ring-amber-400')).not.toBeNull()
    expect(within(boss).getByText('鸡皇').className).toContain('text-amber-300')
    // HP 条宽 = 40/80 = 50%
    const fill = boss.querySelector('.bg-gradient-to-r') as HTMLElement
    expect(fill.style.width).toBe('50%')

    const target = screen.getByTestId('scene-monster-tgt')
    expect(target.className).toContain('ring-amber-300')
    expect(target.className).toContain('scale-105')
    expect(within(target).getByText('目标')).toBeInTheDocument()

    // 普通怪：无环无标签
    const plain = screen.getByTestId('scene-monster-plain')
    expect(plain.className).not.toContain('ring-amber-300')
    expect(within(plain).queryByText('目标')).not.toBeInTheDocument()
    expect(within(plain).queryByText('👑')).not.toBeInTheDocument()
  })

  it('dyingUntil 置位的怪挂 scene-fade-sink class', () => {
    apply([mkMonster('m1'), mkMonster('m2')])
    apply([mkMonster('m2')]) // m1 消失 → dying 滞留
    render(<MonsterRow />)
    const dying = screen.getByTestId('scene-monster-m1')
    expect(dying.className).toContain('scene-fade-sink')
    expect(dying.className).not.toContain('scene-spawn-pop')
    expect(screen.getByTestId('scene-monster-m2').className).not.toContain(
      'scene-fade-sink',
    )
  })

  it('死亡动画 animationend → sweepDying 快路径清扫过期条目', () => {
    apply([mkMonster('m1'), mkMonster('m2')])
    apply([mkMonster('m2')])
    vi.advanceTimersByTime(DYING_LINGER_MS + 1) // dyingUntil 过期
    render(<MonsterRow />)
    const dying = screen.getByTestId('scene-monster-m1')
    fireEvent.animationEnd(dying)
    expect(useSceneStore.getState().monsters.map((m) => m.id)).toEqual(['m2'])
    expect(screen.queryByTestId('scene-monster-m1')).not.toBeInTheDocument()
  })
})
