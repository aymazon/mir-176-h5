import { create } from 'zustand'
import { INVENTORY_CAPACITY } from '../config'

export interface BagItem {
  /** item_instance id（字符串化的 int） */
  id: string
  /** gamedata 物品 id */
  itemId: string
  name: string
  qty: number
  /** 物品种类（potion/weapon/scroll…，GET /api/inventory/bag kind 字段） */
  kind?: string
  /** 当前耐久（null/undefined = 无耐久概念或满耐久存量兼容） */
  durability?: number | null
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

/** 槽位值 = 物品定义 id（展示名/耐久上限经 data/gamedata 目录解析） */
export type EquipSlots = Record<EquipSlotKey, string | null>

function emptySlots(): EquipSlots {
  return Object.fromEntries(EQUIP_SLOT_KEYS.map((k) => [k, null])) as EquipSlots
}

interface InventoryStore {
  items: BagItem[]
  capacity: number
  gold: number
  equipSlots: EquipSlots
  /** 槽位 → 已穿戴实例 id（会话内跟踪；后端 bag 接口暂不回传穿戴状态） */
  equippedInstanceBySlot: EquipSlots
  /** 背包/装备快照整体替换（SSE stat/REST 刷新时调用） */
  setSnapshot: (patch: {
    items?: BagItem[]
    gold?: number
    capacity?: number
    equipSlots?: EquipSlots
  }) => void
  equip: (slot: EquipSlotKey, itemId: string | null) => void
  /** 穿戴成功（EquipOut）：槽位→item_id + 实例 id；替换下的旧实例自动出集合 */
  markEquipped: (
    slot: EquipSlotKey,
    itemId: string,
    instanceId: string,
  ) => void
  /** 卸下成功（UnequipOut）：清槽位 + 移除该槽位实例 id */
  markUnequipped: (slot: EquipSlotKey) => void
  reset: () => void
}

/**
 * 背包/装备快照 store：todo 31 起接真实 API——
 * equipSlots/equippedInstanceBySlot 为会话内跟踪（equip/unequip 响应驱动），
 * 刷新页面后归零是已知 MVP 债务（后端无 GET 穿戴清单端点，见 learnings）。
 */
export const useInventoryStore = create<InventoryStore>((set) => ({
  items: [],
  capacity: INVENTORY_CAPACITY,
  gold: 0,
  equipSlots: emptySlots(),
  equippedInstanceBySlot: emptySlots(),

  setSnapshot: (patch) =>
    set((state) => ({
      items: patch.items ?? state.items,
      gold: patch.gold ?? state.gold,
      capacity: patch.capacity ?? state.capacity,
      equipSlots: patch.equipSlots ?? state.equipSlots,
    })),

  equip: (slot, itemId) =>
    set((state) => ({
      equipSlots: { ...state.equipSlots, [slot]: itemId },
    })),

  markEquipped: (slot, itemId, instanceId) =>
    set((state) => ({
      equipSlots: { ...state.equipSlots, [slot]: itemId },
      equippedInstanceBySlot: {
        ...state.equippedInstanceBySlot,
        [slot]: instanceId,
      },
    })),

  markUnequipped: (slot) =>
    set((state) => ({
      equipSlots: { ...state.equipSlots, [slot]: null },
      equippedInstanceBySlot: {
        ...state.equippedInstanceBySlot,
        [slot]: null,
      },
    })),

  reset: () =>
    set({
      items: [],
      gold: 0,
      equipSlots: emptySlots(),
      equippedInstanceBySlot: emptySlots(),
    }),
}))

/** 已穿戴实例 id 集合（背包格「已穿戴」角标用派生值，不入 store） */
export function selectEquippedIds(s: InventoryStore): Set<string> {
  return new Set(
    Object.values(s.equippedInstanceBySlot).filter((v): v is string => v !== null),
  )
}

/** 物品种类 → 可穿槽位推断（左右镯/戒指先空位后右侧）；非装备种类返回 null */
export const EQUIPMENT_KINDS: ReadonlySet<string> = new Set([
  'weapon',
  'armor',
  'helmet',
  'necklace',
  'bracelet',
  'ring',
  'belt',
  'boots',
])

export function inferSlotForKind(
  kind: string | undefined,
  slots: EquipSlots,
): EquipSlotKey | null {
  switch (kind) {
    case 'weapon':
      return 'weapon'
    case 'armor':
      return 'armor'
    case 'helmet':
      return 'helmet'
    case 'necklace':
      return 'necklace'
    case 'bracelet':
      return slots.braceletL === null ? 'braceletL' : 'braceletR'
    case 'ring':
      return slots.ringL === null ? 'ringL' : 'ringR'
    case 'belt':
      return 'belt'
    case 'boots':
      return 'boots'
    default:
      return null
  }
}