import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import BattleScene from '../ui/scene/BattleScene'
import { useSceneStore } from '../store/scene'
import type { SceneSnapshotPayload } from '../store/scene'
import { useGameStore } from '../store/game'

const mkScene = (
  mapId: string,
  monsters: SceneSnapshotPayload['monsters'],
): SceneSnapshotPayload => ({
  map_id: mapId,
  monsters,
  pets: [],
})

const mkMonster = (
  id: string,
): SceneSnapshotPayload['monsters'][number] => ({
  id,
  monster_id: 'chicken',
  name: `怪-${id}`,
  hp: 50,
  max_hp: 100,
  boss: false,
  is_target: false,
})

const seedGame = (mapId: string): void => {
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
      gold: 0,
      bindCity: mapId,
      mapId,
      exp: 0,
      maxHp: 24,
      maxMp: 10,
    },
  })
}

beforeEach(() => {
  useSceneStore.getState().resetScene()
  seedGame('yin-xing-cun')
})

afterEach(() => {
  cleanup()
})

describe('BattleScene', () => {
  it('渲染容器与标题栏：地图名 + 区域渐变主题 + 等级区间', () => {
    render(<BattleScene />)
    expect(screen.getByTestId('battle-scene')).toBeInTheDocument()
    expect(screen.getByTestId('scene-map-name')).toHaveTextContent('月华村')
    // newbie 区域主题渐变
    expect(screen.getByTestId('scene-bg').className).toContain(
      'from-emerald-900',
    )
    expect(screen.getByTestId('scene-bg').className).toContain(
      'bg-gradient-to-b',
    )
    // 月华村 Lv.0–7
    expect(screen.getByTestId('battle-scene')).toHaveTextContent('Lv.0–7')
  })

  it('2 只怪时计数显示 2', () => {
    act(() => {
      useSceneStore
        .getState()
        .applyScene(mkScene('yin-xing-cun', [mkMonster('m1'), mkMonster('m2')]))
    })
    render(<BattleScene />)
    expect(screen.getByTestId('scene-monster-count')).toHaveTextContent('2')
  })

  it('0 只怪时显示「暂无交战目标」', () => {
    render(<BattleScene />)
    expect(screen.getByTestId('scene-monster-count')).toHaveTextContent(
      '暂无交战目标',
    )
  })

  it('未知 mapId：兜底 default 主题 + 原始 mapId 文本，不崩溃', () => {
    seedGame('nowhere-land')
    act(() => {
      useSceneStore.getState().applyScene(mkScene('nowhere-land', []))
    })
    render(<BattleScene />)
    expect(screen.getByTestId('scene-map-name')).toHaveTextContent(
      'nowhere-land',
    )
    // default 主题渐变
    expect(screen.getByTestId('scene-bg').className).toContain(
      'from-slate-900',
    )
    expect(screen.getByTestId('battle-scene')).toBeInTheDocument()
  })
})
