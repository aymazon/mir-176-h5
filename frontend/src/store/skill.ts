import { create } from 'zustand'

/**
 * 技能面板 store（todo 32）：
 *  - learned：已学技能（等级 + 熟练经验 + 下一级阈值，GET /api/skill/list 投影）
 *  - available：可学清单（职业匹配且未学；requires_book=true 的 35 级技能仅展示）
 * 会话内事实源即 GET /list 最近一次响应；learn/learn_from_book 成功后整体重拉。
 */

export interface LearnedSkill {
  skill_id: string
  name: string
  level: number
  exp: number
  max_level: number
  /** 从当前级升下一级所需累计熟练；0 = 已满级（后端哨兵） */
  next_level_exp: number
}

export interface AvailableSkill {
  skill_id: string
  name: string
  learn_level: number
  mp_cost: number
  /** true = ≥35 级技能书技能，/learn 会拒绝，只能 learn_from_book */
  requires_book: boolean
}

interface SkillStore {
  learned: LearnedSkill[]
  available: AvailableSkill[]
  /** 整体替换（GET /list 响应驱动） */
  setList: (out: { learned: LearnedSkill[]; available: AvailableSkill[] }) => void
  reset: () => void
}

export const useSkillStore = create<SkillStore>((set) => ({
  learned: [],
  available: [],

  setList: (out) => set({ learned: out.learned, available: out.available }),

  reset: () => set({ learned: [], available: [] }),
}))
