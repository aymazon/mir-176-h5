import { useEffect, useState } from 'react'
import { useTeleportStore } from '../store/teleport'
import type { TeleportDestination } from '../store/teleport'
import { apiWarp, refreshTeleportPanel } from '../api/teleport'
import { useGameStore } from '../store/game'
import { useInventoryStore } from '../store/inventory'

/**
 * 传送 Tab（todo 32，接 todo 30 后端 /api/teleport/*）：
 *  - 面板：当前图 + 绑定主城 + 行进中倒计时横幅
 *  - 目的地清单（地表路线 / 洞穴层档）含费用；两段式确认（点「传送」→ 变「确认」
 *    再点执行），防误触扣费
 *  - warp 成功：扣费金币同步 game/inventory store + 本地倒计时；
 *    到点切图由既有 SSE zone_change(reason=travel) 落地（不新增订阅）
 */

/** 目的地稳定键（洞穴层同 map_id 不同 depth 需区分） */
function destKey(d: TeleportDestination): string {
  return `${d.map_id}:${d.depth ?? 0}`
}

export default function TeleportTab() {
  const panel = useTeleportStore((s) => s.panel)
  const characterGold = useGameStore((s) => s.character?.gold)
  const bagGold = useInventoryStore((s) => s.gold)
  const gold = characterGold ?? bagGold

  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  /** 两段式确认：待确认的目的地键 */
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  /** 本地倒计时秒（warp 响应 / panel.travel_remaining_seconds 驱动） */
  const [countdown, setCountdown] = useState<number | null>(null)

  useEffect(() => {
    void refreshTeleportPanel().then((p) => {
      if (p?.traveling && p.travel_remaining_seconds != null) {
        setCountdown(Math.ceil(p.travel_remaining_seconds))
      }
    })
  }, [])

  // toast 自动消退（InventoryTab 同款）
  useEffect(() => {
    if (notice === null) return
    const timer = window.setTimeout(() => setNotice(null), 2500)
    return () => window.clearTimeout(timer)
  }, [notice])

  // 行进倒计时：每秒递减；归零清行进态并重拉面板（切图本身由 SSE zone_change 承担）
  useEffect(() => {
    if (countdown === null) return
    if (countdown <= 0) {
      setCountdown(null)
      useTeleportStore.getState().clearTraveling()
      void refreshTeleportPanel()
      return
    }
    const timer = window.setTimeout(() => setCountdown((c) => (c ?? 1) - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [countdown])

  const traveling = (panel?.traveling ?? false) || countdown !== null

  async function warp(d: TeleportDestination): Promise<void> {
    if (busy || traveling) return
    const key = destKey(d)
    if (pendingKey !== key) {
      setPendingKey(key)
      return
    }
    setPendingKey(null)
    setBusy(true)
    try {
      const out = await apiWarp(d.map_id, d.depth)
      // 扣费金币同步两处快照（SSE stat 随后也会覆盖同值，幂等）
      useGameStore.getState().applyStat({ gold: out.gold_after })
      useInventoryStore.getState().setSnapshot({ gold: out.gold_after })
      useTeleportStore.getState().markTraveling(out.target_map_id, out.travel_seconds)
      setCountdown(out.travel_seconds)
      setNotice(`踏上前往 ${d.name} 的路途（-${out.fee} 金）`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '传送失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section data-testid="tab-panel-teleport" aria-label="传送" className="space-y-3">
      {notice !== null && (
        <div
          data-testid="toast"
          role="status"
          className="fixed inset-x-0 top-12 z-50 mx-auto w-fit max-w-[440px] rounded-full border border-amber-400/50 bg-slate-900/95 px-4 py-1.5 text-xs text-amber-200 shadow-lg"
        >
          {notice}
        </div>
      )}

      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold text-amber-300">传送</h1>
        <span data-testid="teleport-gold" className="font-mono text-xs text-amber-200">
          {gold} 金
        </span>
      </div>

      {/* --- 当前位置 / 行进中 --- */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <div className="flex items-baseline justify-between text-sm">
          <span className="text-slate-400">当前位置</span>
          <span data-testid="teleport-current" className="text-slate-200">
            {panel?.current_map_name ?? '…'}
          </span>
        </div>
        <div className="mt-1 flex items-baseline justify-between text-[10px] text-slate-500">
          <span>回城绑定</span>
          <span data-testid="teleport-bind">{panel?.bind_city ?? '—'}</span>
        </div>
        {traveling && (
          <div
            data-testid="traveling-banner"
            className="mt-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-xs text-sky-300"
          >
            行进中
            {panel?.travel_target_map_id ? ` → ${panel.travel_target_map_id}` : ''}
            {countdown !== null && (
              <span data-testid="travel-countdown" className="ml-1 font-mono">
                约 {countdown}s 到达
              </span>
            )}
          </div>
        )}
      </div>

      {/* --- 目的地清单 --- */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <h2 className="mb-2 text-xs font-semibold text-slate-400">可达目的地</h2>
        {!panel || panel.destinations.length === 0 ? (
          <p data-testid="teleport-empty" className="text-xs text-slate-600">
            （当前位置没有可达的传送路线）
          </p>
        ) : (
          <ul data-testid="teleport-list" className="space-y-2">
            {panel.destinations.map((d) => {
              const key = destKey(d)
              const affordable = gold >= d.fee
              const confirming = pendingKey === key
              return (
                <li
                  key={key}
                  data-testid={`teleport-dest-${key}`}
                  className="flex items-center justify-between gap-2"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm text-slate-200">
                      {d.name}
                      {d.kind === 'cave' && (
                        <span className="ml-1 text-[10px] text-violet-300">洞穴</span>
                      )}
                    </div>
                    <div
                      data-testid={`teleport-fee-${key}`}
                      className={`font-mono text-[10px] ${
                        affordable ? 'text-amber-200/80' : 'text-rose-400'
                      }`}
                    >
                      {d.fee} 金{!affordable && '（不足）'}
                    </div>
                  </div>
                  <button
                    type="button"
                    data-testid={`teleport-warp-${key}`}
                    disabled={busy || traveling || !affordable}
                    onClick={() => void warp(d)}
                    className={`shrink-0 rounded px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 ${
                      confirming
                        ? 'bg-amber-600 hover:bg-amber-500'
                        : 'bg-sky-600 hover:bg-sky-500'
                    }`}
                  >
                    {confirming ? `确认 ${d.fee} 金` : '传送'}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}
