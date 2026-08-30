import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import ActionLayer from '../ui/scene/ActionLayer'
import { AnchorProvider, useAnchor } from '../ui/scene/anchorContext'
import { useSceneStore } from '../store/scene'

/** 假锚点：注册 id → 元素（jsdom 下 getBoundingClientRect 全 0，位置断言不依赖它） */
function FakeAnchor({ id }: { id: string }) {
  const ref = useAnchor(id)
  return <div ref={ref} data-testid={`anchor-${id}`} />
}

function renderLayer(anchorIds: string[] = ['m1']) {
  return render(
    <AnchorProvider>
      {anchorIds.map((id) => (
        <FakeAnchor key={id} id={id} />
      ))}
      <ActionLayer />
    </AnchorProvider>,
  )
}

beforeEach(() => {
  useSceneStore.getState().resetScene()
})

afterEach(() => {
  cleanup()
  useSceneStore.getState().resetScene()
  vi.unstubAllGlobals()
})

describe('ActionLayer', () => {
  it('attack 动作：目标位浮出 -12（amber），animationEnd 后队列清空', () => {
    act(() => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 12 })
    })

    renderLayer()

    const float = screen.getByTestId('action-attack')
    expect(float).toHaveTextContent('-12')
    expect(float).toHaveClass('text-amber-300')
    expect(float).toHaveClass('scene-dmg-float')

    fireEvent.animationEnd(float)

    expect(useSceneStore.getState().actions).toHaveLength(0)
  })

  it('同帧双 attack 同目标：store 合并为一条，只渲染一个浮字', () => {
    act(() => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 5 })
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 9 })
    })

    renderLayer()

    expect(useSceneStore.getState().actions).toHaveLength(1)
    const floats = screen.getAllByTestId('action-attack')
    expect(floats).toHaveLength(1)
    expect(floats[0]).toHaveTextContent('-9')
  })

  it('skill 动作：玩家位横幅 = emoji + 技能名（去除「你施放了」前缀）', () => {
    act(() => {
      useSceneStore.getState().pushLogAction({
        kind: 'skill',
        skill_id: 'liehuo',
        text: '你施放了焚焰剑法',
      })
    })

    renderLayer()

    const banner = screen.getByTestId('action-skill')
    expect(banner).toHaveTextContent('🔥 焚焰剑法')
    expect(banner).toHaveClass('scene-banner-pop')
    expect(banner).toHaveClass('text-red-500')
  })

  it('pet_kill 动作：用 pet_id 定位宠物锚点，浮出 +exp 经验', () => {
    act(() => {
      useSceneStore.getState().pushLogAction({
        kind: 'pet_kill',
        pet_id: 'p1',
        target_id: 'm1',
        exp: 25,
      })
    })

    renderLayer(['p1'])

    const float = screen.getByTestId('action-pet_kill')
    expect(float).toHaveTextContent('+25 经验')
    expect(float).toHaveClass('text-green-400')
  })

  it('reduced-motion：matchMedia 返回 reduce → rAF 一帧后队列清空', async () => {
    const mql = {
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue(mql))

    act(() => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 12 })
    })

    renderLayer()

    expect(useSceneStore.getState().actions).toHaveLength(1)

    await act(async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve())
      })
    })

    expect(useSceneStore.getState().actions).toHaveLength(0)
  })
})