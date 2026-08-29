import { requestJson } from './game'
import { useSkillStore } from '../store/skill'
import type { AvailableSkill, LearnedSkill } from '../store/skill'

/**
 * 技能 REST 薄封装（todo 28 后端契约 / todo 32 前端接入）：
 *  - GET  /api/skill/list            已学（等级/熟练经验/下一级阈值）+ 可学清单
 *  - POST /api/skill/learn           显式按级习得（<35 级技能；≥35 必须技能书）
 *  - POST /api/skill/learn_from_book 技能书习得（35 级技能唯一入口，扣一本书）
 * 全部走 requestJson（restFetch：401 统一跳登录；非 2xx 抛 ApiError(detail)）。
 */

export interface SkillListOut {
  learned: LearnedSkill[]
  available: AvailableSkill[]
}

export interface LearnOut {
  skill_id: string
  name: string
  level: number
  exp: number
  /** learn=显式按级习得 | book=技能书习得 */
  source: string
}

/** 拉技能清单并同步 skill store；失败保留既有快照返回 null（不阻塞页面） */
export async function refreshSkills(): Promise<SkillListOut | null> {
  try {
    const out = await requestJson<SkillListOut>('/api/skill/list')
    useSkillStore.getState().setList(out)
    return out
  } catch {
    return null
  }
}

export function apiLearnSkill(skillId: string): Promise<LearnOut> {
  return requestJson<LearnOut>('/api/skill/learn', {
    json: { skill_id: skillId },
  })
}

export function apiLearnFromBook(itemInstanceId: string): Promise<LearnOut> {
  return requestJson<LearnOut>('/api/skill/learn_from_book', {
    json: { item_instance_id: Number(itemInstanceId) },
  })
}
