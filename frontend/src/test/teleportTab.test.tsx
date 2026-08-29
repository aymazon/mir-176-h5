import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react'
import TeleportTab from '../ui/TeleportTab'
import { useTeleportStore } from '../store/teleport'
import { useGameStore } from '../store/game'
import { useInventoryStore } from '../store/inventory'

const panelResponse = {
  current_map_id: 'yin-xing-cun',
  current_map_name: '银杏村',
  bind_city: 'yin-xing-cun',
  traveling: false,
  travel_remaining_seconds: null,
  travel_target_map_id: null,
  destinations: [
    { map_id: 'biqi-wild', name: '比奇野外', fee: 100, kind: 'route', depth: null },
    { map_id: 'shimen', name: '石墓（猪洞） 5 层', fee: 500, kind: 'cave', depth: 5 },
  ],
}

function mockFetch(): ReturnType<typeof vi.fn> {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    if (url.includes('/api/teleport/panel')) {
      return new Response(JSON.stringify(panelResponse), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'unexpected' }), { status: 404 })
  })
}

beforeEach(() => {
  useTeleportStore.getState().reset()
  useGameStore.getState().clearAuth()
  useInventoryStore.getState().reset()
  // 登录态 + 金币（applyStat 依赖 character 存在）
  useGameStore.getState().setGame({
    player: { id: 'p1', username: 'tester' },
    character: {
      id: 'c1',
      name: '勇者',
      class: 'warrior',
      level: 10,
      hp: 50,
      mp: 20,
      gold: 1000,
      bindCity: 'yin-xing-cun',
      mapId: 'yin-xing-cun',
    },
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('TeleportTab', () => {
  it('挂载拉取面板：当前图/绑定城/目的地费用清单', async () => {
    mockFetch()
    render(<TeleportTab />)

    await waitFor(() => {
      expect(screen.getByTestId('teleport-current')).toHaveTextContent('银杏村')
    })
    expect(screen.getByTestId('teleport-bind')).toHaveTextContent('yin-xing-cun')
    expect(screen.getByTestId('teleport-fee-biqi-wild:0')).toHaveTextContent('100 金')
    expect(screen.getByTestId('teleport-fee-shimen:5')).toHaveTextContent('500 金')
    expect(screen.getByTestId('teleport-gold')).toHaveTextContent('1000 金')
  })

  it('两段式确认：首点变确认，再点 POST /api/teleport/warp 并扣金币 + 出行进横幅', async () => {
    const fetchMock = mockFetch()
    render(<TeleportTab />)
    await waitFor(() => {
      expect(screen.getByTestId('teleport-warp-shimen:5')).toBeInTheDocument()
    })

    // 第一次点击：仅进入确认态，不发请求
    fireEvent.click(screen.getByTestId('teleport-warp-shimen:5'))
    expect(screen.getByTestId('teleport-warp-shimen:5')).toHaveTextContent('确认 500 金')

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/teleport/warp')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          target_map_id: 'shimen',
          layer: 5,
        })
        return new Response(
          JSON.stringify({
            target_map_id: 'shimen',
            layer: 5,
            fee: 500,
            gold_after: 500,
            travel_seconds: 12,
            traveling_until: 1999999999,
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(panelResponse), { status: 200 })
    })

    // 第二次点击：执行传送
    fireEvent.click(screen.getByTestId('teleport-warp-shimen:5'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('-500 金')
    })
    // 金币扣减同步 game store（1000 → 500）
    expect(useGameStore.getState().character?.gold).toBe(500)
    expect(useInventoryStore.getState().gold).toBe(500)
    // 行进中横幅 + 倒计时
    expect(screen.getByTestId('traveling-banner')).toBeInTheDocument()
    expect(screen.getByTestId('travel-countdown')).toHaveTextContent('约 12s 到达')
    // 倒计时递减（真实 1s 计时器）
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100))
    })
    expect(screen.getByTestId('travel-countdown')).toHaveTextContent('约 11s 到达')
  })

  it('金币不足：按钮禁用且费用标红（不发请求）', async () => {
    mockFetch()
    useGameStore.getState().applyStat({ gold: 50 })
    render(<TeleportTab />)
    await waitFor(() => {
      expect(screen.getByTestId('teleport-fee-biqi-wild:0')).toBeInTheDocument()
    })
    expect(screen.getByTestId('teleport-warp-biqi-wild:0')).toBeDisabled()
    expect(screen.getByTestId('teleport-fee-biqi-wild:0')).toHaveTextContent('不足')
  })

  it('后端 422（行进中）→ toast 透传 detail 且页面不崩溃', async () => {
    const fetchMock = mockFetch()
    render(<TeleportTab />)
    await waitFor(() => {
      expect(screen.getByTestId('teleport-warp-biqi-wild:0')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('teleport-warp-biqi-wild:0')) // 进确认态

    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/teleport/warp')) {
        return new Response(
          JSON.stringify({ detail: '行进中无法再次传送（跑图倒计时进行中）' }),
          { status: 409 },
        )
      }
      return new Response(JSON.stringify(panelResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('teleport-warp-biqi-wild:0'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('行进中无法再次传送')
    })
    expect(screen.getByTestId('tab-panel-teleport')).toBeInTheDocument()
  })
})
