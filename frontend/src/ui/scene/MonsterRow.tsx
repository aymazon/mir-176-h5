import { useSceneStore } from '../../store/scene'
import MonsterUnit from './MonsterUnit'

/**
 * 怪物横排（todo 11）：
 *  - 绝对定位贴场景右缘（right-3 + inset-y-6），row-reverse 使首怪（玩家目标）贴右缘
 *  - wrap + content-center + items-end：多行时纵向居中、底部对齐
 *  - 可见上限 8 只（store 顺序即快照顺序，alive 在前 dying 在后）；超出折叠为「+N 只」chip
 *  - 无 x/y 坐标定位、无 onClick（横排即布局方案；MVP 无手动选目标）
 */

const VISIBLE_CAP = 8

export default function MonsterRow() {
  const monsters = useSceneStore((s) => s.monsters)
  const visible = monsters.slice(0, VISIBLE_CAP)
  const overflow = monsters.length - visible.length

  return (
    <div className="absolute inset-y-6 right-3 flex flex-row-reverse flex-wrap content-center items-end justify-start gap-2">
      {visible.map((m) => (
        <MonsterUnit key={m.id} monster={m} />
      ))}
      {overflow > 0 && (
        <span
          data-testid="scene-monster-overflow"
          className="rounded bg-slate-800/80 px-1.5 py-0.5 text-[10px] text-slate-300"
        >
          +{overflow} 只
        </span>
      )}
    </div>
  )
}
