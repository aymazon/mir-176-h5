import { useState, type AnimationEvent } from 'react'
import type { SceneMonster } from '../../store/scene'
import { useSceneStore } from '../../store/scene'
import { monsterVisualOf, type MonsterVisual } from '../../data/monster-visuals'
import { useAnchor } from './anchorContext'

/**
 * 场景怪物单元（todo 11）：
 *  - 竖列布局：HP 条（顶）→ emoji 身体 → 名字标签
 *  - tier 取「视觉表 tier 与 payload.boss」的较高档（payload.boss=true 强制 boss 呈现）
 *  - isTarget → 外层琥珀 ring + scale-105 + 「目标」小标签（玩家目标 = alive_monsters[0]）
 *  - 入场：挂载即挂 scene-spawn-pop（store 不发 spawn 动作，新 id 必然是新挂载）
 *  - 死亡：dyingUntil 置位 → scene-fade-sink；animationend → sweepDying() 快路径清扫
 *    （兜底路径：下一次 applyScene 快照丢弃过期 dying 条目，reduced-motion 下也成立）
 *  - 无 onClick：MVP 不支持手动选目标
 */

type Tier = MonsterVisual['tier']

/** tier 升档：payload.boss 或视觉表 boss 均为 boss；否则用视觉表 tier */
function effectiveTier(visual: MonsterVisual, payloadBoss: boolean): Tier {
  if (payloadBoss || visual.tier === 'boss') return 'boss'
  return visual.tier
}

const BODY_CLASS: Record<Tier, string> = {
  normal: 'text-4xl',
  elite: 'text-4xl rounded ring-2 ring-purple-400',
  boss: 'rounded-full text-6xl ring-2 ring-amber-400',
}

export default function MonsterUnit({ monster }: { monster: SceneMonster }) {
  const sweepDying = useSceneStore((s) => s.sweepDying)
  const [entering, setEntering] = useState(true)
  const dying = monster.dyingUntil !== undefined
  const anchorRef = useAnchor(monster.id)

  const visual = monsterVisualOf(monster.monsterId)
  const tier = effectiveTier(visual, monster.boss)
  const hpPct = Math.min(100, (monster.hp / Math.max(monster.maxHp, 1)) * 100)

  const onAnimationEnd = (e: AnimationEvent<HTMLDivElement>) => {
    if (e.currentTarget !== e.target) return // 子元素动画不触发清扫
    if (dying) {
      sweepDying() // 快路径：死亡动画播完立即清扫（仅丢弃 dyingUntil < now 的条目）
    } else if (entering) {
      setEntering(false) // 入场动画播完摘掉 class，避免与后续 dying 动画互斥
    }
  }

  return (
    <div
      ref={anchorRef}
      data-testid={`scene-monster-${monster.id}`}
      className={[
        'relative flex flex-col items-center gap-0.5',
        dying ? 'scene-fade-sink' : entering ? 'scene-spawn-pop' : '',
        monster.isTarget ? 'scale-105 rounded ring-2 ring-amber-300' : '',
      ].join(' ')}
      onAnimationEnd={onAnimationEnd}
    >
      {monster.isTarget && (
        <span className="text-[9px] leading-none text-amber-300">目标</span>
      )}
      {/* HP 条：宽 = hp/maxHp 百分比，500ms 宽度过渡 */}
      <div className="h-1 w-10 overflow-hidden rounded bg-slate-800">
        <div
          className="h-full bg-gradient-to-r from-rose-600 to-rose-400 transition-[width] duration-500"
          style={{ width: `${hpPct}%` }}
        />
      </div>
      {/* emoji 身体：tier 决定尺寸与环色；boss 加 👑 冠标 */}
      <span
        className={`relative inline-block leading-none ${visual.tint} ${BODY_CLASS[tier]}`}
      >
        {visual.emoji}
        {tier === 'boss' && (
          <span className="absolute -top-2 left-1/2 -translate-x-1/2 text-xs">
            👑
          </span>
        )}
      </span>
      <span
        className={`text-[10px] ${tier === 'boss' ? 'text-amber-300' : 'text-slate-300'}`}
      >
        {monster.name}
      </span>
    </div>
  )
}
