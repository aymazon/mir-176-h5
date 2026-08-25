import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import InventoryTab from '../ui/InventoryTab'
import { useInventoryStore } from '../store/inventory'
import { useGameStore } from '../store/game'

const bagResponse = {
  items: [
    {
      item_instance_id: 11,
      item_id: 'potion_hp_small',
      name: '金创药（小）',
      kind: 'potion',
      quantity: 3,
      durability: null,
    },
    {
      item_instance_id: 12,
      item_id: 'wooden_sword',
      name: '木剑',
      kind: 'weapon',
      quantity: 1,
      durability: 3800,
    },
  ],
  gold: 88,
  capacity: 40,
  used: 2,
}

function mockFetch(): ReturnType<typeof vi.fn> {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    if (url.includes('/api/inventory/bag')) {
      return new Response(JSON.stringify(bagResponse), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'unexpected' }), { status: 404 })
  })
}

beforeEach(() => {
  useInventoryStore.getState().reset()
  useGameStore.getState().clearAuth()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('InventoryTab', () => {
  it('挂载拉取背包：网格渲染物品、堆叠数量角标与空格计数', async () => {
    mockFetch()
    render(<InventoryTab />)

    await waitFor(() => {
      expect(screen.getByTestId('bag-cell-0')).toHaveTextContent('金创药（小）')
    })
    // 堆叠数量
    expect(screen.getByTestId('bag-qty-0')).toHaveTextContent('×3')
    expect(screen.getByTestId('bag-cell-1')).toHaveTextContent('木剑')
    // 空格计数 = capacity - used = 38；空格子从 index 2 起为虚线格
    expect(screen.getByTestId('bag-empty-count')).toHaveTextContent('38')
    expect(screen.getByTestId('bag-gold')).toHaveTextContent('88')
  })

  it('点击物品开动作单，卖出调 POST /api/shop/sell 并刷新背包', async () => {
    const fetchMock = mockFetch()
    render(<InventoryTab />)
    await waitFor(() => {
      expect(screen.getByTestId('bag-cell-0')).toHaveTextContent('金创药（小）')
    })

    fireEvent.click(screen.getByTestId('bag-cell-0'))
    expect(screen.getByTestId('action-sheet')).toBeInTheDocument()

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/shop/sell')) {
        expect(JSON.parse(String(init.body))).toEqual({
          item_instance_id: 11,
          quantity: 1,
        })
        return new Response(
          JSON.stringify({
            item_instance_id: 11,
            item_id: 'potion_hp_small',
            name: '金创药（小）',
            quantity_sold: 1,
            unit_price: 16,
            total_earned: 16,
            gold_after: 104,
            quantity_left: 2,
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(bagResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('act-sell'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('卖出 金创药（小） ×1')
    })
  })

  it('后端 422 → toast 展示 detail 文案且不崩溃', async () => {
    const fetchMock = mockFetch()
    render(<InventoryTab />)
    await waitFor(() => {
      expect(screen.getByTestId('bag-cell-0')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('bag-cell-0'))

    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/inventory/use')) {
        return new Response(
          JSON.stringify({ detail: '药品冷却中' }),
          { status: 422 },
        )
      }
      return new Response(JSON.stringify(bagResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('act-use'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('药品冷却中')
    })
    // 页面仍然可用：动作单仍在（未关闭），网格未卸载
    expect(screen.getByTestId('action-sheet')).toBeInTheDocument()
    expect(screen.getByTestId('tab-panel-inventory')).toBeInTheDocument()
  })

  it('地面掉落条渲染拾取按钮并调 POST /api/inventory/pick', async () => {
    useGameStore.getState().setGame({
      player: { id: 'p1', username: 'tester' },
      character: {
        id: 'c1',
        name: '勇者',
        class: 'warrior',
        level: 1,
        hp: 10,
        mp: 5,
        gold: 0,
        bindCity: 'yin-xing-cun',
      },
    })
    useGameStore.getState().applyDropLoot([
      { item_id: 'wooden_sword', qty: 1, picked: false, drop_id: 'd-42' },
    ])
    const fetchMock = mockFetch()
    render(<InventoryTab />)

    const pickBtn = await screen.findByTestId('pick-d-42')
    expect(pickBtn).toHaveTextContent('拾取 木剑')

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/inventory/pick')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          item_drop_id: 'd-42',
        })
        return new Response(
          JSON.stringify({
            item_drop_id: 'd-42',
            item_instance_id: 12,
            item_id: 'wooden_sword',
            name: '木剑',
            quantity: 1,
            gold: 0,
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(bagResponse), { status: 200 })
    })
    fireEvent.click(pickBtn)
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('拾取 木剑 ×1')
    })
    // 地面条目已移除
    expect(useGameStore.getState().groundDrops).toHaveLength(0)
  })
})
