import { useEffect, useRef, useState } from 'react'
import { useLogStore } from '../store/log'
import { useGameStore } from '../store/game'
import { EXP_TO_NEXT } from '../data/gamedata'
import PotionSettings from './PotionSettings'
import BattleScene from './scene/BattleScene'

/**
 * 战斗 Tab（todo 31）：
 *  - 人物 HP/MP/经验条 ← SSE stat 事件（game store）
 *  - 战斗场景（BattleScene）← 地图背景 + 单位 + 动作层
 *  - 日志流 ← log store（含 gap 分隔行）；滚到底自动滚动，上滑暂停
 *  - 设置区（todo 32）：吃药策略表单（PotionSettings）
 */

function StatBar(props: {
  label: string
  value: number
  max: number
  barClass: string
  testId: string
  suffix?: string
}) {
  const pct = props.max > 0 ? Math.min(100, (props.value / props.max) * 100) : 0
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between text-[10px]">
        <span className="text-slate-500">{props.label}</span>
        <span data-testid={props.testId} className="font-mono text-slate-300">
          {Math.round(props.value)}
          {props.suffix ?? `/${Math.round(props.max)}`}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          data-testid={`${props.testId}-bar`}
          className={`h-full rounded-full transition-[width] duration-500 ${props.barClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function BattleTab() {
  const lines = useLogStore((s) => s.lines)
  const character = useGameStore((s) => s.character)

  const logBoxRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)
  const [autoScroll, setAutoScroll] = useState(true)

  // 新日志到达且未暂停 → 贴底；用户上滑（离底 >32px）暂停自动滚动
  useEffect(() => {
    const el = logBoxRef.current
    if (el && autoScrollRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [lines])

  function handleScroll(): void {
    const el = logBoxRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32
    if (atBottom !== autoScrollRef.current) {
      autoScrollRef.current = atBottom
      setAutoScroll(atBottom)
    }
  }

  function resumeAutoScroll(): void {
    autoScrollRef.current = true
    setAutoScroll(true)
    const el = logBoxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }

  const hpMax = 24 // stat.max_hp 缺省时的占位上限（首条 stat 到达前）
  const hp = character?.hp ?? 0
  const mp = character?.mp ?? 0
  const exp = character?.exp ?? 0
  const level = character?.level ?? 1
  const expNext = EXP_TO_NEXT[level] ?? 0

  return (
    <section data-testid="tab-panel-battle" aria-label="战斗" className="space-y-3">
      {/* --- 人物状态 --- */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-sm font-semibold text-amber-300">
            {character ? character.name : '未登录'}
            <span className="ml-1 text-xs text-slate-400">Lv.{level}</span>
          </span>
          <span data-testid="stat-gold" className="text-xs text-amber-200/90">
            {character ? `${character.gold} 金` : '—'}
          </span>
        </div>
        <div className="space-y-1.5">
          <StatBar
            label="HP"
            value={hp}
            max={typeof character?.maxHp === 'number' ? character.maxHp : hpMax}
            barClass="bg-red-500"
            testId="stat-hp"
          />
          <StatBar
            label="MP"
            value={mp}
            max={typeof character?.maxMp === 'number' ? character.maxMp : 10}
            barClass="bg-sky-500"
            testId="stat-mp"
          />
          <StatBar
            label={`EXP（Lv.${level}${expNext > 0 ? ` → ${expNext}` : ' 满级'}）`}
            value={exp}
            max={expNext > 0 ? expNext : Math.max(exp, 1)}
            barClass="bg-amber-400"
            testId="stat-exp"
            suffix={expNext > 0 ? `/${expNext}` : ''}
          />
        </div>
      </div>

      {/* --- 战斗场景（BattleScene）--- */}
      <BattleScene />

      {/* --- 设置区：吃药策略（todo 32） --- */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <h2 className="mb-2 text-xs font-semibold text-slate-400">吃药策略</h2>
        <PotionSettings />
      </div>

      {/* --- 日志流 --- */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-xs font-semibold text-slate-400">战斗日志</h2>
          {!autoScroll && (
            <button
              type="button"
              data-testid="log-resume"
              onClick={resumeAutoScroll}
              className="rounded bg-slate-800 px-2 py-0.5 text-[10px] text-amber-300 hover:bg-slate-700"
            >
              回到底部 ↓
            </button>
          )}
        </div>
        <div
          ref={logBoxRef}
          onScroll={handleScroll}
          data-testid="log-scroll-box"
          className="max-h-[280px] space-y-1 overflow-y-auto rounded-lg border border-slate-800 bg-slate-900/60 p-3 font-mono text-xs text-slate-300"
        >
          <ul data-testid="log-list" className="space-y-1">
            {lines.length === 0 ? (
              <li data-testid="log-empty" className="text-slate-600">
                （暂无日志，等待事件流…）
              </li>
            ) : (
              lines.map((line) =>
                line.kind === 'gap' ? (
                  <li
                    key={line.id}
                    data-testid="log-gap"
                    className="my-1 border-y border-dashed border-amber-500/40 py-1 text-center text-amber-400/80"
                  >
                    —— {line.text} ——
                  </li>
                ) : (
                  <li
                    key={line.id}
                    data-testid={`log-line-${line.id}`}
                    className="truncate"
                  >
                    <span className="text-slate-600">[#{line.id}]</span> {line.text}
                  </li>
                ),
              )
            )}
          </ul>
        </div>
      </div>
    </section>
  )
}
