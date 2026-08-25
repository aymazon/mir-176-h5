import { useEffect, useMemo, useState } from 'react'
import {
  useInventoryStore,
  inferSlotForKind,
  EQUIPMENT_KINDS,
} from '../store/inventory'
import type { BagItem } from '../store/inventory'
import { useGameStore } from '../store/game'
import { itemEntry } from '../data/gamedata'
import { apiEquip, apiPick, apiSell, apiUse, refreshBag } from '../api/game'

/**
 * 背包 Tab（todo 31）：
 *  - 网格列表（可堆叠物由后端合并行，角标显示数量）+ 空格计数
 *  - 地面掉落条（drop 事件 picked=false 的物品）→ 点击拾取 POST /api/inventory/pick
 *  - 点物品 → 底部动作单：使用（potion/scroll）/ 穿戴（装备类）/ 卖出（POST /api/shop/sell）
 *  - 全部操作走 REST；失败 toast 错误不崩溃
 */

const KIND_LABELS: Record<string, string> = {
  potion: '药品',
  scroll: '卷轴',
  weapon: '武器',
  armor: '衣服',
  helmet: '头盔',
  necklace: '项链',
  bracelet: '手镯',
  ring: '戒指',
  belt: '腰带',
  boots: '靴子',
  book: '技能书',
  material: '素材',
}

