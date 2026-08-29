import { useEffect, useState } from 'react'
import { useSkillStore } from '../store/skill'
import { apiLearnSkill, refreshSkills } from '../api/skill'

/**
 * 技能 Tab（todo 32，接 todo 28 后端 /api/skill/*）：
 *  - 已学技能：等级徽章 + 熟练经验进度条（exp/next_level_exp；满级显示「已满级」）
 *  - 可学清单：职业匹配且未学；学习按钮走 POST /api/skill/learn
 *  - ≥35 级技能书技能（requires_book）仅展示「需技能书」，不提供按钮
 *    （技能书在背包 Tab 使用入口属 learn_from_book 范畴，本页不重复造轮子）
 */

function ExpBar(props: { exp: number; next: number; testId: string }) {
  const maxed = props.next <= 0
  const pct = maxed ? 100 : Math.min(100, (props.exp / props.next) * 100)
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between text-[10px]">
        <span className="text-slate-500">熟练度</span>
        <span data-testid={props.testId} className="font-mono text-slate-400">
          {maxed ? '已满级' : `${props.exp}/${props.next}`}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            maxed ? 'bg-amber-400' : 'bg-emerald-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function SkillTab() {
  const learned = useSkillStore((s) => s.learned)
  const available = useSkillStore((s) => s.available)

  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void refreshSkills()
  }, [])

  // toast 自动消退（InventoryTab 同款）
  useEffect(() => {
    if (notice === null) return
    const timer = window.setTimeout(() => setNotice(null), 2500)
    return () => window.clearTimeout(timer)
  }, [notice])

  async function learn(skillId: string): Promise<void> {
    if (busy) return
    setBusy(true)
    try {
      const out = await apiLearnSkill(skillId)
      await refreshSkills()
      setNotice(`已学会 ${out.name}`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '学习失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section data-testid="tab-panel-skill" aria-label="技能" className="space-y-3">
      {notice !== null && (
        <div
          data-testid="toast"
          role="status"
          className="fixed inset-x-0 top-12 z-50 mx-auto w-fit max-w-[440px] rounded-full border border-amber-400/50 bg-slate-900/95 px-4 py-1.5 text-xs text-amber-200 shadow-lg"
        >
          {notice}
        </div>
      )}

      <h1 className="text-lg font-semibold text-amber-300">技能</h1>

      {/* --- 已学技能 --- */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <h2 className="mb-2 text-xs font-semibold text-slate-400">已学技能</h2>
        {learned.length === 0 ? (
          <p data-testid="skill-learned-empty" className="text-xs text-slate-600">
            （尚未学会任何技能，到级后可在下方学习）
          </p>
        ) : (
          <ul data-testid="skill-learned-list" className="space-y-2.5">
            {learned.map((s) => (
              <li key={s.skill_id} data-testid={`skill-learned-${s.skill_id}`}>
                <div className="mb-1 flex items-baseline justify-between">
                  <span className="text-sm text-slate-200">{s.name}</span>
                  <span
                    data-testid={`skill-level-${s.skill_id}`}
                    className={`text-[10px] font-semibold ${
                      s.level >= s.max_level ? 'text-amber-300' : 'text-emerald-400'
                    }`}
                  >
                    Lv.{s.level}/{s.max_level}
                  </span>
                </div>
                <ExpBar
                  exp={s.exp}
                  next={s.next_level_exp}
                  testId={`skill-exp-${s.skill_id}`}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* --- 可学技能 --- */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <h2 className="mb-2 text-xs font-semibold text-slate-400">可学技能</h2>
        {available.length === 0 ? (
          <p data-testid="skill-available-empty" className="text-xs text-slate-600">
            （暂无可学技能）
          </p>
        ) : (
          <ul data-testid="skill-available-list" className="space-y-2">
            {available.map((s) => (
              <li
                key={s.skill_id}
                data-testid={`skill-available-${s.skill_id}`}
                className="flex items-center justify-between gap-2"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm text-slate-200">{s.name}</div>
                  <div className="text-[10px] text-slate-500">
                    Lv.{s.learn_level} 可学 · 耗蓝 {s.mp_cost}
                  </div>
                </div>
                {s.requires_book ? (
                  <span
                    data-testid={`skill-book-${s.skill_id}`}
                    className="shrink-0 rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-500"
                  >
                    需技能书
                  </span>
                ) : (
                  <button
                    type="button"
                    data-testid={`skill-learn-${s.skill_id}`}
                    disabled={busy}
                    onClick={() => void learn(s.skill_id)}
                    className="shrink-0 rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                  >
                    学习
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
