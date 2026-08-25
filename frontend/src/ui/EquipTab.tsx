import { useEffect, useState } from 'react'
import {
  EQUIP_SLOT_KEYS,
  useInventoryStore,
  inferSlotForKind,
  EQUIPMENT_KINDS,
} from '../store/inventory'
import type { EquipSlotKey } from '../store/inventory'
import { useGameStore } from '../store/game'
import { itemEntry } from '../data/gamedata'
import { apiEquip, apiUnequip, refreshBag } from '../api/game'

/**
 * 装备 Tab（todo 31）：
 *  - 10 槽位（左右手镯/戒指分槽）+ 穿戴/卸下（REST）
 *  - 面板数值 ← SSE stat.panel（equip/unequip/repair/buy/sell 触发时推送）
 *  - 耐久条 = bag 实例 durability / gamedata maxDurability
 */

const SLOT_LABELS: Record<EquipSlotKey, string> = {
  weapon: '武器',
  armor: '衣服',
  helmet: '头盔',
  necklace: '项链',
  braceletL: '左手镯',
  braceletR: '右手镯',
  ringL: '左戒指',
  ringR: '右戒指',
  belt: '腰带',
  boots: '靴子',
}

/** 面板展示行：key = StatPanel 字段；label 中文 */
const PANEL_ROWS: Array<{ key: string; label: string; range?: boolean }> = [
  { key: 'dc', label: '攻击', range: true },
  { key: 'mc', label: '魔法', range: true },
  { key: 'sc', label: '道术', range: true },
  { key: 'ac', label: '防御', range: true },
  { key: 'mac', label: '魔御', range: true },
  { key: 'luck', label: '幸运' },
  { key: 'accuracy', label: '准确' },
  { key: 'agility', label: '敏捷' },
  { key: 'attack_speed', label: '攻速' },
  { key: 'strength', label: '腕力' },
]

function panelValue(
  panel: Record<string, number> | null,
  row: (typeof PANEL_ROWS)[number],
): string {
  if (panel === null) return '—'
  if (row.range) {
    const min = panel[`${row.key}_min`]
    const max = panel[`${row.key}_max`]
    if (min === undefined || max === undefined) return '—'
    return `${min}-${max}`
  }
  const v = panel[row.key]
  return v === undefined ? '—' : String(v)
}

