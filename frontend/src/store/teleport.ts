import { create } from 'zustand'

/**
 * 传送面板 store（todo 32）：GET /api/teleport/panel 投影。
 *  - destinations：可达目的地（route=地表邻接路线 / cave=洞穴层档），费用后端算好
 *  - traveling：warp 成功后本地登记（目标图 + 到期墙钟），zone_change 到达后清除
 */

export interface TeleportDestination {
  map_id: string
  name: string
  fee: number
  /** route=地表路线 | cave=洞穴层 */
  kind: string
  /** 洞穴层深度档（地表路线为 null） */
  depth: number | null
}

export interface TeleportPanel {
  current_map_id: string
  current_map_name: string
  bind_city: string
  traveling: boolean
  travel_remaining_seconds: number | null
  travel_target_map_id: string | null
  destinations: TeleportDestination[]
}

interface TeleportStore {
  panel: TeleportPanel | null
  setPanel: (panel: TeleportPanel) => void
  /** warp 成功：登记行进中（目标/倒计时/扣费后金币由组件层另行同步 game store） */
  markTraveling: (targetMapId: string, seconds: number) => void
  /** zone_change 到达 / 倒计时结束：清行进态 */
  clearTraveling: () => void
  reset: () => void
}

export const useTeleportStore = create<TeleportStore>((set) => ({
  panel: null,

  setPanel: (panel) => set({ panel }),

  markTraveling: (targetMapId, seconds) =>
    set((state) =>
      state.panel === null
        ? state
        : {
            panel: {
              ...state.panel,
              traveling: true,
              travel_target_map_id: targetMapId,
              travel_remaining_seconds: seconds,
            },
          },
    ),

  clearTraveling: () =>
    set((state) =>
      state.panel === null
        ? state
        : {
            panel: {
              ...state.panel,
              traveling: false,
              travel_target_map_id: null,
              travel_remaining_seconds: null,
            },
          },
    ),

  reset: () => set({ panel: null }),
}))
