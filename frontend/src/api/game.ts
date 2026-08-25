import { restFetch, restJson } from './rest'
import { useInventoryStore } from '../store/inventory'
import type { BagItem, EquipSlotKey } from '../store/inventory'

/**
 * 战斗/背包/装备 REST 薄封装（todo 23–27 契约）：
 *  - 全部走 restFetch（credentials: 'include'，401 统一跳登录）
 *  - 非 2xx 抛 Error(detail)——组件层 catch 后 toast，不崩溃
 *  - refreshBag 拉全量背包并同步 inventory store（买卖/使用/拾取后复用）
 */

/** 前端槽位键 → 后端 EquipmentSlotType 值（bracelet/ring 左右分槽） */
export const SLOT_API_KEYS: Record<EquipSlotKey, string> = {
  weapon: 'weapon',
  armor: 'armor',
  helmet: 'helmet',
  necklace: 'necklace',
  braceletL: 'bracelet_l',
  braceletR: 'bracelet_r',
  ringL: 'ring_l',
  ringR: 'ring_r',
  belt: 'belt',
  boots: 'boots',
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** JSON POST/GET：非 2xx 抛 ApiError(detail) */
async function requestJson<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const res = await restFetch(path, {
    ...init,
    method: init?.method ?? (init?.json !== undefined ? 'POST' : 'GET'),
    headers: {
      ...(init?.json !== undefined
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...(init?.headers ?? {}),
    },
    body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
  })
  const data = (await res.json().catch(() => null)) as
    | (T & { detail?: unknown })
    | null
  if (!res.ok) {
    const detail =
      typeof data?.detail === 'string' ? data.detail : `请求失败（${res.status}）`
    throw new ApiError(res.status, detail)
  }
  return data as T
}

// --- 背包（todo 25）--------------------------------------------------------------

export interface BagItemOut {
  item_instance_id: number
  item_id: string
  name: string
  kind: string
  quantity: number
  durability: number | null
}

export interface BagOut {
  items: BagItemOut[]
  gold: number
  capacity: number
  used: number
}

function toBagItem(row: BagItemOut): BagItem {
  return {
    id: String(row.item_instance_id),
    itemId: row.item_id,
    name: row.name,
    qty: row.quantity,
    kind: row.kind,
    durability: row.durability,
  }
}

/** 拉全量背包并同步 inventory store；返回 used/capacity 供空格计数展示 */
export async function refreshBag(): Promise<BagOut | null> {
  try {
    const bag = await restJson<BagOut>('/api/inventory/bag')
    useInventoryStore.getState().setSnapshot({
      items: bag.items.map(toBagItem),
      gold: bag.gold,
      capacity: bag.capacity,
    })
    return bag
  } catch {
    // 网络失败/未建号：保留既有快照，页面展示空态而非崩溃
    return null
  }
}

export interface PickOut {
  item_drop_id: string
  item_instance_id: number
  item_id: string
  name: string
  quantity: number
  gold: number
}

export function apiPick(itemDropId: string): Promise<PickOut> {
  return requestJson<PickOut>('/api/inventory/pick', {
    json: { item_drop_id: itemDropId },
  })
}

export interface UseOut {
  item_instance_id: number
  item_id: string
  name: string
  result: string
  message: string
  quantity_left: number
}

export function apiUse(itemInstanceId: string): Promise<UseOut> {
  return requestJson<UseOut>('/api/inventory/use', {
    json: { item_instance_id: Number(itemInstanceId) },
  })
}

// --- 穿戴（todo 23）--------------------------------------------------------------

export interface EquipOut {
  slot: string
  item_instance_id: number
  item_id: string
  replaced_item_instance_id: number | null
  panel: Record<string, number>
}

export function apiEquip(
  itemInstanceId: string,
  slot: EquipSlotKey,
): Promise<EquipOut> {
  return requestJson<EquipOut>('/api/inventory/equip', {
    json: {
      item_instance_id: Number(itemInstanceId),
      slot: SLOT_API_KEYS[slot],
    },
  })
}

export interface UnequipOut {
  slot: string
  item_instance_id: number
  panel: Record<string, number>
}

export function apiUnequip(slot: EquipSlotKey): Promise<UnequipOut> {
  return requestJson<UnequipOut>('/api/inventory/unequip', {
    json: { slot: SLOT_API_KEYS[slot] },
  })
}

// --- 商店（todo 27）--------------------------------------------------------------

export interface SellOut {
  item_instance_id: number
  item_id: string
  name: string
  quantity_sold: number
  unit_price: number
  total_earned: number
  gold_after: number
  quantity_left: number
}

export function apiSell(itemInstanceId: string, quantity: number): Promise<SellOut> {
  return requestJson<SellOut>('/api/shop/sell', {
    json: { item_instance_id: Number(itemInstanceId), quantity },
  })
}

export interface BuyOut {
  npc_id: string
  item_id: string
  name: string
  quantity_bought: number
  unit_price: number
  total_cost: number
  gold_after: number
  item_instance_id: number
  quantity_in_bag: number
}

export function apiBuy(
  npcId: string,
  itemId: string,
  quantity: number,
): Promise<BuyOut> {
  return requestJson<BuyOut>('/api/shop/buy', {
    json: { npc_id: npcId, item_id: itemId, quantity },
  })
}
