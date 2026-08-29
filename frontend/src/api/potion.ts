import { ApiError, requestJson } from './game'
import { usePotionStore } from '../store/potion'
import type { PotionPolicy } from '../store/potion'

/**
 * 吃药策略 REST 薄封装（todo 32）：
 *  - POST /api/character/policy 提交策略存会话
 *    body: { hp_pct, urgent_hp_pct, mp_pct, recall_free_slots }
 *    （与 combat/potion.py policy 覆盖键 hp_pct/mp_pct/urgent_hp_pct 同源命名）
 *
 * 已知债务：todo 26 只落了 sim 层 potion_policy 字段，未提供 REST 端点——
 * 后端未就位时（404/405）策略降级为纯本地持久化（localStorage + potion store），
 * 返回值 persisted 区分 'server' | 'local'，组件层 toast 如实展示。
 */

export interface PolicySaveResult {
  ok: boolean
  /** server=已存后端会话 | local=后端无端点，仅本地持久化 */
  persisted: 'server' | 'local'
}

interface PolicyPayload {
  hp_pct: number
  urgent_hp_pct: number
  mp_pct: number
  recall_free_slots: number
}

function toPayload(p: PotionPolicy): PolicyPayload {
  return {
    hp_pct: p.hpPct / 100,
    urgent_hp_pct: p.urgentHpPct / 100,
    mp_pct: p.mpPct / 100,
    recall_free_slots: p.recallFreeSlots,
  }
}

/** 提交策略：先写本地 store/localStorage（立即可用），再尝试 REST 落会话 */
export async function apiSavePotionPolicy(
  policy: PotionPolicy,
): Promise<PolicySaveResult> {
  usePotionStore.getState().setPolicy(policy)
  try {
    await requestJson('/api/character/policy', { json: toPayload(policy) })
    return { ok: true, persisted: 'server' }
  } catch (err) {
    // 端点未实现（todo 26 未含 REST）：本地已存，静默降级
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
      return { ok: true, persisted: 'local' }
    }
    throw err
  }
}
