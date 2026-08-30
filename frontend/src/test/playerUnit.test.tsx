import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import PlayerUnit from '../ui/scene/PlayerUnit'
import PetUnit from '../ui/scene/PetUnit'
import { useGameStore } from '../store/game'
import type { CharacterSnapshot } from '../store/game'
import { useSceneStore } from '../store/scene'
import type { SceneSnapshotPayload } from '../store/scene'

const mkCharacter = (
  patch: Partial<CharacterSnapshot> = {},
): CharacterSnapshot => ({
  id: 'c1',
  name: '小道士',
  class: 'taoist',
  level: 22,
  hp: 30,
  mp: 10,
  gold: 0,
  bindCity: '比奇城',
  maxHp: 100,
  maxMp: 40,
  ...patch,
})

const mkPet = (
  id: string,
  type = 'dog',
  hp = 50,
  maxHp = 50,
): SceneSnapshotPayload['pets'][number] => ({
  id,
  type,
  name: `宠-${id}`,
  hp,
  max_hp: maxHp,
})

const mkScene = (
  pets: SceneSnapshotPayload['pets'],
): SceneSnapshotPayload => ({ map_id: 'wild', monsters: [], pets })

function renderUnits() {
  return render(
    <>
      <PlayerUnit />
      <PetUnit />
    </>,
  )
}

beforeEach(() => {
  useGameStore.getState().setGame({ character: mkCharacter() })
  useSceneStore.getState().resetScene()
})

afterEach(() => {
  cleanup()
  useGameStore.getState().clearAuth()
  useSceneStore.getState().resetScene()
})

describe('PlayerUnit + PetUnit', () => {
  it('道士 + 2 宠物：渲染 ☯️ 头像、2 个宠物头像与 HP 微条', () => {
    useSceneStore
      .getState()
      .applyScene(mkScene([mkPet('p1', 'dog'), mkPet('p2', 'skull')]))

    renderUnits()

    // 玩家：道士 emoji + 名字/等级
    const player = screen.getByTestId('player-unit')
    expect(player).toHaveTextContent('☯️')
    expect(player).toHaveTextContent('小道士')
    expect(player).toHaveTextContent('Lv.22')

    // 宠物：2 只，emoji 按 type 映射
    expect(screen.getByTestId('pet-p1')).toHaveTextContent('🐕')
    expect(screen.getByTestId('pet-p2')).toHaveTextContent('💀')

    // HP 微条：玩家与宠物各自存在
    expect(screen.getByTestId('player-hp-bar')).toBeInTheDocument()
    expect(screen.getByTestId('pet-hp-p1')).toBeInTheDocument()
    expect(screen.getByTestId('pet-hp-p2')).toBeInTheDocument()
    // 无溢出角标
    expect(screen.queryByTestId('pet-overflow-badge')).not.toBeInTheDocument()
  })

  it('4 宠物：仅渲染前 3 只并显示 +1 角标', () => {
    useSceneStore
      .getState()
      .applyScene(
        mkScene([mkPet('p1'), mkPet('p2'), mkPet('p3'), mkPet('p4', 'charm')]),
      )

    renderUnits()

    expect(screen.getByTestId('pet-p1')).toBeInTheDocument()
    expect(screen.getByTestId('pet-p2')).toBeInTheDocument()
    expect(screen.getByTestId('pet-p3')).toBeInTheDocument()
    expect(screen.queryByTestId('pet-p4')).not.toBeInTheDocument()
    expect(screen.getByTestId('pet-overflow-badge')).toHaveTextContent('+1')
  })

  it('HP 微条宽度反映 character HP 比例（30/100 → 30%）', () => {
    renderUnits()

    expect(screen.getByTestId('player-hp-bar')).toHaveStyle('width: 30%')
    // MP 10/40 → 25%
    expect(screen.getByTestId('player-mp-bar')).toHaveStyle('width: 25%')
  })

  it('defend 动作存在期间玩家单位挂 scene-shake 与红色 vignette', () => {
    renderUnits()

    // 无动作时不带受击 class
    expect(screen.getByTestId('player-unit')).not.toHaveClass('scene-shake')
    expect(
      screen.queryByTestId('player-hit-vignette'),
    ).not.toBeInTheDocument()

    act(() => {
      useSceneStore.getState().pushLogAction({ kind: 'defend', dmg: 5 })
    })

    expect(screen.getByTestId('player-unit')).toHaveClass('scene-shake')
    expect(screen.getByTestId('player-hit-vignette')).toBeInTheDocument()
  })

  it('skill 动作存在期间头像挂 scene-glow-pulse', () => {
    renderUnits()
    expect(screen.getByTestId('player-avatar')).not.toHaveClass(
      'scene-glow-pulse',
    )

    act(() => {
      useSceneStore
        .getState()
        .pushLogAction({ kind: 'skill', skill_id: 'liehuo' })
    })

    expect(screen.getByTestId('player-avatar')).toHaveClass(
      'scene-glow-pulse',
    )
  })
})
