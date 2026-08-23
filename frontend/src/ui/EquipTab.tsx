import { EQUIP_SLOT_KEYS, useInventoryStore } from '../store/inventory'
import type { EquipSlotKey } from '../store/inventory'

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

/** 装备 Tab 占位：todo 23 穿戴 API 就绪后接门槛校验与面板重算 */
export default function EquipTab() {
  const equipSlots = useInventoryStore((s) => s.equipSlots)

  return (
    <section data-testid="tab-panel-equip" aria-label="装备">
      <h1 className="mb-2 text-lg font-semibold text-amber-300">装备</h1>
      <ul className="divide-y divide-slate-800 rounded-lg border border-slate-800 bg-slate-900/60">
        {EQUIP_SLOT_KEYS.map((slot) => (
          <li
            key={slot}
            data-testid={`equip-slot-${slot}`}
            className="flex items-center justify-between px-3 py-2 text-sm"
          >
            <span className="text-slate-500">{SLOT_LABELS[slot]}</span>
            <span className="text-slate-300">{equipSlots[slot] ?? '—'}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
