import { create } from 'zustand'

/**
 * 战斗场景 store（todo 6）：场景实例表 + 动作动画队列。
 *  - 实例表由 SSE scene 快照（1Hz）整体替换驱动；消失怪标记 dyingUntil 滞留播死亡动画
 *  - 动作队列由 SSE log 事件驱动，组件层 onAnimationEnd 后 removeAction 出队
 * 纯逻辑 store：内部不含任何 setInterval/setTimeout（reduced-motion 下动画事件不触发，
 * dying 清理由下一次 applyScene 快照驱动兜底，动画结束回调走 sweepDying 快路径）。
 */

/** 场景怪物实例：同名怪按 id 多实例共存（取代 game store 按名合并的 MonsterBar） */
export interface SceneMonster {
  id: string
  monsterId: string
  name: string
  hp: number
  maxHp: number
  boss: boolean
  isTarget: boolean
  /** 死亡滞留截止时刻（ms epoch）；undefined = 存活 */
  dyingUntil?: number
}

export interface ScenePet {
  id: string
  type: string
  name: string
  hp: number
  maxHp: number
}

/** 进入动画队列的 9 种日志 kind；其余 kind 忽略 */
export type SceneActionKind =
  | 'attack'
  | 'miss'
  | 'kill'
  | 'defend'
  | 'dodge'
  | 'skill'
  | 'potion'
  | 'pet_miss'
  | 'pet_kill'

export interface SceneAction {
  /** 单调递增计数器（模块级，会话内不回退，可直接作 React key） */
  id: string
  kind: SceneActionKind
  targetId?: string
  dmg?: number
  skillId?: string
  exp?: number
  hpRestore?: number
  mpRestore?: number
}

/** scene 快照 payload（SSE scene 事件契约，snake_case 直传） */
export interface SceneSnapshotPayload {
  map_id: string
  monsters: Array<{
    id: string
    monster_id: string
    name: string
    hp: number
    max_hp: number
    boss: boolean
    is_target: boolean
  }>
  pets: Array<{
    id: string
    type: string
    name: string
    hp: number
    max_hp: number
  }>
}

/** pushLogAction 接受的原始日志 payload（log 事件 data，snake_case 直传） */
export interface SceneLogPayload {
  kind: string
  target_id?: string
  dmg?: number
  skill_id?: string
  exp?: number
  hp_restore?: number
  mp_restore?: number
}

/** 动作队列容量：超出丢弃最旧（重连重放风暴保护） */
export const SCENE_ACTION_CAP = 24
/** 消失怪死亡动画滞留时长（ms） */
export const DYING_LINGER_MS = 1200

const KNOWN_KINDS: ReadonlySet<string> = new Set<SceneActionKind>([
  'attack',
  'miss',
  'kill',
  'defend',
  'dodge',
  'skill',
  'potion',
  'pet_miss',
  'pet_kill',
])

/** 模块级单调计数器：不随 resetScene 回退，保证队列 id 会话内唯一 */
let actionSeq = 0

interface SceneStore {
  mapId: string | null
  monsters: SceneMonster[]
  pets: ScenePet[]
  actions: SceneAction[]
  /**
   * scene 快照整体替换：
   *  1. 先扫尾丢弃 dyingUntil < now 的滞留条目（不依赖 animationend）
   *  2. monsters/pets/mapId 全量替换；消失怪标记 dyingUntil = now + 1200 滞留
   * 新出现的 id 由组件挂载时自加 scene-spawn-pop class，store 不发 spawn 动作
   */
  applyScene: (payload: SceneSnapshotPayload) => void
  /**
   * log 事件 → 动画动作入队：
   *  - 未知 kind 忽略；id 取模块级单调计数器
   *  - 同帧去重：队尾同 kind + 同 targetId → 合并（数值字段取最新），防重放突发
   *  - 容量 24，超出丢弃最旧
   * 动作无 TTL：正常清理由组件 onAnimationEnd → removeAction；
   * reduced-motion 下由 todo 12 改用 rAF 触发同一回调
   */
  pushLogAction: (payload: SceneLogPayload) => void
  /** 动画结束回调出队（onAnimationEnd / rAF  exemption 共用） */
  removeAction: (id: string) => void
  /** 显式清扫过期 dying 怪（动画结束回调驱动的快路径） */
  sweepDying: () => void
  /** 清空全部（zone_change / gap / 401 clear-auth 调用） */
  resetScene: () => void
}

