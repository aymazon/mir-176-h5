import { expect, test } from '@playwright/test'
import type { Route } from '@playwright/test'

/**
 * todo 32 e2e：技能/任务/传送 Tab + 战斗页吃药策略表单。
 * 全部 REST 走 route mock（后端并行开发，不依赖 :8000 在位）；
 * 截图落 .omo/evidence/task-32-mir-176-h5-mvp-{skill,quest,teleport,policy}.png。
 */

const CHARACTER = {
  id: 1,
  name: '勇者',
  class: 'warrior',
  level: 20,
  exp: 1234,
  hp: 80,
  mp: 30,
  gold: 1000,
  map_id: 'yin-xing-cun',
  bind_city: 'yin-xing-cun',
  bag_capacity: 40,
}

const SKILL_LIST = {
  learned: [
    {
      skill_id: 'basic_sword',
      name: '基本剑术',
      level: 2,
      exp: 1500,
      max_level: 3,
      next_level_exp: 3000,
    },
  ],
  available: [
    {
      skill_id: 'half_moon',
      name: '半月弯刀',
      learn_level: 20,
      mp_cost: 8,
      requires_book: false,
    },
  ],
}

const QUEST_LIST = {
  not_started: [
    {
      quest_id: 'newbie_kill_chicken',
      name: '杀鸡儆猴',
      kind: 'kill',
      status: 'not_started',
      level_req: 1,
      required_class: null,
      target_id: 'chicken',
      target_count: 5,
      count: 0,
      need: 5,
      reward_gold: 100,
      reward_exp: 60,
      reward_items: [],
      dialogue: { accept: '村口的鸡成灾了，去杀几只吧。' },
      arena_map_id: null,
    },
  ],
  in_progress: [],
  completed: [],
  delivered: [],
}

