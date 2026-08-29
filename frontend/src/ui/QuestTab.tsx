import { useEffect, useState } from 'react'
import { useQuestStore, progressActionFor } from '../store/quest'
import type { QuestEntry } from '../store/quest'
import {
  apiQuestAccept,
  apiQuestComplete,
  apiQuestProgress,
  refreshQuests,
} from '../api/quest'
import { itemEntry } from '../data/gamedata'

/**
 * 任务 Tab（todo 32，接 todo 29 后端 /api/quest/*）：
 *  - 四状态分组：进行（进度条 + 推进按钮）/ 可接（接取按钮）/
 *    完成待交付（交付按钮发奖励）/ 已交付（终态只读）
 *  - 推进按钮 action 由 kind + arena_map_id 推导（progressActionFor）：
 *    kill→kill、collect→collect（缴纳扣背包）、dialogue/profession→dialog、
 *    boss_instance→boss（需在竞技场图内，否则后端 422 透传 toast）
 *  - 全部操作成功后重拉 /list 对齐后端状态机（前端不推演状态迁移）
 */

const KIND_LABELS: Record<string, string> = {
  kill: '击杀',
  collect: '收集',
  dialogue: '对话',
  profession: '引导',
}

function rewardText(q: QuestEntry): string {
  const parts: string[] = []
  if (q.reward_gold > 0) parts.push(`${q.reward_gold} 金`)
  if (q.reward_exp > 0) parts.push(`${q.reward_exp} 经验`)
  for (const id of q.reward_items) {
    parts.push(itemEntry(id)?.name ?? id)
  }
  return parts.length > 0 ? parts.join(' · ') : '无'
}

function ProgressBar(props: { count: number; need: number; testId: string }) {
  const pct = props.need > 0 ? Math.min(100, (props.count / props.need) * 100) : 0
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between text-[10px]">
        <span className="text-slate-500">进度</span>
        <span data-testid={props.testId} className="font-mono text-slate-400">
          {props.count}/{props.need}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-sky-500 transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function QuestGroup(props: {
  title: string
  testId: string
  emptyText: string
  quests: QuestEntry[]
  children: (q: QuestEntry) => React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
      <h2 className="mb-2 text-xs font-semibold text-slate-400">
        {props.title}
        <span className="ml-1 font-mono text-slate-600">×{props.quests.length}</span>
      </h2>
      {props.quests.length === 0 ? (
        <p data-testid={`${props.testId}-empty`} className="text-xs text-slate-600">
          {props.emptyText}
        </p>
      ) : (
        <ul data-testid={props.testId} className="space-y-3">
          {props.quests.map((q) => (
            <li key={q.quest_id} data-testid={`quest-${q.quest_id}`}>
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-sm text-slate-200">
                  {q.name}
                </span>
                <span className="shrink-0 text-[10px] text-slate-500">
                  {KIND_LABELS[q.kind] ?? q.kind}
                  {q.arena_map_id !== null && ' · 竞技场'}
                  {q.required_class !== null && ` · 限${q.required_class}`}
                  {` · Lv.${q.level_req}+`}
                </span>
              </div>
              <div className="mb-1.5 text-[10px] text-slate-500">
                奖励：{rewardText(q)}
              </div>
              {props.children(q)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function QuestTab() {
  const notStarted = useQuestStore((s) => s.notStarted)
  const inProgress = useQuestStore((s) => s.inProgress)
  const completed = useQuestStore((s) => s.completed)
  const delivered = useQuestStore((s) => s.delivered)

  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void refreshQuests()
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
      const message = await action()
      await refreshQuests()
      setNotice(message)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const accept = (q: QuestEntry) =>
    run(async () => {
      const out = await apiQuestAccept(q.quest_id)
      return out.message || `接受任务【${q.name}】`
    })

  const progress = (q: QuestEntry) =>
    run(async () => {
      const out = await apiQuestProgress(q.quest_id, progressActionFor(q))
      return out.message || `${q.name} 进度 ${out.count}/${out.need}`
    })

  const complete = (q: QuestEntry) =>
    run(async () => {
      const out = await apiQuestComplete(q.quest_id)
      return `交付【${out.name}】：金币+${out.rewards.gold} 经验+${out.rewards.exp}`
    })

  return (
    <section data-testid="tab-panel-quest" aria-label="任务" className="space-y-3">
      {notice !== null && (
        <div
          data-testid="toast"
          role="status"
          className="fixed inset-x-0 top-12 z-50 mx-auto w-fit max-w-[440px] rounded-full border border-amber-400/50 bg-slate-900/95 px-4 py-1.5 text-xs text-amber-200 shadow-lg"
        >
          {notice}
        </div>
      )}

      <h1 className="text-lg font-semibold text-amber-300">任务</h1>

      <QuestGroup
        title="进行中"
        testId="quest-in-progress"
        emptyText="（没有进行中的任务）"
        quests={inProgress}
      >
        {(q) => (
          <div className="space-y-1.5">
            <ProgressBar
              count={q.count}
              need={q.need}
              testId={`quest-progress-${q.quest_id}`}
            />
            <button
              type="button"
              data-testid={`quest-advance-${q.quest_id}`}
              disabled={busy}
              onClick={() => void progress(q)}
              className="rounded bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {progressActionFor(q) === 'collect'
                ? '缴纳物品'
                : progressActionFor(q) === 'dialog'
                  ? '对话推进'
                  : progressActionFor(q) === 'boss'
                    ? '挑战分身'
                    : '上报击杀'}
            </button>
          </div>
        )}
      </QuestGroup>

      <QuestGroup
        title="可接取"
        testId="quest-not-started"
        emptyText="（没有可接的任务）"
        quests={notStarted}
      >
        {(q) => (
          <button
            type="button"
            data-testid={`quest-accept-${q.quest_id}`}
            disabled={busy}
            onClick={() => void accept(q)}
            className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            接取
          </button>
        )}
      </QuestGroup>

      <QuestGroup
        title="完成待交付"
        testId="quest-completed"
        emptyText="（没有待交付的任务）"
        quests={completed}
      >
        {(q) => (
          <button
            type="button"
            data-testid={`quest-complete-${q.quest_id}`}
            disabled={busy}
            onClick={() => void complete(q)}
            className="rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
          >
            交付领奖
          </button>
        )}
      </QuestGroup>

      <QuestGroup
        title="已交付"
        testId="quest-delivered"
        emptyText="（还没有交付过任务）"
        quests={delivered}
      >
        {(q) => (
          <span
            data-testid={`quest-done-${q.quest_id}`}
            className="text-[10px] text-slate-600"
          >
            已完成 {q.count}/{q.need}
          </span>
        )}
      </QuestGroup>
    </section>
  )
}