export const useSceneStore = create<SceneStore>((set) => ({
  mapId: null,
  monsters: [],
  pets: [],
  actions: [],

  applyScene: (payload) =>
    set((state) => {
      const now = Date.now()
      // 1) 扫尾：丢弃已过期的 dying 条目（dyingUntil < now；等于此刻仍保留一帧）
      const swept = state.monsters.filter(
        (m) => m.dyingUntil === undefined || m.dyingUntil >= now,
      )
      // 2) 消失怪 → dying 滞留；已 dying 的保留原截止时刻不续期
      const incomingIds = new Set(payload.monsters.map((m) => m.id))
      const dying: SceneMonster[] = []
      for (const m of swept) {
        if (incomingIds.has(m.id)) continue
        dying.push(
          m.dyingUntil === undefined
            ? { ...m, dyingUntil: now + DYING_LINGER_MS }
            : m,
        )
      }
      const monsters: SceneMonster[] = payload.monsters.map((m) => ({
        id: m.id,
        monsterId: m.monster_id,
        name: m.name,
        hp: m.hp,
        maxHp: m.max_hp,
        boss: m.boss,
        isTarget: m.is_target,
      }))
      const pets: ScenePet[] = payload.pets.map((p) => ({
        id: p.id,
        type: p.type,
        name: p.name,
        hp: p.hp,
        maxHp: p.max_hp,
      }))
      return {
        mapId: payload.map_id,
        monsters: [...monsters, ...dying],
        pets,
      }
    }),

  pushLogAction: (payload) =>
    set((state) => {
      if (!KNOWN_KINDS.has(payload.kind)) return state
      const kind = payload.kind as SceneActionKind
      const incoming = compactUndefined({
        targetId: payload.target_id,
        dmg: payload.dmg,
        skillId: payload.skill_id,
        exp: payload.exp,
        hpRestore: payload.hp_restore,
        mpRestore: payload.mp_restore,
      })
      // 同帧去重：队尾同 kind + 同 targetId → 合并，数值字段取最新值
      const tail = state.actions[state.actions.length - 1]
      if (
        tail !== undefined &&
        tail.kind === kind &&
        tail.targetId === incoming.targetId
      ) {
        const merged: SceneAction = { ...tail, ...incoming, id: tail.id }
        const actions = state.actions.slice()
        actions[actions.length - 1] = merged
        return { actions }
      }
      const action: SceneAction = { id: `act-${++actionSeq}`, kind, ...incoming }
      return { actions: capQueue([...state.actions, action]) }
    }),

  removeAction: (id) =>
    set((state) => {
      if (!state.actions.some((a) => a.id === id)) return state
      return { actions: state.actions.filter((a) => a.id !== id) }
    }),

  sweepDying: () =>
    set((state) => {
      const now = Date.now()
      if (
        !state.monsters.some(
          (m) => m.dyingUntil !== undefined && m.dyingUntil < now,
        )
      ) {
        return state
      }
      return {
        monsters: state.monsters.filter(
          (m) => m.dyingUntil === undefined || m.dyingUntil >= now,
        ),
      }
    }),

  resetScene: () => set({ mapId: null, monsters: [], pets: [], actions: [] }),
}))

/** 丢弃 undefined 字段：合并语义下「未携带 = 保留旧值」，携带值 = 取最新 */
function compactUndefined<T extends Record<string, unknown>>(obj: T): Partial<T> {
  const out: Partial<T> = {}
  for (const key of Object.keys(obj) as Array<keyof T>) {
    const v = obj[key]
    if (v !== undefined) out[key] = v
  }
  return out
}

/** 容量截断：保留末尾 N 条（最新），丢弃最旧 */
function capQueue(actions: SceneAction[]): SceneAction[] {
  return actions.length > SCENE_ACTION_CAP
    ? actions.slice(-SCENE_ACTION_CAP)
    : actions
}
