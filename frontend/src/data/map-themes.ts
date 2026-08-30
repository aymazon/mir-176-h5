/**
 * 地图区域视觉主题表（纯数据，无逻辑、无 React 依赖）：
 *  - key 为 MapDef.region（frontend/src/data/gamedata.ts，唯一事实源）
 *  - bgClass：Tailwind 渐变起止类（from-X / to-X）；decor：场景装饰 emoji；accentClass：强调文本色
 *  - 未知 region 走 mapThemeOf 兜底（default），绝不抛错
 */

export interface MapTheme {
  bgClass: string
  decor: string[]
  accentClass: string
}

export const MAP_THEMES: Record<string, MapTheme> = {
  newbie: {
    bgClass: 'from-emerald-900 to-slate-900',
    decor: ['🌾', '🌳', '🏡'],
    accentClass: 'text-emerald-300',
  },
  bichi: {
    bgClass: 'from-green-900 to-slate-950',
    decor: ['🌿', '🌾'],
    accentClass: 'text-green-300',
  },
  orc_tomb: {
    bgClass: 'from-stone-800 to-slate-950',
    decor: ['🦴', '🪨'],
    accentClass: 'text-stone-300',
  },
  biqi_mine: {
    bgClass: 'from-zinc-800 to-black',
    decor: ['⛏️', '🪨'],
    accentClass: 'text-zinc-300',
  },
  snake_valley: {
    bgClass: 'from-red-950 to-slate-950',
    decor: ['🌵', '🪨'],
    accentClass: 'text-red-300',
  },
  mengzhong: {
    bgClass: 'from-amber-950 to-slate-950',
    decor: ['🏜️', '🌵'],
    accentClass: 'text-amber-300',
  },
  death_valley: {
    bgClass: 'from-purple-950 to-black',
    decor: ['🕸️', '🪦'],
    accentClass: 'text-purple-300',
  },
  shimen: {
    bgClass: 'from-rose-950 to-stone-950',
    decor: ['🐗', '🪦'],
    accentClass: 'text-rose-300',
  },
  wooma_forest: {
    bgClass: 'from-teal-950 to-slate-950',
    decor: ['🌲', '🌫️'],
    accentClass: 'text-teal-300',
  },
  wooma_temple: {
    bgClass: 'from-orange-950 to-black',
    decor: ['🛕', '🔥'],
    accentClass: 'text-orange-300',
  },
  zuma_temple: {
    bgClass: 'from-slate-800 to-black',
    decor: ['🗿', '⚡'],
    accentClass: 'text-slate-200',
  },
  default: {
    bgClass: 'from-slate-900 to-slate-950',
    decor: ['🌑'],
    accentClass: 'text-slate-300',
  },
}

/** 取区域主题；未知/缺省 region 返回 default 主题，绝不抛错 */
export function mapThemeOf(region: string | undefined): MapTheme {
  if (region === undefined) return MAP_THEMES.default
  return MAP_THEMES[region] ?? MAP_THEMES.default
}
