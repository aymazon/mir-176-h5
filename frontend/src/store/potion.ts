import { create } from 'zustand'

/**
 * 吃药策略 store（todo 32）：
 *  - hpPct：HP < X% 喝回血药（金创药族，常规线；后端 POTION_HP_PCT 默认 30）
 *  - urgentHpPct：HP < Y% 危险线优先高级药（太阳水；POTION_URGENT_HP_PCT 默认 15）
 *  - mpPct：MP < Z% 喝回蓝药（魔法药；POTION_MP_PCT 默认 20）
 *  - recallFreeSlots：背包空格 < N 自动回城（BAG_FREE_SLOTS_RECALL 默认 5；0 = 关闭）
 * 本地 localStorage 持久化（键 mir.potionPolicy）；REST 落会话见 api/potion.ts。
 */

export interface PotionPolicy {
  hpPct: number
  urgentHpPct: number
  mpPct: number
  recallFreeSlots: number
}

export const DEFAULT_POTION_POLICY: PotionPolicy = {
  hpPct: 30,
  urgentHpPct: 15,
  mpPct: 20,
  recallFreeSlots: 5,
}

const STORAGE_KEY = 'mir.potionPolicy'

/** 策略字段校验：百分比 1–99 整数、危险线低于常规线、回城空格 0–40（背包容量上限） */
export function validatePotionPolicy(p: PotionPolicy): string | null {
  const pcts: Array<[string, number]> = [
    ['HP 回血线', p.hpPct],
    ['HP 危险线', p.urgentHpPct],
    ['MP 回蓝线', p.mpPct],
  ]
  for (const [label, v] of pcts) {
    if (!Number.isInteger(v) || v < 1 || v > 99) {
      return `${label}须为 1–99 的整数百分比（当前 ${v}）`
    }
  }
  if (p.urgentHpPct >= p.hpPct) {
    return 'HP 危险线必须低于回血线（危险线优先喝太阳水保命）'
  }
  if (
    !Number.isInteger(p.recallFreeSlots) ||
    p.recallFreeSlots < 0 ||
    p.recallFreeSlots > 40
  ) {
    return `回城空格阈值须为 0–40 的整数（0 = 关闭自动回城）`
  }
  return null
}

function loadPersisted(): PotionPolicy {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === null) return DEFAULT_POTION_POLICY
    const parsed = JSON.parse(raw) as Partial<PotionPolicy>
    const merged: PotionPolicy = {
      hpPct: Number(parsed.hpPct ?? DEFAULT_POTION_POLICY.hpPct),
      urgentHpPct: Number(parsed.urgentHpPct ?? DEFAULT_POTION_POLICY.urgentHpPct),
      mpPct: Number(parsed.mpPct ?? DEFAULT_POTION_POLICY.mpPct),
      recallFreeSlots: Number(
        parsed.recallFreeSlots ?? DEFAULT_POTION_POLICY.recallFreeSlots,
      ),
    }
    // 持久化数据被手工改坏时回退默认，不阻塞页面
    return validatePotionPolicy(merged) === null ? merged : DEFAULT_POTION_POLICY
  } catch {
    return DEFAULT_POTION_POLICY
  }
}

interface PotionStore {
  policy: PotionPolicy
  setPolicy: (policy: PotionPolicy) => void
  reset: () => void
}

export const usePotionStore = create<PotionStore>((set) => ({
  policy: loadPersisted(),

  setPolicy: (policy) => {
    set({ policy })
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(policy))
    } catch {
      // 隐私模式等写失败：内存态已生效，忽略
    }
  },

  reset: () => {
    set({ policy: DEFAULT_POTION_POLICY })
    try {
      window.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 同上
    }
  },
}))
