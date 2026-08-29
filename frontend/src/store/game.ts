import { create } from 'zustand'
import { MAPS } from '../data/gamedata'

/** 三职业（与后端 todo 3 建号契约对齐） */
export type CharacterClass = 'warrior' | 'mage' | 'taoist'

export interface PlayerSnapshot {
  id: string
  username: string
}

export interface CharacterSnapshot {
  id: string
  name: string
  class: CharacterClass
  level: number
  hp: number
  mp: number
  gold: number
  bindCity: string
  /** SSE stat.exp / GET me 的 exp；旧快照可缺省（占位期构造点未带） */
  exp?: number
  /** 当前所在地图 id（GET me map_id / SSE zone_change 更新） */
  mapId?: string
  /** HP/MP 上限（SSE stat.max_hp/max_mp；GET me 无此字段，首条 stat 前缺省） */
  maxHp?: number
  maxMp?: number
}

/** 面板快照：与后端 StatPanel（combat/stats.py）字段一一对应（snake_case 直传） */
export type PanelSnapshot = Record<string, number>

/** 怪物血条条目：由战斗日志（attack/kill）派生——stat 事件不含怪物字段 */
export interface MonsterBar {
  /** 以怪物名为 key（同名怪合并一条，MVP 简化） */
  key: string
  name: string
  hp: number
  maxHp: number
}

/** 地面掉落条目：drop 事件中 picked=false 的物品，等待手动拾取 */
export interface GroundDrop {
  dropId: string
  itemId: string
  qty: number
}

interface GameStore {
  player: PlayerSnapshot | null
  character: CharacterSnapshot | null
  panel: PanelSnapshot | null
  monsters: MonsterBar[]
  groundDrops: GroundDrop[]
  /**
   * 局部更新登录态/角色快照：
   *  - setGame({ character }) 只换角色，保留 player
   *  - setGame({ player: null, character: null }) 清登录态（401 拦截器调用）
   */
  setGame: (patch: {
    player?: PlayerSnapshot | null
    character?: CharacterSnapshot | null
  }) => void
  /** 清空本地登录态（等价 setGame 双 null），401 时由 api/rest.ts 调用 */
  clearAuth: () => void

  /**
   * SSE stat 事件入账（tick 心跳与 REST 触发的面板推送共用形状）：
   * { tick?, reason?, hp, max_hp, mp, max_mp, exp, gold, panel? }
   */
  applyStat: (raw: Record<string, unknown>) => void
  /** SSE zone_change 事件：切地图 + 清空怪物/地面掉落（换图语义） */
  applyZoneChange: (mapId: string) => void
  /**
   * 战斗日志批量派生怪物血条（attack 命中剩余血量 / kill 移除）。
   * 批量入口保证一帧多条日志只触发一次 store 更新。
   */
  applyCombatLogs: (entries: ReadonlyArray<{ kind: string; text: string }>) => void
  /** SSE drop 事件 loot[]：picked=false 的物品进地面池（条目含 gold/item_id/qty/picked/drop_id） */
  applyDropLoot: (loot: ReadonlyArray<Record<string, unknown>>) => void
  /** 拾取成功后移除地面条目 */
  removeGroundDrop: (dropId: string) => void
  /** 登出/会话重置：清空战斗瞬态（面板/怪物/地面掉落） */
  resetTransient: () => void
}

// --- 战斗日志解析（后端 tick_loop._log_event 文案契约，勿在别处复制） -------------

/** 「你命中小野雉，造成 3 点伤害（剩余 2）」→ { name, remaining } */
const ATTACK_RE = /^你命中(.+?)，造成 \d+ 点伤害（剩余 (\d+)）$/
/** 「你击杀了小野雉，获得 9 经验」→ 小野雉 */
const KILL_RE = /^你击杀了(.+?)，获得 \d+ 经验$/

export function parseAttack(text: string): { name: string; remaining: number } | null {
  const m = ATTACK_RE.exec(text)
  if (!m) return null
  return { name: m[1]!, remaining: Number(m[2]) }
}