export default function EquipTab() {
  const equipSlots = useInventoryStore((s) => s.equipSlots)
  const equippedBySlot = useInventoryStore((s) => s.equippedInstanceBySlot)
  const items = useInventoryStore((s) => s.items)
  const character = useGameStore((s) => s.character)
  const panel = useGameStore((s) => s.panel)

  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void refreshBag()
  }, [])

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

  const unequipSlot = (slot: EquipSlotKey) =>
    run(async () => {
      const out = await apiUnequip(slot)
      useInventoryStore.getState().markUnequipped(slot)
      useGameStore.getState().applyStat({ panel: out.panel })
      return `已卸下 ${SLOT_LABELS[slot]}`
    })

  const wearItem = (instanceId: string, itemId: string) =>
    run(async () => {
      const kind = itemEntry(itemId)?.kind
      const slot = inferSlotForKind(kind, equipSlots)
      if (slot === null) return '没有可用槽位'
      const out = await apiEquip(instanceId, slot)
      useInventoryStore
        .getState()
        .markEquipped(slot, out.item_id, String(out.item_instance_id))
      useGameStore.getState().applyStat({ panel: out.panel })
      return `已穿戴 ${itemEntry(out.item_id)?.name ?? out.item_id}`
    })

  // 可穿戴的背包物品（装备类且未在任一槽位上）
  const equippedAll = new Set(
    Object.values(equippedBySlot).filter((v): v is string => v !== null),
  )
  const wearable = items.filter(
    (it) => EQUIPMENT_KINDS.has(it.kind ?? '') && !equippedAll.has(it.id),
  )

  return (
    <section data-testid="tab-panel-equip" aria-label="装备" className="space-y-3">
      {notice !== null && (
        <div
          data-testid="toast"
          role="status"
          className="fixed inset-x-0 top-12 z-50 mx-auto w-fit max-w-[440px] rounded-full border border-amber-400/50 bg-slate-900/95 px-4 py-1.5 text-xs text-amber-200 shadow-lg"
        >
          {notice}
        </div>
      )}

      <h1 className="text-lg font-semibold text-amber-300">装备</h1>

      {/* --- 面板数值 --- */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-xs font-semibold text-slate-400">面板数值</h2>
          <span className="text-[10px] text-slate-500">
            {character ? `${character.name} Lv.${character.level}` : ''}
          </span>
        </div>
        <dl data-testid="panel-stats" className="grid grid-cols-3 gap-x-3 gap-y-1.5 text-[11px]">
          <div className="flex justify-between">
            <dt className="text-slate-500">HP</dt>
            <dd data-testid="panel-hp" className="font-mono text-slate-200">
              {character ? character.hp : '—'}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">MP</dt>
            <dd data-testid="panel-mp" className="font-mono text-slate-200">
              {character ? character.mp : '—'}
            </dd>
          </div>
          {PANEL_ROWS.map((row) => (
            <div key={row.key} className="flex justify-between">
              <dt className="text-slate-500">{row.label}</dt>
              <dd
                data-testid={`panel-${row.key}`}
                className="font-mono text-slate-200"
              >
                {panelValue(panel, row)}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {/* --- 10 槽位 --- */}
      <ul className="divide-y divide-slate-800 rounded-lg border border-slate-800 bg-slate-900/60">
        {EQUIP_SLOT_KEYS.map((slot) => {
          const itemId = equipSlots[slot]
          const entry = itemId ? itemEntry(itemId) : null
          const instanceId = equippedBySlot[slot]
          const inst = instanceId
            ? items.find((it) => it.id === instanceId)
            : undefined
          const maxDur = entry?.maxDurability ?? 0
          // 后端契约：durability NULL 视为满耐久（repair 同口径）；无耐久概念不渲染条
          const curDur =
            inst?.durability !== undefined && inst.durability !== null
              ? inst.durability
              : maxDur > 0
                ? maxDur
                : null
          const durPct =
            maxDur > 0 && curDur !== null
              ? Math.min(100, (curDur / maxDur) * 100)
              : null
          return (
            <li
              key={slot}
              data-testid={`equip-slot-${slot}`}
              className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
            >
              <span className="w-14 shrink-0 text-slate-500">
                {SLOT_LABELS[slot]}
              </span>
              <span className="min-w-0 flex-1">
                {itemId ? (
                  <>
                    <span
                      className="block truncate text-slate-200"
                      data-testid={`equip-item-${slot}`}
                    >
                      {entry?.name ?? itemId}
                    </span>
                    {durPct !== null ? (
                      <span className="mt-0.5 flex items-center gap-1.5">
                        <span className="h-1 w-20 overflow-hidden rounded-full bg-slate-800">
                          <span
                            data-testid={`durability-bar-${slot}`}
                            className={`block h-full rounded-full ${
                              durPct > 50
                                ? 'bg-emerald-500'
                                : durPct > 20
                                  ? 'bg-amber-400'
                                  : 'bg-red-500'
                            }`}
                            style={{ width: `${durPct}%` }}
                          />
                        </span>
                        <span
                          data-testid={`durability-text-${slot}`}
                          className="font-mono text-[10px] text-slate-500"
                        >
                          耐久 {curDur}/{maxDur}
                        </span>
                      </span>
                    ) : (
                      <span className="text-[10px] text-slate-600">耐久良好</span>
                    )}
                  </>
                ) : (
                  <span className="text-slate-600">—</span>
                )}
              </span>
              {itemId && (
                <button
                  type="button"
                  data-testid={`unequip-${slot}`}
                  disabled={busy}
                  onClick={() => void unequipSlot(slot)}
                  className="shrink-0 rounded border border-slate-600 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                >
                  卸下
                </button>
              )}
            </li>
          )
        })}
      </ul>

      {/* --- 可穿戴背包物品 --- */}
      {wearable.length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <h2 className="mb-2 text-xs font-semibold text-slate-400">
            背包中可穿戴
          </h2>
          <ul className="flex flex-wrap gap-2">
            {wearable.map((it) => (
              <li key={it.id}>
                <button
                  type="button"
                  data-testid={`wear-${it.id}`}
                  disabled={busy}
                  onClick={() => void wearItem(it.id, it.itemId)}
                  className="rounded-full border border-emerald-500/50 bg-slate-950 px-3 py-1 text-[11px] text-emerald-300 hover:bg-slate-800 disabled:opacity-50"
                >
                  穿戴 {it.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
