/**
 * 技能视觉表（纯数据，无逻辑、无 React 依赖）：
 *  - 覆盖 docs/MVP/README.md 全部 32 个技能 id
 *  - emoji：技能图标占位；colorClass：Tailwind 文本色
 *  - 未知 id 走 skillVisualOf 兜底（✨），绝不抛错
 */

export interface SkillVisual {
  emoji: string
  colorClass: string
}

export const SKILL_VISUALS: Record<string, SkillVisual> = {
  basic_sword: { emoji: '🗡️', colorClass: 'text-slate-300' },
  tae_kwon_do: { emoji: '⚔️', colorClass: 'text-red-400' },
  thrust: { emoji: '🗡️', colorClass: 'text-cyan-300' },
  half_moon: { emoji: '🌙', colorClass: 'text-amber-300' },
  soul_shoulder: { emoji: '💥', colorClass: 'text-orange-400' },
  liehuo: { emoji: '🔥', colorClass: 'text-red-500' },
  fireball: { emoji: '🔥', colorClass: 'text-orange-400' },
  repulsion: { emoji: '⭕', colorClass: 'text-orange-300' },
  temptation: { emoji: '💜', colorClass: 'text-purple-300' },
  hell_fire: { emoji: '🔥', colorClass: 'text-purple-400' },
  lightning: { emoji: '⚡', colorClass: 'text-yellow-300' },
  thunder_bolt: { emoji: '⚡', colorClass: 'text-yellow-400' },
  great_fireball: { emoji: '🔥', colorClass: 'text-red-400' },
  teleport: { emoji: '✨', colorClass: 'text-cyan-300' },
  blast_fire: { emoji: '💥', colorClass: 'text-orange-500' },
  fire_wall: { emoji: '🌋', colorClass: 'text-orange-500' },
  lightning_exorcism: { emoji: '🌩️', colorClass: 'text-yellow-300' },
  magic_shield: { emoji: '🛡️', colorClass: 'text-cyan-300' },
  holy_word: { emoji: '✨', colorClass: 'text-white' },
  ice_thunder: { emoji: '❄️', colorClass: 'text-cyan-200' },
  healing: { emoji: '💚', colorClass: 'text-green-400' },
  spirit_sword: { emoji: '🎯', colorClass: 'text-slate-300' },
  poison_amulet: { emoji: '☠️', colorClass: 'text-green-500' },
  soul_fire: { emoji: '🔮', colorClass: 'text-purple-400' },
  summon_skeleton: { emoji: '💀', colorClass: 'text-white' },
  invisibility: { emoji: '👻', colorClass: 'text-slate-400' },
  mass_invisibility: { emoji: '👻', colorClass: 'text-slate-300' },
  ghost_shield: { emoji: '🛡️', colorClass: 'text-purple-300' },
  holy_armor: { emoji: '🛡️', colorClass: 'text-amber-300' },
  trap_curse: { emoji: '🕸️', colorClass: 'text-purple-300' },
  mass_heal: { emoji: '💚', colorClass: 'text-green-300' },
  summon_beast: { emoji: '🐕', colorClass: 'text-amber-300' },
}

const FALLBACK: SkillVisual = { emoji: '✨', colorClass: 'text-slate-200' }

/** 取技能视觉；未知 id 返回兜底（✨），绝不抛错 */
export function skillVisualOf(id: string): SkillVisual {
  return SKILL_VISUALS[id] ?? FALLBACK
}