export function parseKill(text: string): string | null {
  const m = KILL_RE.exec(text)
  return m ? m[1]! : null
}

function upsertMonster(list: MonsterBar[], name: string, hp: number): MonsterBar[] {
  const idx = list.findIndex((m) => m.key === name)
  if (idx >= 0) {
    const prev = list[idx]!
    const next: MonsterBar = {
      ...prev,
      hp,
      maxHp: Math.max(prev.maxHp, hp),
    }
    const copy = list.slice()
    copy[idx] = next
    return copy
  }
  // 首见按当前剩余血量近似满血（怪出生即满血；命中日志不透露上限）
  return [...list, { key: name, name, hp, maxHp: Math.max(hp, 1) }]
}

export const useGameStore = create<GameStore>((set) => ({
  player: null,
  character: null,
  panel: null,
  monsters: [],
  groundDrops: [],

  setGame: (patch) =>
    set((state) => ({
      player: patch.player !== undefined ? patch.player : state.player,
      character:
        patch.character !== undefined ? patch.character : state.character,
    })),

  clearAuth: () =>
    set({
      player: null,
      character: null,
      panel: null,
      monsters: [],
      groundDrops: [],
    }),

  applyStat: (raw) =>
    set((state) => {
      if (!state.character) return state
      const num = (v: unknown, fallback: number): number =>
        typeof v === 'number' && Number.isFinite(v) ? v : fallback
      const character: CharacterSnapshot = {
        ...state.character,
        hp: num(raw.hp, state.character.hp),
        mp: num(raw.mp, state.character.mp),
        exp: num(raw.exp, state.character.exp ?? 0),
        gold: num(raw.gold, state.character.gold),
        mapId:
          typeof raw.map_id === 'string' ? raw.map_id : state.character.mapId,
        maxHp: num(raw.max_hp, state.character.maxHp ?? 0),
        maxMp: num(raw.max_mp, state.character.maxMp ?? 0),
      }
      const panel =
        raw.panel && typeof raw.panel === 'object'
          ? (raw.panel as PanelSnapshot)
          : state.panel
      return { character, panel }
    }),

  applyZoneChange: (mapId) =>
    set((state) => {
      const character = state.character
        ? { ...state.character, mapId }
        : state.character
      return { character, monsters: [], groundDrops: [] }
    }),

  applyCombatLogs: (entries) =>
    set((state) => {
      let monsters = state.monsters
      for (const { kind, text } of entries) {
        if (kind === 'attack') {
          const hit = parseAttack(text)
          if (hit) monsters = upsertMonster(monsters, hit.name, hit.remaining)
        } else if (kind === 'kill') {
          const name = parseKill(text)
          if (name) monsters = monsters.filter((m) => m.key !== name)
        }
      }
      if (monsters === state.monsters) return state
      return { monsters }
    }),

  applyDropLoot: (loot) =>
    set((state) => {
      const additions: GroundDrop[] = []
      for (const entry of loot) {
        if (entry.picked !== false) continue
        if (typeof entry.item_id !== 'string') continue
        if (typeof entry.drop_id !== 'string') continue
        additions.push({
          dropId: entry.drop_id,
          itemId: entry.item_id,
          qty: typeof entry.qty === 'number' ? entry.qty : 1,
        })
      }
      if (additions.length === 0) return state
      return { groundDrops: [...state.groundDrops, ...additions] }
    }),

  removeGroundDrop: (dropId) =>
    set((state) => ({
      groundDrops: state.groundDrops.filter((d) => d.dropId !== dropId),
    })),

  resetTransient: () => set({ panel: null, monsters: [], groundDrops: [] }),
}))

/** selector：登录态派生值，避免订阅整个 store 造成重渲染 */
export const selectIsLoggedIn = (s: GameStore): boolean => s.player !== null

/** selector：当前地图定义（未知地图返回 null，调用方降级展示原始 mapId） */
export const selectCurrentMap = (s: GameStore) => {
  const mapId = s.character?.mapId
  return mapId ? (MAPS[mapId] ?? null) : null
}
