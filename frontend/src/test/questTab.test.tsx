import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import QuestTab from '../ui/QuestTab'
import { useQuestStore } from '../store/quest'

function questRow(over: Record<string, unknown>): Record<string, unknown> {
  return {
    quest_id: 'q1',
    name: '除鸡行动',
    kind: 'kill',
    status: 'not_started',
    level_req: 1,
    required_class: null,
    target_id: 'chicken',
    target_count: 5,
    count: 0,
    need: 5,
    reward_gold: 100,
    reward_exp: 50,
    reward_items: [],
    dialogue: {},
    arena_map_id: null,
    ...over,
  }
}

const questListResponse = {
  not_started: [questRow({ quest_id: 'q_new', name: '新手悬赏' })],
  in_progress: [
    questRow({ quest_id: 'q_kill', name: '杀鸡悬赏', status: 'in_progress', count: 2 }),
    questRow({
      quest_id: 'q_collect',
      name: '收集鸡肉',
      kind: 'collect',
      target_id: 'chicken_meat',
      status: 'in_progress',
      count: 0,
    }),
  ],
  completed: [
    questRow({ quest_id: 'q_done', name: '已完成悬赏', status: 'completed', count: 5 }),
  ],
  delivered: [
    questRow({ quest_id: 'q_old', name: '上古任务', status: 'delivered', count: 5 }),
  ],
}

function mockFetch(): ReturnType<typeof vi.fn> {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    if (url.includes('/api/quest/list')) {
      return new Response(JSON.stringify(questListResponse), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'unexpected' }), { status: 404 })
  })
}

beforeEach(() => {
  useQuestStore.getState().reset()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('QuestTab', () => {
  it('挂载拉取任务清单：四状态分组渲染，进行中带进度条', async () => {
    mockFetch()
    render(<QuestTab />)

    await waitFor(() => {
      expect(screen.getByTestId('quest-q_kill')).toBeInTheDocument()
    })
    expect(screen.getByTestId('quest-progress-q_kill')).toHaveTextContent('2/5')
    expect(screen.getByTestId('quest-q_new')).toBeInTheDocument()
    expect(screen.getByTestId('quest-q_done')).toBeInTheDocument()
    expect(screen.getByTestId('quest-q_old')).toBeInTheDocument()
    // 按钮按组就位
    expect(screen.getByTestId('quest-accept-q_new')).toBeInTheDocument()
    expect(screen.getByTestId('quest-complete-q_done')).toHaveTextContent('交付领奖')
    // collect 任务的推进按钮文案为缴纳物品
    expect(screen.getByTestId('quest-advance-q_collect')).toHaveTextContent('缴纳物品')
    expect(screen.getByTestId('quest-advance-q_kill')).toHaveTextContent('上报击杀')
  })

  it('接取任务：POST /api/quest/accept 后重拉清单', async () => {
    const fetchMock = mockFetch()
    render(<QuestTab />)
    await waitFor(() => {
      expect(screen.getByTestId('quest-accept-q_new')).toBeInTheDocument()
    })

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/quest/accept')) {
        expect(JSON.parse(String(init?.body))).toEqual({ quest_id: 'q_new' })
        return new Response(
          JSON.stringify({
            quest: questRow({ quest_id: 'q_new', status: 'in_progress' }),
            message: '接受任务【新手悬赏】',
            map_id: null,
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(questListResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('quest-accept-q_new'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('接受任务【新手悬赏】')
    })
  })

  it('推进进度：kill 任务 POST action=kill 并 toast 后端文案', async () => {
    const fetchMock = mockFetch()
    render(<QuestTab />)
    await waitFor(() => {
      expect(screen.getByTestId('quest-advance-q_kill')).toBeInTheDocument()
    })

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/quest/progress')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          quest_id: 'q_kill',
          action: 'kill',
        })
        return new Response(
          JSON.stringify({
            quest_id: 'q_kill',
            count: 3,
            need: 5,
            status: 'in_progress',
            message: '击杀进度 3/5',
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(questListResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('quest-advance-q_kill'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('击杀进度 3/5')
    })
  })

  it('交付任务：POST /api/quest/complete 并 toast 奖励摘要', async () => {
    const fetchMock = mockFetch()
    render(<QuestTab />)
    await waitFor(() => {
      expect(screen.getByTestId('quest-complete-q_done')).toBeInTheDocument()
    })

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/quest/complete')) {
        expect(JSON.parse(String(init?.body))).toEqual({ quest_id: 'q_done' })
        return new Response(
          JSON.stringify({
            quest_id: 'q_done',
            name: '已完成悬赏',
            status: 'delivered',
            gold: 200,
            exp: 100,
            rewards: { gold: 100, exp: 50, items: [] },
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(questListResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('quest-complete-q_done'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent(
        '交付【已完成悬赏】：金币+100 经验+50',
      )
    })
  })

  it('后端 409（重复交付）→ toast 透传 detail 且页面不崩溃', async () => {
    const fetchMock = mockFetch()
    render(<QuestTab />)
    await waitFor(() => {
      expect(screen.getByTestId('quest-complete-q_done')).toBeInTheDocument()
    })

    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/quest/complete')) {
        return new Response(JSON.stringify({ detail: '任务已交付' }), {
          status: 409,
        })
      }
      return new Response(JSON.stringify(questListResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('quest-complete-q_done'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('任务已交付')
    })
    expect(screen.getByTestId('tab-panel-quest')).toBeInTheDocument()
  })
})
