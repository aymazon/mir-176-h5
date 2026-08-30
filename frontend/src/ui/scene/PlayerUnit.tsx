import { useGameStore } from '../../store/game'
import type { CharacterClass } from '../../store/game'
import { useSceneStore } from '../../store/scene'
import { useAnchor } from './anchorContext'

/**
 * 玩家单位（todo 10，挂载位由 todo 9 BattleScene 提供）：
 *  - 绝对定位 left-4 bottom-3；圆形头像按职业取 emoji（warrior ⚔️ / mage 🔮 / taoist ☯️），
 *    底环色按职业 amber/sky/violet
 *  - 头像上方名字 + Lv 标签；下方 HP/MP 微条（w-16 h-1，红/蓝，transition-[width]）
 *  - HP/MP 数据来自 game store character/maxHp/maxMp（角色状态，非场景瞬态）
 *  - 受击反应：scene store actions 中存在 kind=defend/dodge 期间挂 scene-shake + 红色 vignette
 *  - 施法反应：存在 kind=skill 期间头像外圈挂 scene-glow-pulse
 * 只订阅动作队列做 class 切换，不出队（出队归 ActionLayer onAnimationEnd）。
 */

/** 职业 → 头像 emoji（与建号契约三职业一一对应） */
const CLASS_EMOJI: Record<CharacterClass, string> = {
  warrior: '⚔️',
  mage: '🔮',
  taoist: '☯️',
}

/** 职业 → 头像底环色（ring-2 着色，一眼辨识职业） */
const CLASS_RING: Record<CharacterClass, string> = {
  warrior: 'ring-amber-400',
  mage: 'ring-sky-400',
  taoist: 'ring-violet-400',
}

/** 微条百分比：max 缺省/为 0 时按当前值兜底（首条 stat 事件前 maxHp/maxMp 可能缺省） */
function barPct(cur: number, max: number | undefined): number {
  const cap = max !== undefined && max > 0 ? max : Math.max(cur, 1)
  return Math.min(100, Math.max(0, (cur / cap) * 100))
}

export default function PlayerUnit() {
  const character = useGameStore((s) => s.character)
  const underHit = useSceneStore((s) =>
    s.actions.some((a) => a.kind === 'defend' || a.kind === 'dodge'),
  )
  const casting = useSceneStore((s) =>
    s.actions.some((a) => a.kind === 'skill'),
  )
  const anchorRef = useAnchor('player')

  if (character === null) return null

  return (
    <div
      ref={anchorRef}
      data-testid="player-unit"
      className={`absolute bottom-3 left-4 flex w-16 flex-col items-center ${
        underHit ? 'scene-shake' : ''
      }`}
    >
      {/* 名字 + 等级标签 */}
      <div className="mb-0.5 flex w-full items-baseline justify-center gap-1 text-[10px] leading-tight">
        <span className="max-w-10 truncate text-slate-200">{character.name}</span>
        <span className="font-mono text-amber-300">Lv.{character.level}</span>
      </div>

      {/* 圆形头像：施法时外圈 glow 脉冲；受击时叠红色 vignette */}
      <div
        data-testid="player-avatar"
        className={`relative flex h-14 w-14 items-center justify-center rounded-full bg-slate-800/90 ring-2 ring-offset-1 ring-offset-slate-900 ${
          CLASS_RING[character.class]
        } ${casting ? 'scene-glow-pulse' : ''}`}
      >
        <span className="text-2xl">{CLASS_EMOJI[character.class]}</span>
        {underHit && (
          <div
            data-testid="player-hit-vignette"
            className="pointer-events-none absolute inset-0 rounded-full bg-red-500/30"
          />
        )}
      </div>

      {/* HP/MP 微条 */}
      <div className="mt-1 space-y-0.5">
        <div className="h-1 w-16 overflow-hidden rounded-full bg-slate-800">
          <div
            data-testid="player-hp-bar"
            className="h-full rounded-full bg-red-500 transition-[width]"
            style={{ width: `${barPct(character.hp, character.maxHp)}%` }}
          />
        </div>
        <div className="h-1 w-16 overflow-hidden rounded-full bg-slate-800">
          <div
            data-testid="player-mp-bar"
            className="h-full rounded-full bg-blue-500 transition-[width]"
            style={{ width: `${barPct(character.mp, character.maxMp)}%` }}
          />
        </div>
      </div>
    </div>
  )
}
