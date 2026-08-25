import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  restFetch,
  setUnauthorizedRedirect,
  resetUnauthorizedRedirect,
} from '../api/rest'
import { useGameStore } from '../store/game'

describe('rest 401 拦截', () => {
  let redirectedTo: string | null = null

  beforeEach(() => {
    redirectedTo = null
    setUnauthorizedRedirect((path) => {
      redirectedTo = path
    })
    // 预置已登录态，验证 401 后被清空
    useGameStore.getState().setGame({
      player: { id: 'p1', username: 'tester' },
      character: {
        id: 'c1',
        name: 'tester',
        class: 'warrior',
        level: 1,
        hp: 100,
        mp: 50,
        gold: 0,
        bindCity: 'yin-xing-cun',
      },
    })
  })

  afterEach(() => {
    resetUnauthorizedRedirect()
    vi.restoreAllMocks()
    useGameStore.getState().clearAuth()
  })

  it('正常响应直接返回，且自动携带 cookie credentials', async () => {
    const mock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      )
    const res = await restFetch('/api/character/me')
    expect(res.status).toBe(200)
    expect(mock).toHaveBeenCalledWith(
      '/api/character/me',
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(useGameStore.getState().player).not.toBeNull()
    expect(redirectedTo).toBeNull()
  })

  it('401 → 清本地登录态（player/character 双 null）+ 跳登录页 + 抛错中止', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('unauthorized', { status: 401 }),
    )
    await expect(restFetch('/api/whoami')).rejects.toThrow('unauthorized')
    const state = useGameStore.getState()
    expect(state.player).toBeNull()
    expect(state.character).toBeNull()
    expect(redirectedTo).toBe('/login')
  })

  it('非 401 错误（如 500）不触发登出与跳转', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('oops', { status: 500 }),
    )
    const res = await restFetch('/api/whoami')
    expect(res.status).toBe(500)
    expect(useGameStore.getState().player).not.toBeNull()
    expect(redirectedTo).toBeNull()
  })

  it('鉴权端点自身 401 = 凭据错误：不跳登录、不清登录态、不抛 UnauthorizedError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: '用户名或密码错误' }), {
        status: 401,
      }),
    )
    const res = await restFetch('/api/auth/login')
    expect(res.status).toBe(401)
    // 登录态与跳转均不受影响（todo 31：登录失败不能被当成会话过期）
    expect(useGameStore.getState().player).not.toBeNull()
    expect(redirectedTo).toBeNull()
  })
})
