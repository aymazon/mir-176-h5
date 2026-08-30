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
  /** SSE zone_change 事件：切地图 + 清空地面掉落（换图语义；场景清理由 scene store resetScene 自理） */
  applyZoneChange: (mapId: string) => void
  /** SSE drop 事件 loot[]：picked=false 的物品进地面池（条目含 gold/item_id/qty/picked/drop_id） */
  applyDropLoot: (loot: ReadonlyArray<Record<string, unknown>>) => void
  /** 拾取成功后移除地面条目 */
  removeGroundDrop: (dropId: string) => void
}

export const useGameStore = create<GameStore>((set) => ({
  player: null,
  character: null,
  panel: null,
  // 遗留运行时占位：BattleTab 旧怪物面板仍订阅 s.monsters（恒空数组），todo 13 随面板一并删除
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
      return { character, groundDrops: [] }
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
}))

/** selector：登录态派生值，避免订阅整个 store 造成重渲染 */
export const selectIsLoggedIn = (s: GameStore): boolean => s.player !== null

/** selector：当前地图定义（未知地图返回 null，调用方降级展示原始 mapId） */
export const selectCurrentMap = (s: GameStore) => {
  const mapId = s.character?.mapId
  return mapId ? (MAPS[mapId] ?? null) : null
}
