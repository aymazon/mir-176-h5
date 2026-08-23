import { create } from 'zustand'

/** 三职业（与后端 todo 3 建号契约对齐） */
export type CharacterClass = 'warrior' | 'mage' | 'taoist'

export interface PlayerSnapshot {
  id: string
  username: string
}

/** 角色快照：字段为后端 GET /api/character/me 契约的子集占位 */
export interface CharacterSnapshot {
  id: string
  name: string
  class: CharacterClass
  level: number
  hp: number
  mp: number
  gold: number
  bindCity: string
}

interface GameStore {
  player: PlayerSnapshot | null
  character: CharacterSnapshot | null
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
}

export const useGameStore = create<GameStore>((set) => ({
  player: null,
  character: null,

  setGame: (patch) =>
    set((state) => ({
      player: patch.player !== undefined ? patch.player : state.player,
      character:
        patch.character !== undefined ? patch.character : state.character,
    })),

  clearAuth: () => set({ player: null, character: null }),
}))

/** selector：登录态派生值，避免订阅整个 store 造成重渲染 */
export const selectIsLoggedIn = (s: GameStore): boolean => s.player !== null
