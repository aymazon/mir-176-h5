import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react'
import EquipTab from '../ui/EquipTab'
import { useInventoryStore } from '../store/inventory'
import { useGameStore } from '../store/game'

const bagResponse = {
  items: [
    {
      item_instance_id: 12,
      item_id: 'wooden_sword',
      name: '木剑',
      kind: 'weapon',
      quantity: 1,
      durability: 3800,
    },
    {
      item_instance_id: 13,
      item_id: 'cloth_robe',
      name: '布衣',
      kind: 'armor',
      quantity: 1,
      durability: null,
    },
  ],
  gold: 50,
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
  useGameStore.getState().setGame({
    player: { id: 'p1', username: 'tester' },
    character: {
      id: 'c1',
      name: '勇者',
      class: 'warrior',
      level: 1,
      hp: 20,
      mp: 8,
      gold: 50,
      bindCity: 'yin-xing-cun',
    },
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('EquipTab', () => {
  it('渲染 10 槽位（空槽占位）与面板数值区', async () => {
    mockFetch()
    render(<EquipTab />)
    for (const slot of [
      'weapon',
      'armor',
      'helmet',
      'necklace',
      'braceletL',
      'braceletR',
      'ringL',
      'ringR',
      'belt',
      'boots',
    ]) {
      expect(screen.getByTestId(`equip-slot-${slot}`)).toBeInTheDocument()
    }
    // 面板：stat 未到时展示 —
    expect(screen.getByTestId('panel-dc')).toHaveTextContent('—')
    expect(screen.getByTestId('panel-hp')).toHaveTextContent('20')
  })

  it('面板数值随 stat.panel 渲染（攻击区间/幸运标量）', async () => {
    mockFetch()
    render(<EquipTab />)
    act(() => {
      useGameStore.getState().applyStat({
        hp: 20,
      mp: 8,
      exp: 0,
      gold: 50,
      panel: {
        dc_min: 1,
        dc_max: 7,
        ac_min: 0,
        ac_max: 3,
        mac_min: 0,
        mac_max: 1,
        luck: 2,
        accuracy: 5,
        agility: 4,
        attack_speed: 1,
          strength: 9,
        },
      })
    })
    expect(screen.getByTestId('panel-dc')).toHaveTextContent('1-7')
    expect(screen.getByTestId('panel-ac')).toHaveTextContent('0-3')
    expect(screen.getByTestId('panel-luck')).toHaveTextContent('2')
  })

  it('背包可穿戴条点击 → POST /api/inventory/equip，槽位显示武器与耐久条', async () => {
    const fetchMock = mockFetch()
    render(<EquipTab />)
    const wearBtn = await screen.findByTestId('wear-12')
    expect(wearBtn).toHaveTextContent('穿戴 木剑')

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/inventory/equip')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          item_instance_id: 12,
          slot: 'weapon',
        })
        return new Response(
          JSON.stringify({
            slot: 'weapon',
            item_instance_id: 12,
            item_id: 'wooden_sword',
            replaced_item_instance_id: null,
            panel: { dc_min: 2, dc_max: 9 },
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(bagResponse), { status: 200 })
    })

    fireEvent.click(wearBtn)
    await waitFor(() => {
      expect(screen.getByTestId('equip-item-weapon')).toHaveTextContent('木剑')
    })
    // 耐久条：3800/4000
    expect(screen.getByTestId('durability-text-weapon')).toHaveTextContent(
      '耐久 3800/4000',
    )
    expect(useInventoryStore.getState().equipSlots.weapon).toBe('wooden_sword')
  })

  it('已穿槽位卸下 → POST /api/inventory/unequip，槽位清空', async () => {
    const fetchMock = mockFetch()
    useInventoryStore.getState().markEquipped('weapon', 'wooden_sword', '12')
    render(<EquipTab />)

    const unequipBtn = await screen.findByTestId('unequip-weapon')
    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/inventory/unequip')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          slot: 'weapon',
        })
        return new Response(
          JSON.stringify({
            slot: 'weapon',
            item_instance_id: 12,
            panel: { dc_min: 1, dc_max: 5 },
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(bagResponse), { status: 200 })
    })

    fireEvent.click(unequipBtn)
    await waitFor(() => {
      expect(useInventoryStore.getState().equipSlots.weapon).toBeNull()
    })
    expect(screen.queryByTestId('equip-item-weapon')).not.toBeInTheDocument()
  })
})
