import { useLogStore } from '../store/log'

/** 战斗 Tab：日志流渲染（SSE 事件经 log store 落地），todo 31 接真实战斗面板 */
export default function BattleTab() {
  const lines = useLogStore((s) => s.lines)

  return (
    <section data-testid="tab-panel-battle" aria-label="战斗">
      <h1 className="mb-2 text-lg font-semibold text-amber-300">战斗</h1>
      <p className="mb-3 text-xs text-slate-500">
        自动战斗日志流（SSE 实时推送，断线自动重连补日志）
      </p>
      <ul
        data-testid="log-list"
        className="space-y-1 rounded-lg border border-slate-800 bg-slate-900/60 p-3 font-mono text-xs text-slate-300"
      >
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
              <li key={line.id} data-testid={`log-line-${line.id}`} className="truncate">
                <span className="text-slate-600">[#{line.id}]</span> {line.text}
              </li>
            ),
          )
        )}
      </ul>
    </section>
  )
}
