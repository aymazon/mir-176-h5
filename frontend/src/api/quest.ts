import { requestJson } from './game'
import { useQuestStore } from '../store/quest'
import type { QuestListOut } from '../store/quest'

/**
 * 任务 REST 薄封装（todo 29 后端契约 / todo 32 前端接入）：
 *  - GET  /api/quest/list      全部任务按状态分组（not_started/in_progress/completed/delivered）
 *  - POST /api/quest/accept    未接→进行（boss_instance 任务接取即传送进竞技场）
 *  - POST /api/quest/progress  推进进度（kill/collect/dialog/boss）
 *  - POST /api/quest/complete  达标待交付→已交付（发奖励）
 * 非 2xx 抛 ApiError(detail)，组件层 catch 后 toast。
 */

export interface AcceptOut {
  quest: import('../store/quest').QuestEntry
  message: string
  /** boss_instance 任务接取即传送：新所在图 id */
  map_id: string | null
}

export interface ProgressOut {
  quest_id: string
  count: number
  need: number
  status: string
  message: string
}

export interface CompleteOut {
  quest_id: string
  name: string
  status: string
  gold: number
  exp: number
  rewards: {
    gold: number
    exp: number
    items: Array<{ item_id: string; name: string; quantity: number }>
  }
}

/** 拉任务清单并同步 quest store；失败保留既有快照返回 null */
export async function refreshQuests(): Promise<QuestListOut | null> {
  try {
    const out = await requestJson<QuestListOut>('/api/quest/list')
    useQuestStore.getState().setList(out)
    return out
  } catch {
    return null
  }
}

export function apiQuestAccept(questId: string): Promise<AcceptOut> {
  return requestJson<AcceptOut>('/api/quest/accept', {
    json: { quest_id: questId },
  })
}

export function apiQuestProgress(
  questId: string,
  action: 'kill' | 'collect' | 'dialog' | 'boss',
): Promise<ProgressOut> {
  return requestJson<ProgressOut>('/api/quest/progress', {
    json: { quest_id: questId, action },
  })
}

export function apiQuestComplete(questId: string): Promise<CompleteOut> {
  return requestJson<CompleteOut>('/api/quest/complete', {
    json: { quest_id: questId },
  })
}
