import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import SkillTab from '../ui/SkillTab'
import { useSkillStore } from '../store/skill'

const skillListResponse = {
  learned: [
    {
      skill_id: 'basic_sword',
      name: '剑招起手',
      level: 1,
      exp: 300,
      max_level: 3,
      next_level_exp: 1000,
    },
    {
      skill_id: 'throw_sword',
      name: '破击剑术',
      level: 3,
      exp: 0,
      max_level: 3,
      next_level_exp: 0,
    },
  ],
  available: [
    {
      skill_id: 'assassination',
      name: '贯刺剑术',
      learn_level: 19,
      mp_cost: 5,
      requires_book: false,
    },
    {
      skill_id: 'liehuo',
      name: '焚焰剑法',
      learn_level: 35,
      mp_cost: 20,
      requires_book: true,
    },
  ],
}

function mockFetch(): ReturnType<typeof vi.fn> {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    if (url.includes('/api/skill/list')) {
      return new Response(JSON.stringify(skillListResponse), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'unexpected' }), { status: 404 })
  })
}

beforeEach(() => {
  useSkillStore.getState().reset()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('SkillTab', () => {
  it('挂载拉取技能清单：已学渲染等级徽章与熟练进度，满级显示已满级', async () => {
    mockFetch()
    render(<SkillTab />)

    await waitFor(() => {
      expect(screen.getByTestId('skill-learned-basic_sword')).toBeInTheDocument()
    })
    expect(screen.getByTestId('skill-level-basic_sword')).toHaveTextContent('Lv.1/3')
    expect(screen.getByTestId('skill-exp-basic_sword')).toHaveTextContent('300/1000')
    // 满级（next_level_exp=0 哨兵）
    expect(screen.getByTestId('skill-level-throw_sword')).toHaveTextContent('Lv.3/3')
    expect(screen.getByTestId('skill-exp-throw_sword')).toHaveTextContent('已满级')
  })

  it('可学清单：普通技能提供学习按钮，35 级技能标注需技能书且无按钮', async () => {
    mockFetch()
    render(<SkillTab />)

    await waitFor(() => {
      expect(screen.getByTestId('skill-available-assassination')).toBeInTheDocument()
    })
    expect(screen.getByTestId('skill-learn-assassination')).toBeInTheDocument()
    expect(screen.getByTestId('skill-book-liehuo')).toHaveTextContent('需技能书')
    expect(screen.queryByTestId('skill-learn-liehuo')).not.toBeInTheDocument()
  })

  it('学习成功：POST /api/skill/learn 后重拉清单并 toast', async () => {
    const fetchMock = mockFetch()
    render(<SkillTab />)
    await waitFor(() => {
      expect(screen.getByTestId('skill-learn-assassination')).toBeInTheDocument()
    })

    fetchMock.mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/skill/learn') && !url.includes('learn_from_book')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          skill_id: 'assassination',
        })
        return new Response(
          JSON.stringify({
            skill_id: 'assassination',
            name: '贯刺剑术',
            level: 0,
            exp: 0,
            source: 'learn',
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify(skillListResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('skill-learn-assassination'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('已学会 贯刺剑术')
    })
  })

  it('后端 422 → toast 展示 detail 文案且页面不崩溃', async () => {
    const fetchMock = mockFetch()
    render(<SkillTab />)
    await waitFor(() => {
      expect(screen.getByTestId('skill-learn-assassination')).toBeInTheDocument()
    })

    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/skill/learn')) {
        return new Response(
          JSON.stringify({ detail: '等级不足：贯刺剑术 需要 19 级' }),
          { status: 422 },
        )
      }
      return new Response(JSON.stringify(skillListResponse), { status: 200 })
    })

    fireEvent.click(screen.getByTestId('skill-learn-assassination'))
    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent('等级不足')
    })
    expect(screen.getByTestId('tab-panel-skill')).toBeInTheDocument()
  })
})
