/**
 * 怪物视觉表（纯数据，无逻辑、无 React 依赖）：
 *  - 覆盖 backend/gamedata/monsters/*.json 全部 60 只怪物
 *  - emoji：战斗场景/图标的占位图形；tint：Tailwind 文本色；tier：normal/elite/boss
 *  - 未知 id 走 monsterVisualOf 兜底（👹），绝不抛错
 */

export interface MonsterVisual {
  emoji: string
  tint: string
  tier: 'normal' | 'elite' | 'boss'
}

export const MONSTER_VISUALS: Record<string, MonsterVisual> = {
  // 新手/野兽系
  chicken: { emoji: '🐔', tint: 'text-amber-200', tier: 'normal' },
  deer: { emoji: '🦌', tint: 'text-amber-300', tier: 'normal' },
  scarecrow: { emoji: '🎍', tint: 'text-yellow-600', tier: 'normal' },
  rake_cat: { emoji: '🐈', tint: 'text-orange-300', tier: 'normal' },
  hook_cat: { emoji: '🐱', tint: 'text-orange-300', tier: 'normal' },
  man_eating_flower: { emoji: '🌺', tint: 'text-pink-400', tier: 'normal' },
  bat: { emoji: '🦇', tint: 'text-violet-300', tier: 'normal' },
  cave_bat: { emoji: '🦇', tint: 'text-violet-400', tier: 'normal' },
  // 蛮族系
  orc: { emoji: '👺', tint: 'text-green-400', tier: 'normal' },
  orc_warrior: { emoji: '👺', tint: 'text-green-500', tier: 'normal' },
  orc_champion: { emoji: '👺', tint: 'text-green-300', tier: 'elite' },
  // 白骨系
  skeleton: { emoji: '💀', tint: 'text-slate-300', tier: 'normal' },
  skeleton_warrior: { emoji: '💀', tint: 'text-slate-400', tier: 'normal' },
  axe_skeleton: { emoji: '💀', tint: 'text-slate-300', tier: 'normal' },
  skeleton_elf: { emoji: '💀', tint: 'text-cyan-300', tier: 'elite' },
  // 行尸系
  zombie: { emoji: '🧟', tint: 'text-lime-400', tier: 'normal' },
  zombie_fighter: { emoji: '🧟', tint: 'text-lime-500', tier: 'normal' },
  lightning_zombie: { emoji: '🧟', tint: 'text-yellow-300', tier: 'normal' },
  corpse_king: { emoji: '🧟', tint: 'text-amber-400', tier: 'boss' },
  corpse_overlord: { emoji: '🧟', tint: 'text-lime-300', tier: 'elite' },
  evil_corpse_king: { emoji: '🧟', tint: 'text-purple-400', tier: 'boss' },
  // 虫系
  cave_maggot: { emoji: '🪱', tint: 'text-lime-300', tier: 'normal' },
  centipede: { emoji: '🐛', tint: 'text-amber-500', tier: 'normal' },
  black_centipede: { emoji: '🐛', tint: 'text-zinc-400', tier: 'normal' },
  giant_centipede: { emoji: '🐛', tint: 'text-amber-300', tier: 'elite' },
  evil_clamp_worm: { emoji: '🦂', tint: 'text-red-400', tier: 'elite' },
  desert_giant_worm: { emoji: '🪱', tint: 'text-yellow-500', tier: 'elite' },
  jumping_bee: { emoji: '🐝', tint: 'text-yellow-300', tier: 'normal' },
  horn_fly: { emoji: '🪰', tint: 'text-slate-400', tier: 'normal' },
  wedge_moth: { emoji: '🦋', tint: 'text-indigo-300', tier: 'normal' },
  // 蛇系
  red_snake: { emoji: '🐍', tint: 'text-red-400', tier: 'normal' },
  tiger_snake: { emoji: '🐍', tint: 'text-orange-400', tier: 'normal' },
  venom_snake: { emoji: '🐍', tint: 'text-green-400', tier: 'normal' },
  venom_snake_fighter: { emoji: '🐍', tint: 'text-green-500', tier: 'normal' },
  scorpion_snake: { emoji: '🐍', tint: 'text-teal-400', tier: 'normal' },
  giant_snake: { emoji: '🐍', tint: 'text-emerald-300', tier: 'elite' },
  two_head_snake: { emoji: '🐍', tint: 'text-cyan-300', tier: 'elite' },
  two_head_snake_demon: { emoji: '🐍', tint: 'text-purple-300', tier: 'boss' },
  // 蝎/蛛
  scorpion: { emoji: '🦂', tint: 'text-amber-400', tier: 'normal' },
  giant_scorpion: { emoji: '🦂', tint: 'text-orange-300', tier: 'elite' },
  poison_spider: { emoji: '🕷️', tint: 'text-purple-400', tier: 'normal' },
  // 野猪系
  wild_boar_red: { emoji: '🐗', tint: 'text-red-400', tier: 'normal' },
  wild_boar_black: { emoji: '🐗', tint: 'text-zinc-300', tier: 'normal' },
  wild_boar_white: { emoji: '🐗', tint: 'text-amber-300', tier: 'boss' },
  // 雪魈
  forest_yeti: { emoji: '🦍', tint: 'text-cyan-200', tier: 'normal' },
  // 蛙系
  toad: { emoji: '🐸', tint: 'text-green-300', tier: 'normal' },
  toad_fighter: { emoji: '🐸', tint: 'text-green-400', tier: 'normal' },
  // 雾喉系
  wooma_warrior: { emoji: '🐐', tint: 'text-orange-300', tier: 'normal' },
  wooma_soldier: { emoji: '🐐', tint: 'text-orange-400', tier: 'normal' },
  wooma_guardian: { emoji: '🐐', tint: 'text-amber-400', tier: 'normal' },
  ancient_wooma_warrior: { emoji: '🐐', tint: 'text-yellow-500', tier: 'elite' },
  flaming_wooma: { emoji: '🐐', tint: 'text-red-500', tier: 'elite' },
  wooma_tactician: { emoji: '🐐', tint: 'text-rose-400', tier: 'elite' },
  wooma_taurus: { emoji: '🐂', tint: 'text-amber-400', tier: 'boss' },
  // 磐石系
  zuma_statue: { emoji: '🗿', tint: 'text-slate-400', tier: 'normal' },
  zuma_guardian: { emoji: '🗿', tint: 'text-slate-300', tier: 'normal' },
  zuma_archer: { emoji: '🗿', tint: 'text-emerald-300', tier: 'normal' },
  zuma_guardian_elite: { emoji: '🗿', tint: 'text-amber-300', tier: 'elite' },
  zuma_taurus: { emoji: '🗿', tint: 'text-amber-400', tier: 'boss' },
  // 龙
  touch_dragon_god: { emoji: '🐉', tint: 'text-red-500', tier: 'boss' },
}

const FALLBACK: MonsterVisual = { emoji: '👹', tint: 'text-slate-300', tier: 'normal' }

/** 取怪物视觉；未知 id 返回兜底（👹 normal），绝不抛错 */
export function monsterVisualOf(id: string): MonsterVisual {
  return MONSTER_VISUALS[id] ?? FALLBACK
}
