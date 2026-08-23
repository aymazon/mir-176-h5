import { create } from 'zustand'
import { INVENTORY_CAPACITY } from '../config'

export interface BagItem {
  /** item_instance id */
  id: string
  /** gamedata 物品 id */
  itemId: string
  name: string
  qty: number
}

/** 10 槽位（与后端 equipment_slot 表 todo 23 契约对齐） */
export const EQUIP_SLOT_KEYS = [
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
] as const

export type EquipSlotKey = (typeof EQUIP_SLOT_KEYS)[number]

export type EquipSlots = Record<EquipSlotKey, string | null>

function emptySlots(): EquipSlots {
  return Object.fromEntries(EQUIP_SLOT_KEYS.map((k) => [k, null])) as EquipSlots
}

interface InventoryStore {
  items: BagItem[]
  capacity: number
  gold: number
  equipSlots: EquipSlots
  /** 背包/装备快照整体替换（SSE stat/REST 刷新时调用） */
  setSnapshot: (patch: {
    items?: BagItem[]
    gold?: number
    equipSlots?: EquipSlots
  }) => void
  equip: (slot: EquipSlotKey, itemId: string | null) => void
  reset: () => void
}

/** 背包/装备快照占位 store：todo 23–25 接真实 API 后扩展动作 */
export const useInventoryStore = create<InventoryStore>((set) => ({
  items: [],
  capacity: INVENTORY_CAPACITY,
  gold: 0,
  equipSlots: emptySlots(),

  setSnapshot: (patch) =>
    set((state) => ({
      items: patch.items ?? state.items,
      gold: patch.gold ?? state.gold,
      equipSlots: patch.equipSlots ?? state.equipSlots,
    })),

  equip: (slot, itemId) =>
    set((state) => ({
      equipSlots: { ...state.equipSlots, [slot]: itemId },
    })),

  reset: () =>
    set({ items: [], gold: 0, equipSlots: emptySlots() }),
}))
