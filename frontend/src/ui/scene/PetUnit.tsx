import { useSceneStore } from '../../store/scene'
import type { ScenePet } from '../../store/scene'

/**
 * 宠物单位组（todo 10，挂载位由 todo 9 BattleScene 提供）：
 *  - 紧邻玩家右侧排列：容器 absolute left-20 bottom-3，最多展示 3 只，溢出显 +N 角标
 *  - 小号头像 w-9 h-9，按 PetType 取 emoji：skull 💀 / dog 🐕 / charm 👹（附 💜 紫色光环）
 *  - 头顶 HP 微条（数据来自 scene store 快照，与 game store 无关）
 *  - pet_died / pet_dispersed 后由 1Hz scene 快照整体替换自然移除，本组件不做手动移除
 *  - 宠物普通命中无后端事件，绝不伪造飘字（见计划 Scope 禁令）
 */

/** 同屏最多展示的宠物数，其余折叠进 +N 角标 */
const MAX_VISIBLE = 3

/** PetType（backend/app/sim/pet.py）→ 头像 emoji；未知类型兜底 🐾 */
const PET_EMOJI: Record<string, string> = {
  skull: '💀',
  dog: '🐕',
  charm: '👹',
}

function petEmoji(type: string): string {
  return PET_EMOJI[type] ?? '🐾'
}

function hpPct(pet: ScenePet): number {
  if (pet.maxHp <= 0) return 0
  return Math.min(100, Math.max(0, (pet.hp / pet.maxHp) * 100))
}

function PetAvatar(props: { pet: ScenePet }) {
  const { pet } = props
  return (
    <div
      data-testid={`pet-${pet.id}`}
      className="flex w-9 flex-col items-center"
    >
      {/* 头顶 HP 微条 */}
      <div className="mb-0.5 h-1 w-9 overflow-hidden rounded-full bg-slate-800">
        <div
          data-testid={`pet-hp-${pet.id}`}
          className="h-full rounded-full bg-red-500 transition-[width]"
          style={{ width: `${hpPct(pet)}%` }}
        />
      </div>
      {/* 小号圆形头像；charm 附紫色光环（💜 意象 → drop-shadow 实现，透明 emoji 也可用） */}
      <div
        className={`flex h-9 w-9 items-center justify-center rounded-full bg-slate-800/90 ring-1 ring-slate-600 ${
          pet.type === 'charm'
            ? '[filter:drop-shadow(0_0_6px_rgba(168,85,247,0.9))]'
            : ''
        }`}
      >
        <span className="text-base">{petEmoji(pet.type)}</span>
      </div>
    </div>
  )
}

export default function PetUnit() {
  const pets = useSceneStore((s) => s.pets)
  if (pets.length === 0) return null

  const visible = pets.slice(0, MAX_VISIBLE)
  const overflow = pets.length - visible.length

  return (
    <div
      data-testid="pet-units"
      className="absolute bottom-3 left-20 flex items-end gap-2"
    >
      {visible.map((pet) => (
        <PetAvatar key={pet.id} pet={pet} />
      ))}
      {overflow > 0 && (
        <div
          data-testid="pet-overflow-badge"
          className="flex h-9 min-w-9 items-center justify-center rounded-full border border-slate-600 bg-slate-900/90 px-1 text-[10px] font-mono text-slate-300"
        >
          +{overflow}
        </div>
      )}
    </div>
  )
}
