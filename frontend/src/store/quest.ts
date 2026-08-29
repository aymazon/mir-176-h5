import { create } from 'zustand'

/**
 * 任务面板 store（todo 32）：GET /api/quest/list 的四状态分组投影。
 * accept/progress/complete 成功后组件层重拉 /list 对齐（后端状态机是唯一事实源，
 * 前端不做本地状态迁移推演）。
 */

export interface QuestEntry {
  quest_id: string
  name: string
  /** kill | collect | dialogue | profession（boss_instance 任务 kind 为 kill 且 arena_map_id 非空） */
  kind: string
  /** not_started | in_progress | completed | delivered */
  status: string
  level_req: number
  required_class: string | null
  target_id: string
  target_count: number
  count: number
  need: number
  reward_gold: number
  reward_exp: number
  reward_items: string[]
  dialogue: Record<string, string>
  /** boss_instance 任务接取即进的竞技场图 id */
  arena_map_id: string | null
}

export interface QuestListOut {
  not_started: QuestEntry[]
  in_progress: QuestEntry[]
  completed: QuestEntry[]
  delivered: QuestEntry[]
}

interface QuestStore {
  notStarted: QuestEntry[]
  inProgress: QuestEntry[]
  completed: QuestEntry[]
  delivered: QuestEntry[]
  setList: (out: QuestListOut) => void
  reset: () => void
}

export const useQuestStore = create<QuestStore>((set) => ({
  notStarted: [],
  inProgress: [],
  completed: [],
  delivered: [],

  setList: (out) =>
    set({
      notStarted: out.not_started,
      inProgress: out.in_progress,
      completed: out.completed,
      delivered: out.delivered,
    }),

  reset: () =>
    set({ notStarted: [], inProgress: [], completed: [], delivered: [] }),
}))

/** kind + arena_map_id → /progress 的 action 值（boss_instance 任务恒为 boss） */
export function progressActionFor(quest: QuestEntry): 'kill' | 'collect' | 'dialog' | 'boss' {
  if (quest.arena_map_id !== null) return 'boss'
  switch (quest.kind) {
    case 'kill':
      return 'kill'
    case 'collect':
      return 'collect'
    default:
      // dialogue / profession 均为对话推进（todo 29：profession 泛化为任意 NPC 同图对话）
      return 'dialog'
  }
}