const TELEPORT_PANEL = {
  current_map_id: 'yin-xing-cun',
  current_map_name: '银杏村',
  bind_city: 'yin-xing-cun',
  traveling: false,
  travel_remaining_seconds: null,
  travel_target_map_id: null,
  destinations: [
    { map_id: 'biqi-wild', name: '比奇野外', fee: 100, kind: 'route', depth: null },
    { map_id: 'shimen', name: '石墓（猪洞） 5 层', fee: 500, kind: 'cave', depth: 5 },
  ],
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test.beforeEach(async ({ context }) => {
  await context.route('**/api/character/me', (route) => json(route, CHARACTER))
  await context.route('**/api/health', (route) => json(route, {}))
  await context.route('**/api/inventory/bag', (route) =>
    json(route, { items: [], gold: 1000, capacity: 40, used: 0 }),
  )
  await context.route('**/api/skill/list', (route) => json(route, SKILL_LIST))
  await context.route('**/api/quest/list', (route) => json(route, QUEST_LIST))
  await context.route('**/api/teleport/panel', (route) => json(route, TELEPORT_PANEL))
})

test('e2e-tabs: 技能页渲染已学等级/经验进度与可学清单', async ({ page }) => {
  await page.goto('/skill')

  await expect(page.locator('[data-testid="tab-panel-skill"]')).toBeVisible()
  await expect(page.locator('[data-testid="skill-learned-basic_sword"]')).toContainText(
    '基本剑术',
  )
  await expect(page.locator('[data-testid="skill-level-basic_sword"]')).toHaveText(
    'Lv.2/3',
  )
  await expect(page.locator('[data-testid="skill-exp-basic_sword"]')).toHaveText(
    '1500/3000',
  )
  await expect(
    page.locator('[data-testid="skill-available-half_moon"]'),
  ).toContainText('半月弯刀')

  await page.screenshot({
    path: '../.omo/evidence/task-32-mir-176-h5-mvp-skill.png',
    fullPage: false,
  })
})

test('e2e-tabs: 任务页分组渲染 + 接取闭环', async ({ page }) => {
  await page.goto('/quest')

  await expect(page.locator('[data-testid="tab-panel-quest"]')).toBeVisible()
  await expect(
    page.locator('[data-testid="quest-newbie_kill_chicken"]'),
  ).toContainText('杀鸡儆猴')

  // 接取：POST /api/quest/accept → toast + 重拉清单
  await page.context().route('**/api/quest/accept', (route) =>
    json(route, {
      quest: { ...QUEST_LIST.not_started[0], status: 'in_progress' },
      message: '接受任务【杀鸡儆猴】',
      map_id: null,
    }),
  )
  await page.locator('[data-testid="quest-accept-newbie_kill_chicken"]').click()
  await expect(page.locator('[data-testid="toast"]')).toContainText(
    '接受任务【杀鸡儆猴】',
  )

  await page.screenshot({
    path: '../.omo/evidence/task-32-mir-176-h5-mvp-quest.png',
    fullPage: false,
  })
})

test('e2e-tabs: 传送页层列表+费用+确认 → 扣金币 + 行进横幅', async ({ page }) => {
  await page.goto('/teleport')

  await expect(page.locator('[data-testid="teleport-current"]')).toHaveText('银杏村')
  await expect(page.locator('[data-testid="teleport-fee-shimen:5"]')).toContainText(
    '500 金',
  )

  // 两段式确认：首点仅变确认态
  const warpBtn = page.locator('[data-testid="teleport-warp-shimen:5"]')
  await warpBtn.click()
  await expect(warpBtn).toHaveText('确认 500 金')

  await page.context().route('**/api/teleport/warp', (route) =>
    json(route, {
      target_map_id: 'shimen',
      layer: 5,
      fee: 500,
      gold_after: 500,
      travel_seconds: 12,
      traveling_until: Date.now() / 1000 + 12,
    }),
  )
  await warpBtn.click()

  // 扣费落地：金币 1000 → 500；行进中横幅出现
  await expect(page.locator('[data-testid="teleport-gold"]')).toHaveText('500 金')
  await expect(page.locator('[data-testid="traveling-banner"]')).toBeVisible()
  await expect(page.locator('[data-testid="toast"]')).toContainText('-500 金')

  await page.screenshot({
    path: '../.omo/evidence/task-32-mir-176-h5-mvp-teleport.png',
    fullPage: false,
  })
})

test('e2e-tabs: 战斗页吃药策略表单——提交闭环 + 越界校验拦截', async ({ page }) => {
  let policyPosts = 0
  let lastBody: unknown = null
  await page.context().route('**/api/character/policy', async (route) => {
    policyPosts += 1
    lastBody = route.request().postDataJSON()
    await json(route, { ok: true })
  })

  await page.goto('/battle')
  await expect(page.locator('[data-testid="potion-form"]')).toBeVisible()

  // failure 场景：150% 越界 → 表单校验拦截，不发请求
  await page.locator('[data-testid="policy-hp-pct"]').fill('150')
  await page.locator('[data-testid="policy-submit"]').click()
  await expect(page.locator('[data-testid="policy-error"]')).toContainText('1–99')
  expect(policyPosts).toBe(0)

  // happy 场景：合法阈值 → POST body 断言 + 保存提示
  await page.locator('[data-testid="policy-hp-pct"]').fill('35')
  await page.locator('[data-testid="policy-urgent-hp-pct"]').fill('18')
  await page.locator('[data-testid="policy-mp-pct"]').fill('22')
  await page.locator('[data-testid="policy-recall-slots"]').fill('4')
  await page.locator('[data-testid="policy-submit"]').click()
  await expect(page.locator('[data-testid="policy-saved"]')).toContainText(
    '吃药策略已保存',
  )
  expect(policyPosts).toBe(1)
  expect(lastBody).toEqual({
    hp_pct: 0.35,
    urgent_hp_pct: 0.18,
    mp_pct: 0.22,
    recall_free_slots: 4,
  })

  await page.screenshot({
    path: '../.omo/evidence/task-32-mir-176-h5-mvp-policy.png',
    fullPage: false,
  })
})
