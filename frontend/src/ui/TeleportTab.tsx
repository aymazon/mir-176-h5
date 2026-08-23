/** 传送 Tab 占位：todo 30 传送员付费传送 + 跑图倒计时就绪后接入 */
export default function TeleportTab() {
  return (
    <section data-testid="tab-panel-teleport" aria-label="传送">
      <h1 className="mb-2 text-lg font-semibold text-amber-300">传送</h1>
      <p className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-500">
        传送面板占位：指定层付费传送 / 回城卷（todo 30 接入）。
      </p>
    </section>
  )
}
