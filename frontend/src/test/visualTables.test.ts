import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { MONSTER_VISUALS, monsterVisualOf } from '../data/monster-visuals'
import { MAP_THEMES, mapThemeOf } from '../data/map-themes'
import { SKILL_VISUALS, skillVisualOf } from '../data/skill-visuals'
import { MAPS } from '../data/gamedata'

/** 后端怪物 JSON 目录（vitest 以 frontend 为 cwd → ../backend/gamedata/monsters） */
const MONSTERS_DIR = resolve(process.cwd(), '../backend/gamedata/monsters')

function backendMonsterIds(): string[] {
  return readdirSync(MONSTERS_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => (JSON.parse(readFileSync(`${MONSTERS_DIR}/${f}`, 'utf8')) as { id: string }).id)
}

const EXPECTED_SKILL_IDS = [
  'basic_sword',
  'tae_kwon_do',
  'thrust',
  'half_moon',
  'soul_shoulder',
  'liehuo',
  'fireball',
  'repulsion',
  'temptation',
  'hell_fire',
  'lightning',
  'thunder_bolt',
  'great_fireball',
  'teleport',
  'blast_fire',
  'fire_wall',
  'lightning_exorcism',
  'magic_shield',
  'holy_word',
  'ice_thunder',
  'healing',
  'spirit_sword',
  'poison_amulet',
  'soul_fire',
  'summon_skeleton',
  'invisibility',
  'mass_invisibility',
  'ghost_shield',
  'holy_armor',
  'trap_curse',
  'mass_heal',
  'summon_beast',
]

describe('monster-visuals', () => {
  it('覆盖后端全部怪物 JSON（一怪一表，防漏怪）', () => {
    const ids = backendMonsterIds()
    expect(ids.length).toBe(60)
    for (const id of ids) {
      expect(MONSTER_VISUALS, `缺少怪物视觉: ${id}`).toHaveProperty(id)
    }
  })

  it('tier 只能是 normal/elite/boss', () => {
    for (const [id, v] of Object.entries(MONSTER_VISUALS)) {
      expect(['normal', 'elite', 'boss'], `${id} tier 非法`).toContain(v.tier)
    }
  })

  it('monsterVisualOf 未知 id 返回兜底且不抛错', () => {
    expect(() => monsterVisualOf('not_a_monster')).not.toThrow()
    expect(monsterVisualOf('not_a_monster')).toEqual({
      emoji: '👹',
      tint: 'text-slate-300',
      tier: 'normal',
    })
  })
})

describe('map-themes', () => {
  it('MAPS 每个 region 都有主题', () => {
    const regions = new Set(Object.values(MAPS).map((m) => m.region))
    for (const region of regions) {
      expect(MAP_THEMES, `缺少地图主题: ${region}`).toHaveProperty(region)
    }
  })

  it('mapThemeOf 未知/缺省 region 返回 default 且不抛错', () => {
    expect(() => mapThemeOf('nowhere')).not.toThrow()
    expect(mapThemeOf('nowhere')).toBe(MAP_THEMES.default)
    expect(() => mapThemeOf(undefined)).not.toThrow()
    expect(mapThemeOf(undefined)).toBe(MAP_THEMES.default)
  })
})

describe('skill-visuals', () => {
  it('覆盖全部 32 个技能', () => {
    expect(Object.keys(SKILL_VISUALS).length).toBe(32)
    for (const id of EXPECTED_SKILL_IDS) {
      expect(SKILL_VISUALS, `缺少技能视觉: ${id}`).toHaveProperty(id)
    }
  })

  it('skillVisualOf 未知 id 返回兜底且不抛错', () => {
    expect(() => skillVisualOf('not_a_skill')).not.toThrow()
    expect(skillVisualOf('not_a_skill')).toEqual({
      emoji: '✨',
      colorClass: 'text-slate-200',
    })
  })
})
