import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import PotionSettings from '../ui/PotionSettings'
import { usePotionStore, DEFAULT_POTION_POLICY } from '../store/potion'

function mockFetchOk(): ReturnType<typeof vi.fn> {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  })
}

beforeEach(() => {
  window.localStorage.clear()
  usePotionStore.getState().reset()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('PotionSettings', () => {
  it('渲染默认策略：30/15/20/5', () => {
    render(<PotionSettings />)
    expect(screen.getByTestId('policy-hp-pct')).toHaveValue(30)
    expect(screen.getByTestId('policy-urgent-hp-pct')).toHaveValue(15)
    expect(screen.getByTestId('policy-mp-pct')).toHaveValue(20)
    expect(screen.getByTestId('policy-recall-slots')).toHaveValue(5)
  })

  it('越界输入（HP 150%）→ 表单校验拦截，不发请求', async () => {
    const fetchMock = mockFetchOk()
    render(<PotionSettings />)

    fireEvent.change(screen.getByTestId('policy-hp-pct'), {
      target: { value: '150' },
    })
    fireEvent.click(screen.getByTestId('policy-submit'))

    await waitFor(() => {
      expect(screen.getByTestId('policy-error')).toHaveTextContent('1–99')
    })
    expect(fetchMock).not.toHaveBeenCalled()
    // store 未被污染
    expect(usePotionStore.getState().policy).toEqual(DEFAULT_POTION_POLICY)
  })

  it('危险线 ≥ 回血线 → 校验拦截', async () => {
    const fetchMock = mockFetchOk()
    render(<PotionSettings />)

    fireEvent.change(screen.getByTestId('policy-urgent-hp-pct'), {
      target: { value: '35' },
    })
    fireEvent.click(screen.getByTestId('policy-submit'))

    await waitFor(() => {
      expect(screen.getByTestId('policy-error')).toHaveTextContent('危险线必须低于回血线')
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('合法提交：POST /api/character/policy（百分比转小数）+ 存 store/localStorage', async () => {
    const fetchMock = mockFetchOk()
    render(<PotionSettings />)

    fireEvent.change(screen.getByTestId('policy-hp-pct'), { target: { value: '40' } })
    fireEvent.change(screen.getByTestId('policy-urgent-hp-pct'), {
      target: { value: '20' },
    })
    fireEvent.change(screen.getByTestId('policy-mp-pct'), { target: { value: '25' } })
    fireEvent.change(screen.getByTestId('policy-recall-slots'), {
      target: { value: '3' },
    })
    fireEvent.click(screen.getByTestId('policy-submit'))

    await waitFor(() => {
      expect(screen.getByTestId('policy-saved')).toHaveTextContent('吃药策略已保存')
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [input, init] = fetchMock.mock.calls[0] as [unknown, RequestInit]
    const url = typeof input === 'string' ? input : (input as Request).url
    expect(url).toContain('/api/character/policy')
    expect(JSON.parse(String(init.body))).toEqual({
      hp_pct: 0.4,
      urgent_hp_pct: 0.2,
      mp_pct: 0.25,
      recall_free_slots: 3,
    })
    // 本地持久化（刷新后可恢复）
    expect(usePotionStore.getState().policy.hpPct).toBe(40)
    expect(window.localStorage.getItem('mir.potionPolicy')).toContain('"hpPct":40')
  })

  it('后端无端点（404）→ 降级本地保存并如实提示', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      return new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })
    })
    render(<PotionSettings />)

    fireEvent.click(screen.getByTestId('policy-submit'))
    await waitFor(() => {
      expect(screen.getByTestId('policy-saved')).toHaveTextContent('本地')
    })
    // 默认策略也已写入 store（本地生效）
    expect(usePotionStore.getState().policy).toEqual(DEFAULT_POTION_POLICY)
  })
})
