import { requestJson } from './game'
import { useTeleportStore } from '../store/teleport'
import type { TeleportPanel } from '../store/teleport'

/**
 * 传送 REST 薄封装（todo 30 后端契约 / todo 32 前端接入）：
 *  - GET  /api/teleport/panel  当前图 + 可达目的地费率清单 + 行进中状态
 *  - POST /api/teleport/warp   付费传送（扣金币 + traveling_until 倒计时登记）
 * warp 成功后目的地切换由 SSE zone_change(reason=travel) 落地（既有接线），
 * 本模块只回传响应体供组件展示倒计时/扣费。
 */

export interface WarpOut {
  target_map_id: string
  layer: number | null
  fee: number
  gold_after: number
  travel_seconds: number
  /** time.time() 墙钟到期时刻 */
  traveling_until: number
}

/** 拉传送面板并同步 teleport store；失败保留既有快照返回 null */
export async function refreshTeleportPanel(): Promise<TeleportPanel | null> {
  try {
    const out = await requestJson<TeleportPanel>('/api/teleport/panel')
    useTeleportStore.getState().setPanel(out)
    return out
  } catch {
    return null
  }
}

export function apiWarp(targetMapId: string, layer: number | null): Promise<WarpOut> {
  return requestJson<WarpOut>('/api/teleport/warp', {
    json: { target_map_id: targetMapId, layer },
  })
}
