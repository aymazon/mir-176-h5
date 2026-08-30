import { useGameStore, selectCurrentMap } from '../../store/game'
import { useSceneStore } from '../../store/scene'
import { mapThemeOf } from '../../data/map-themes'

/**
 * 战斗场景容器（todo 9，骨架）：
 *  - 全幅渐变背景按当前地图 region 取主题（mapThemeOf 兜底 default），
 *    装饰 emoji 组低透明点缀，pointer-events-none 不挡后续单位层交互
 *  - 左上角标题栏：地图名（未知地图降级显示原始 mapId）+ 等级区间 + 交战怪数
 *  - 右上角为空预留槽位（后续 todo 扩展，勿加冲突样式）
 * 单位渲染（PlayerUnit/PetUnit/MonsterRow）与动作层（ActionLayer）由 todo 10-12 挂载。
 */
export default function BattleScene() {
  const mapId = useSceneStore((s) => s.mapId)
  const monsters = useSceneStore((s) => s.monsters)
  const currentMap = useGameStore(selectCurrentMap)
  const charMapId = useGameStore((s) => s.character?.mapId)

  const theme = mapThemeOf(currentMap?.region)
  const mapName = currentMap?.name ?? mapId ?? charMapId ?? '未知区域'

  return (
    <div
      data-testid="battle-scene"
      className="relative h-64 overflow-hidden rounded-lg border border-slate-800"
    >
      {/* 背景层：区域主题渐变全幅覆盖 */}
      <div
        data-testid="scene-bg"
        className={`absolute inset-0 bg-gradient-to-b ${theme.bgClass}`}
      />

      {/* 装饰层：区域主题 emoji 组，低透明、不响应指针 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 flex items-end justify-around pb-2 opacity-20"
      >
        {theme.decor.map((emoji, i) => (
          <span key={`${emoji}-${i}`} className="text-4xl">
            {emoji}
          </span>
        ))}
      </div>

      {/* 标题栏（左上）：地图名 + 等级区间 + 交战怪数 */}
      <div className="absolute left-2 top-2 flex items-center gap-2 rounded bg-slate-950/60 px-2 py-1 text-xs">
        <span
          data-testid="scene-map-name"
          className={`font-semibold ${theme.accentClass}`}
        >
          {mapName}
        </span>
        <span className="font-mono text-slate-400">
          Lv.{currentMap?.levelMin ?? '?'}–{currentMap?.levelMax ?? '?'}
        </span>
        <span data-testid="scene-monster-count" className="text-rose-300">
          {monsters.length > 0 ? `怪物 ×${monsters.length}` : '暂无交战目标'}
        </span>
      </div>

      {/* 右上角预留槽位（空占位，后续 todo 挂载） */}
      <div data-testid="scene-corner-slot" className="absolute right-2 top-2" />
    </div>
  )
}
