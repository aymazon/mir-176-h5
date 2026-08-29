import { useState } from 'react'
import { usePotionStore, validatePotionPolicy } from '../store/potion'
import type { PotionPolicy } from '../store/potion'
import { apiSavePotionPolicy } from '../api/potion'

/**
 * 吃药策略表单（todo 32，战斗页设置区）：
 *  - HP<X% 喝金创药（常规回血线）/ HP<Y% 喝太阳水（危险线）/
 *    MP<Z% 喝魔法药 / 背包空格<N 自动回城（0 = 关闭）
 *  - 提交前本地校验（validatePotionPolicy：1–99 整数百分比、危险线<回血线、
 *    空格 0–40）；越界（如 150%）拦截不发请求
 *  - 提交走 POST /api/character/policy 存会话；后端端点未就位（todo 26 未含
 *    REST）时降级本地持久化并如实提示
 *  - 策略间优先级固定（HP 危险线 > HP 常规线 > MP > 回城），不做拖拽排序（红线）
 */

interface FieldDef {
  key: keyof PotionPolicy
  label: string
  hint: string
  min: number
  max: number
  testId: string
}

const FIELDS: FieldDef[] = [
  {
    key: 'hpPct',
    label: 'HP 低于',
    hint: '% 喝金创药',
    min: 1,
    max: 99,
    testId: 'policy-hp-pct',
  },
  {
    key: 'urgentHpPct',
    label: 'HP 低于',
    hint: '% 喝太阳水（危险线，须低于回血线）',
    min: 1,
    max: 99,
    testId: 'policy-urgent-hp-pct',
  },
  {
    key: 'mpPct',
    label: 'MP 低于',
    hint: '% 喝魔法药',
    min: 1,
    max: 99,
    testId: 'policy-mp-pct',
  },
  {
    key: 'recallFreeSlots',
    label: '背包空格少于',
    hint: '格自动回城（0 = 关闭）',
    min: 0,
    max: 40,
    testId: 'policy-recall-slots',
  },
]

export default function PotionSettings() {
  const policy = usePotionStore((s) => s.policy)

  const [draft, setDraft] = useState<PotionPolicy>(policy)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function patchField(key: keyof PotionPolicy, raw: string): void {
    // 允许输入过程中的非法中间态（空串按 0 记账，提交时统一校验拦截）
    const v = raw === '' ? 0 : Number(raw)
    setDraft((d) => ({ ...d, [key]: Number.isFinite(v) ? v : 0 }))
    setError(null)
  }

  async function submit(): Promise<void> {
    if (busy) return
    const invalid = validatePotionPolicy(draft)
    if (invalid !== null) {
      setError(invalid)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const out = await apiSavePotionPolicy(draft)
      setNotice(
        out.persisted === 'server'
          ? '吃药策略已保存'
          : '吃药策略已保存到本地（后端会话端点待补）',
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      data-testid="potion-form"
      aria-label="吃药策略"
      className="space-y-2.5"
      // noValidate：自定义校验（validatePotionPolicy）输出人话文案，
      // 原生 max/min 的静默拦截会吞掉 submit 事件（jsdom 实测同款行为）
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      {FIELDS.map((f) => (
        <label
          key={f.key}
          className="flex items-center justify-between gap-2 text-xs text-slate-300"
        >
          <span className="shrink-0">
            {f.label}
            <span className="ml-1 text-[10px] text-slate-500">{f.hint}</span>
          </span>
          <input
            type="number"
            inputMode="numeric"
            min={f.min}
            max={f.max}
            data-testid={f.testId}
            value={draft[f.key]}
            onChange={(e) => patchField(f.key, e.target.value)}
            className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right font-mono text-xs text-slate-100 focus:border-amber-400/60 focus:outline-none"
          />
        </label>
      ))}

      {error !== null && (
        <p data-testid="policy-error" role="alert" className="text-xs text-rose-400">
          {error}
        </p>
      )}
      {notice !== null && (
        <p data-testid="policy-saved" role="status" className="text-xs text-emerald-400">
          {notice}
        </p>
      )}

      <button
        type="submit"
        data-testid="policy-submit"
        disabled={busy}
        className="w-full rounded bg-amber-600 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
      >
        保存策略
      </button>
    </form>
  )
}
