import { useInventoryStore } from '../store/inventory'

/** 背包 Tab 占位：todo 25 接背包 API 后渲染真实物品格 */
export default function InventoryTab() {
  const items = useInventoryStore((s) => s.items)
  const capacity = useInventoryStore((s) => s.capacity)
  const gold = useInventoryStore((s) => s.gold)

  return (
    <section data-testid="tab-panel-inventory" aria-label="背包">
      <h1 className="mb-2 text-lg font-semibold text-amber-300">背包</h1>
      <p className="mb-3 text-xs text-slate-500">
        格子 {items.length}/{capacity} · 金币 {gold}
      </p>
      <div className="grid grid-cols-5 gap-2">
        {Array.from({ length: capacity }, (_, i) => {
          const item = items[i]
          return (
            <div
              key={i}
              data-testid={`bag-cell-${i}`}
              className="flex aspect-square items-center justify-center rounded border border-slate-800 bg-slate-900/60 text-[10px] text-slate-400"
            >
              {item ? item.name : ''}
            </div>
          )
        })}
      </div>
    </section>
  )
}