export default function InventoryTab() {
  const items = useInventoryStore((s) => s.items)
  const capacity = useInventoryStore((s) => s.capacity)
  const gold = useInventoryStore((s) => s.gold)
  const equippedBySlot = useInventoryStore((s) => s.equippedInstanceBySlot)
  const equipSlots = useInventoryStore((s) => s.equipSlots)
  const groundDrops = useGameStore((s) => s.groundDrops)

  const equippedIds = useMemo(
    () =>
      new Set(
        Object.values(equippedBySlot).filter((v): v is string => v !== null),
      ),
    [equippedBySlot],
  )

  const [active, setActive] = useState<BagItem | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void refreshBag()
  }, [])

  // toast 自动消退
  useEffect(() => {
    if (notice === null) return
    const timer = window.setTimeout(() => setNotice(null), 2500)
    return () => window.clearTimeout(timer)
  }, [notice])

  async function run(action: () => Promise<string>): Promise<void> {
    if (busy) return
    setBusy(true)
    try {
      setNotice(await action())
      await refreshBag()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const pickDrop = (dropId: string) =>
    run(async () => {
      const out = await apiPick(dropId)
      useGameStore.getState().removeGroundDrop(dropId)
      return `拾取 ${out.name} ×${out.quantity}`
    })

  const consumeItem = (item: BagItem) =>
    run(async () => {
      const out = await apiUse(item.id)
      setActive(null)
      return out.message || `使用了 ${out.name}`
    })

  const sellItem = (item: BagItem, qty: number) =>
    run(async () => {
      const out = await apiSell(item.id, qty)
      setActive(null)
      return `卖出 ${out.name} ×${out.quantity_sold}，+${out.total_earned} 金`
    })

  const wearItem = (item: BagItem) =>
    run(async () => {
      const slot = inferSlotForKind(item.kind, equipSlots)
      if (slot === null) return '没有可用槽位'
      const out = await apiEquip(item.id, slot)
      useInventoryStore
        .getState()
        .markEquipped(slot, out.item_id, String(out.item_instance_id))
      useGameStore.getState().applyStat({ panel: out.panel })
      setActive(null)
      return `已穿戴 ${itemEntry(out.item_id)?.name ?? out.item_id}`
    })

  const used = items.length
  const empty = Math.max(0, capacity - used)

  return (
    <section data-testid="tab-panel-inventory" aria-label="背包" className="space-y-3">
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
        <h1 className="text-lg font-semibold text-amber-300">背包</h1>
        <span data-testid="bag-slots" className="text-xs text-slate-400">
          金币 <span data-testid="bag-gold" className="font-mono text-amber-200">{gold}</span> · 空格{' '}
          <span data-testid="bag-empty-count" className="font-mono">
            {empty}
          </span>
          /{capacity}
        </span>
      </div>

      {/* --- 地面掉落（手动拾取）--- */}
      {groundDrops.length > 0 && (
        <div
          data-testid="ground-drops"
          className="rounded-lg border border-amber-500/30 bg-amber-400/5 p-2.5"
        >
          <h2 className="mb-1.5 text-xs font-semibold text-amber-300/90">
            地面掉落
          </h2>
          <ul className="flex flex-wrap gap-2">
            {groundDrops.map((d) => (
              <li key={d.dropId}>
                <button
                  type="button"
                  data-testid={`pick-${d.dropId}`}
                  disabled={busy}
                  onClick={() => void pickDrop(d.dropId)}
                  className="rounded-full border border-amber-400/60 bg-slate-900 px-3 py-1 text-[11px] text-amber-200 hover:bg-slate-800 disabled:opacity-50"
                >
                  拾取 {itemEntry(d.itemId)?.name ?? d.itemId} ×{d.qty}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* --- 物品格子 --- */}
      <div className="grid grid-cols-5 gap-2">
        {Array.from({ length: capacity }, (_, i) => {
          const item = items[i]
          if (!item) {
            return (
              <div
                key={i}
                data-testid={`bag-cell-${i}`}
                className="aspect-square rounded border border-dashed border-slate-800 bg-slate-950/40"
              />
            )
          }
          const equipped = equippedIds.has(item.id)
          return (
            <button
              type="button"
              key={i}
              data-testid={`bag-cell-${i}`}
              onClick={() => setActive(item)}
              className={`relative flex aspect-square flex-col items-center justify-center overflow-hidden rounded border p-1 text-center ${
                equipped
                  ? 'border-emerald-500/60 bg-emerald-500/10'
                  : 'border-slate-700 bg-slate-900/60 hover:border-amber-400/60'
              }`}
            >
              <span className="line-clamp-2 w-full break-all text-[10px] leading-tight text-slate-200">
                {item.name}
              </span>
              {item.qty > 1 && (
                <span
                  data-testid={`bag-qty-${i}`}
                  className="absolute bottom-0.5 right-1 font-mono text-[10px] text-amber-300"
                >
                  ×{item.qty}
                </span>
              )}
              {equipped && (
                <span className="absolute left-1 top-0.5 text-[9px] text-emerald-400">
                  已穿
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* --- 动作单 --- */}
      {active !== null && (
        <div
          data-testid="action-sheet"
          className="fixed inset-x-0 bottom-[57px] z-40 mx-auto max-w-[480px] px-3"
        >
          <div className="rounded-xl border border-slate-700 bg-slate-900 p-4 shadow-2xl">
            <div className="mb-3 flex items-baseline justify-between">
              <span className="text-sm font-semibold text-slate-100">
                {active.name}
                {active.qty > 1 && (
                  <span className="ml-1 font-mono text-xs text-amber-300">
                    ×{active.qty}
                  </span>
                )}
              </span>
              <span className="text-[10px] text-slate-500">
                {KIND_LABELS[active.kind ?? ''] ?? active.kind ?? '物品'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {(active.kind === 'potion' || active.kind === 'scroll') && (
                <button
                  type="button"
                  data-testid="act-use"
                  disabled={busy}
                  onClick={() => void consumeItem(active)}
                  className="rounded bg-sky-600 py-2 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
                >
                  使用
                </button>
              )}
              {EQUIPMENT_KINDS.has(active.kind ?? '') &&
                inferSlotForKind(active.kind, equipSlots) !== null && (
                  <button
                    type="button"
                    data-testid="act-wear"
                    disabled={busy}
                    onClick={() => void wearItem(active)}
                    className="rounded bg-emerald-600 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                  >
                    穿戴
                  </button>
                )}
              <button
                type="button"
                data-testid="act-sell"
                disabled={busy}
                onClick={() => void sellItem(active, 1)}
                className="rounded bg-slate-700 py-2 text-xs font-semibold text-slate-100 hover:bg-slate-600 disabled:opacity-50"
              >
                卖出×1
              </button>
              {active.qty > 1 && (
                <button
                  type="button"
                  data-testid="act-sell-all"
                  disabled={busy}
                  onClick={() => void sellItem(active, active.qty)}
                  className="rounded bg-slate-700 py-2 text-xs font-semibold text-slate-100 hover:bg-slate-600 disabled:opacity-50"
                >
                  卖出全部
                </button>
              )}
              <button
                type="button"
                data-testid="act-close"
                onClick={() => setActive(null)}
                className="rounded border border-slate-600 py-2 text-xs text-slate-300 hover:bg-slate-800"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      {items.length === 0 && groundDrops.length === 0 && (
        <p data-testid="bag-empty" className="text-xs text-slate-600">
          （背包空空如也，去战斗页挂机捡点战利品）
        </p>
      )}
    </section>
  )
}
