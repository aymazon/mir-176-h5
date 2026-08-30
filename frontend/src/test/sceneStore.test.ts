import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  useSceneStore,
  SCENE_ACTION_CAP,
  DYING_LINGER_MS,
} from '../store/scene'
import type { SceneSnapshotPayload } from '../store/scene'

const T0 = 1_000_000

const mkMonster = (
  id: string,
  name = `怪-${id}`,
  hp = 100,
): SceneSnapshotPayload['monsters'][number] => ({
  id,
  monster_id: `mid-${id}`,
  name,
  hp,
  max_hp: 100,
  boss: false,
  is_target: false,
})

const mkPet = (id: string): SceneSnapshotPayload['pets'][number] => ({
  id,
  type: 'dog',
  name: `宠-${id}`,
  hp: 50,
  max_hp: 50,
})

const mkScene = (
  monsters: SceneSnapshotPayload['monsters'],
  pets: SceneSnapshotPayload['pets'] = [],
  mapId = 'wild',
): SceneSnapshotPayload => ({ map_id: mapId, monsters, pets })

describe('sceneStore', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(T0)
    useSceneStore.getState().resetScene()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('applyScene', () => {
    it('全量替换 monsters/pets/mapId（snake_case → camelCase 投影）', () => {
      useSceneStore
        .getState()
        .applyScene(mkScene([mkMonster('m1'), mkMonster('m2')], [mkPet('p1')]))
      let s = useSceneStore.getState()
      expect(s.mapId).toBe('wild')
      expect(s.monsters.map((m) => m.id)).toEqual(['m1', 'm2'])
      expect(s.monsters[0]).toMatchObject({
        monsterId: 'mid-m1',
        maxHp: 100,
        boss: false,
        isTarget: false,
      })
      expect(s.monsters[0].dyingUntil).toBeUndefined()
      expect(s.pets).toHaveLength(1)

      // 第二次快照：存活集整体替换；消失的 m1/m2 标记 dying 滞留（死亡动画）
      useSceneStore
        .getState()
        .applyScene(mkScene([mkMonster('m3')], [], 'mine'))
      s = useSceneStore.getState()
      expect(s.mapId).toBe('mine')
      expect(s.monsters.map((m) => m.id)).toEqual(['m3', 'm1', 'm2'])
      expect(s.monsters[0].dyingUntil).toBeUndefined()
      expect(s.monsters[1].dyingUntil).toBe(T0 + DYING_LINGER_MS)
      expect(s.monsters[2].dyingUntil).toBe(T0 + DYING_LINGER_MS)
      expect(s.pets).toEqual([])
    })

    it('同名怪按 id 双实例共存', () => {
      useSceneStore
        .getState()
        .applyScene(
          mkScene([mkMonster('m1', '小野雉'), mkMonster('m2', '小野雉')]),
        )
      const { monsters } = useSceneStore.getState()
      expect(monsters).toHaveLength(2)
      expect(monsters.map((m) => m.name)).toEqual(['小野雉', '小野雉'])
      expect(new Set(monsters.map((m) => m.id)).size).toBe(2)
    })

    it('消失怪标记 dyingUntil = now + 1200 滞留（不打断死亡动画）', () => {
      useSceneStore
        .getState()
        .applyScene(mkScene([mkMonster('m1'), mkMonster('m2')]))
      useSceneStore.getState().applyScene(mkScene([mkMonster('m2')]))
      const { monsters } = useSceneStore.getState()
      expect(monsters).toHaveLength(2)
      expect(monsters[0].id).toBe('m2')
      expect(monsters[0].dyingUntil).toBeUndefined()
      expect(monsters[1].id).toBe('m1')
      expect(monsters[1].dyingUntil).toBe(T0 + DYING_LINGER_MS)
    })

    it('已 dying 的怪在下一次快照中保留原截止时刻（不续期）', () => {
      useSceneStore.getState().applyScene(mkScene([mkMonster('m1')]))
      useSceneStore.getState().applyScene(mkScene([]))
      vi.advanceTimersByTime(500)
      useSceneStore.getState().applyScene(mkScene([]))
      const { monsters } = useSceneStore.getState()
      expect(monsters).toHaveLength(1)
      expect(monsters[0].dyingUntil).toBe(T0 + DYING_LINGER_MS)
    })

    it('下一次 applyScene 丢弃已过期 dying 条目（dyingUntil < now）', () => {
      useSceneStore.getState().applyScene(mkScene([mkMonster('m1')]))
      useSceneStore.getState().applyScene(mkScene([]))
      expect(useSceneStore.getState().monsters).toHaveLength(1)
      // 模拟无动画事件环境（reduced-motion）：纯时间推进后快照驱动清理
      vi.advanceTimersByTime(DYING_LINGER_MS + 1)
      useSceneStore.getState().applyScene(mkScene([]))
      expect(useSceneStore.getState().monsters).toEqual([])
    })

    it(' dying 未过期时下一次 applyScene 保留（dyingUntil == now 仍存活一帧）', () => {
      useSceneStore.getState().applyScene(mkScene([mkMonster('m1')]))
      useSceneStore.getState().applyScene(mkScene([]))
      vi.advanceTimersByTime(DYING_LINGER_MS) // 恰好到截止时刻：不小于 now，保留
      useSceneStore.getState().applyScene(mkScene([]))
      expect(useSceneStore.getState().monsters).toHaveLength(1)
    })

    it('sweepDying 显式清扫过期条目；未过期不动', () => {
      useSceneStore
        .getState()
        .applyScene(mkScene([mkMonster('m1'), mkMonster('m2')]))
      useSceneStore.getState().applyScene(mkScene([mkMonster('m2')]))

      // 未过期：sweep 不触发状态变更
      const before = useSceneStore.getState().monsters
      useSceneStore.getState().sweepDying()
      expect(useSceneStore.getState().monsters).toBe(before)

      // 过期：sweep 快路径丢弃
      vi.advanceTimersByTime(DYING_LINGER_MS + 1)
      useSceneStore.getState().sweepDying()
      const { monsters } = useSceneStore.getState()
      expect(monsters.map((m) => m.id)).toEqual(['m2'])
    })
  })

  describe('pushLogAction', () => {
    it('9 种 kind 全部入队，字段 snake_case → camelCase', () => {
      const kinds = [
        'attack',
        'miss',
        'kill',
        'defend',
        'dodge',
        'skill',
        'potion',
        'pet_miss',
        'pet_kill',
      ] as const
      for (const kind of kinds) {
        // 每个 kind 用不同 target 防同帧合并
        useSceneStore.getState().pushLogAction({ kind, target_id: `t-${kind}` })
      }
      const { actions } = useSceneStore.getState()
      expect(actions.map((a) => a.kind)).toEqual([...kinds])
      expect(new Set(actions.map((a) => a.id)).size).toBe(kinds.length)
    })

    it('未知 kind 忽略且不影响已有队列', () => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1' })
      const before = useSceneStore.getState().actions
      useSceneStore.getState().pushLogAction({ kind: 'emote', target_id: 'm2' })
      useSceneStore.getState().pushLogAction({ kind: '' })
      expect(useSceneStore.getState().actions).toBe(before)
      expect(useSceneStore.getState().actions).toHaveLength(1)
    })

    it('数值/可选字段映射：dmg/skill_id/exp/hp_restore/mp_restore', () => {
      useSceneStore.getState().pushLogAction({
        kind: 'skill',
        target_id: 'm1',
        dmg: 42,
        skill_id: 'fireball',
      })
      useSceneStore.getState().pushLogAction({
        kind: 'potion',
        hp_restore: 30,
        mp_restore: 10,
      })
      useSceneStore.getState().pushLogAction({ kind: 'kill', target_id: 'm9', exp: 9 })
      const [skill, potion, kill] = useSceneStore.getState().actions
      expect(skill).toMatchObject({ kind: 'skill', targetId: 'm1', dmg: 42, skillId: 'fireball' })
      expect(potion).toMatchObject({ kind: 'potion', hpRestore: 30, mpRestore: 10 })
      expect(kill).toMatchObject({ kind: 'kill', targetId: 'm9', exp: 9 })
    })

    it('同帧同 kind+同 targetId 合并为一条，数值取最新', () => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 5 })
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 9 })
      const { actions } = useSceneStore.getState()
      expect(actions).toHaveLength(1)
      expect(actions[0].dmg).toBe(9)
    })

    it('同帧不同 target 或不同 kind 不合并', () => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 1 })
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm2', dmg: 2 })
      useSceneStore.getState().pushLogAction({ kind: 'miss', target_id: 'm2' })
      expect(useSceneStore.getState().actions).toHaveLength(3)
    })

    it('合并后保持原队尾位置，后续同参动作继续向合并条目聚合', () => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1', dmg: 1 })
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm2', dmg: 2 })
      // m2 连发：与队尾合并
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm2', dmg: 7 })
      const { actions } = useSceneStore.getState()
      expect(actions).toHaveLength(2)
      expect(actions[0]).toMatchObject({ targetId: 'm1', dmg: 1 })
      expect(actions[1]).toMatchObject({ targetId: 'm2', dmg: 7 })
    })

    it('队列容量 24：超出丢弃最旧', () => {
      for (let i = 0; i < SCENE_ACTION_CAP + 6; i++) {
        useSceneStore
          .getState()
          .pushLogAction({ kind: 'attack', target_id: `m${i}`, dmg: i })
      }
      const { actions } = useSceneStore.getState()
      expect(actions).toHaveLength(SCENE_ACTION_CAP)
      expect(actions[0].targetId).toBe('m6')
      expect(actions[actions.length - 1].targetId).toBe(`m${SCENE_ACTION_CAP + 5}`)
    })

    it('removeAction 按 id 出队；未知 id 不触发状态变更', () => {
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1' })
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm2' })
      const [a1] = useSceneStore.getState().actions
      useSceneStore.getState().removeAction(a1.id)
      const rest = useSceneStore.getState().actions
      expect(rest).toHaveLength(1)
      expect(rest[0].targetId).toBe('m2')
      // 未知 id：返回原 state（无冗余渲染）
      useSceneStore.getState().removeAction('act-999999')
      expect(useSceneStore.getState().actions).toBe(rest)
    })
  })

  describe('resetScene', () => {
    it('清空 mapId/monsters/pets/actions', () => {
      useSceneStore
        .getState()
        .applyScene(mkScene([mkMonster('m1')], [mkPet('p1')]))
      useSceneStore.getState().pushLogAction({ kind: 'attack', target_id: 'm1' })
      useSceneStore.getState().resetScene()
      const s = useSceneStore.getState()
      expect(s.mapId).toBeNull()
      expect(s.monsters).toEqual([])
      expect(s.pets).toEqual([])
      expect(s.actions).toEqual([])
    })
  })
})
