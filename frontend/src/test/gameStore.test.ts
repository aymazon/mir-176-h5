import { describe, it, expect, beforeEach } from 'vitest'
import { useGameStore } from '../store/game'

function login(): void {
  useGameStore.getState().setGame({
    player: { id: 'p1', username: 'tester' },
    character: {
      id: 'c1',
      name: '勇者',
      class: 'warrior',
      level: 1,
      hp: 10,
      mp: 5,
      gold: 0,
      bindCity: 'yin-xing-cun',
      mapId: 'yin-xing-cun',
    },
  })
}

describe('gameStore stat/日志/掉落入账', () => {
  beforeEach(() => {
    useGameStore.getState().clearAuth()
    login()
  })

  it('applyStat 更新 hp/mp/exp/gold 与面板快照', () => {
    useGameStore.getState().applyStat({
      tick: 3,
      hp: 24,
      max_hp: 24,
      mp: 10,
      max_mp: 10,
      exp: 18,
      gold: 55,
      panel: { dc_min: 1, dc_max: 5, luck: 0 },
    })
    const s = useGameStore.getState()
    expect(s.character).toMatchObject({ hp: 24, mp: 10, exp: 18, gold: 55 })
    expect(s.panel).toMatchObject({ dc_max: 5 })
  })

  it('zone_change 切地图并清空地面掉落', () => {
    useGameStore.getState().applyDropLoot([
      { item_id: 'meat', qty: 1, picked: false, drop_id: 'd-1' },
    ])
    useGameStore.getState().applyZoneChange('biqi-wild')
    const s = useGameStore.getState()
    expect(s.character?.mapId).toBe('biqi-wild')
    expect(s.groundDrops).toHaveLength(0)
  })

  it('drop loot 仅收 picked=false 的物品；拾取后可移除', () => {
    useGameStore.getState().applyDropLoot([
      { gold: 15 },
      { item_id: 'meat', qty: 1, picked: true },
      { item_id: 'wooden_sword', qty: 1, picked: false, drop_id: 'd-9' },
    ])
    const drops = useGameStore.getState().groundDrops
    expect(drops).toEqual([{ dropId: 'd-9', itemId: 'wooden_sword', qty: 1 }])

    useGameStore.getState().removeGroundDrop('d-9')
    expect(useGameStore.getState().groundDrops).toHaveLength(0)
  })

  it('未登录时 applyStat 不建角色快照', () => {
    useGameStore.getState().clearAuth()
    useGameStore.getState().applyStat({ hp: 5, mp: 5, exp: 0, gold: 1 })
    expect(useGameStore.getState().character).toBeNull()
  })
})
