import { useGameStore } from '../store/game'
import type { CharacterSnapshot } from '../store/game'
import { useSceneStore } from '../store/scene'

/**
 * REST fetch 封装：
 *  - credentials: 'include'，HttpOnly cookie 自动随请求发出
 *  - 任一响应 401 → 清本地登录态（setGame 双 null）+ 跳登录页，并 throw 中止上层
 */

/** 供测试注入的重定向出口；生产环境走浏览器历史路由跳转 */
type RedirectFn = (path: string) => void

let redirectTo: RedirectFn = (path) => {
  window.location.assign(path)
}

/** 测试可替换重定向行为（jsdom 不支持真实导航） */
export function setUnauthorizedRedirect(fn: RedirectFn): void {
  redirectTo = fn
}

export function resetUnauthorizedRedirect(): void {
  redirectTo = (path) => {
    window.location.assign(path)
  }
}

/** 401 统一处理：清登录态 + 清场景 + 跳登录页（SSE 探测也复用） */
export function handleUnauthorized(): void {
  useGameStore.getState().clearAuth()
  useSceneStore.getState().resetScene()
  redirectTo('/login')
}

/** 鉴权端点自身的 401 = 凭据错误而非会话过期，不走全局跳登录（todo 31 修正） */
const AUTH_ENDPOINTS: ReadonlySet<string> = new Set([
  '/api/auth/login',
  '/api/auth/register',
])

export async function restFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const res = await fetch(path, {
    ...init,
    credentials: 'include',
  })
  if (res.status === 401 && !AUTH_ENDPOINTS.has(path)) {
    handleUnauthorized()
    throw new UnauthorizedError(path)
  }
  return res
}

export class UnauthorizedError extends Error {
  constructor(path: string) {
    super(`unauthorized: ${path}`)
    this.name = 'UnauthorizedError'
  }
}

/** JSON 请求便捷封装：json 字段自动序列化并补 Content-Type/method */
export async function restJson<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const { json, ...restInit } = init ?? {}
  const res = await restFetch(path, {
    ...restInit,
    method: restInit.method ?? (json !== undefined ? 'POST' : 'GET'),
    headers: {
      ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(restInit.headers ?? {}),
    },
    body: json !== undefined ? JSON.stringify(json) : restInit.body,
  })
  return (await res.json()) as T
}

// ---------------------------------------------------------------------------
// 鉴权/角色端点薄封装（todo 3 契约；后端并行就位，失败路径必须可降级展示）
// ---------------------------------------------------------------------------

export interface AuthResult {
  ok: boolean
  message?: string
}


/** 鉴权端点错误体统一 {detail: string}；非 JSON/网络错降级为通用文案 */
async function authRequest(
  path: string,
  username: string,
  password: string,
  fallbackMessage: string,
): Promise<AuthResult> {
  try {
    const res = await restFetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (res.ok) return { ok: true }
    const data = (await res.json().catch(() => null)) as
      | { detail?: unknown }
      | null
    return {
      ok: false,
      message:
        typeof data?.detail === 'string' ? data.detail : fallbackMessage,
    }
  } catch (err) {
    if (err instanceof UnauthorizedError) throw err
    return { ok: false, message: err instanceof Error ? err.message : fallbackMessage }
  }
}

export async function apiRegister(
  username: string,
  password: string,
): Promise<AuthResult> {
  return authRequest('/api/auth/register', username, password, '注册失败')
}

export async function apiLogin(
  username: string,
  password: string,
): Promise<AuthResult> {
  return authRequest('/api/auth/login', username, password, '登录失败')
}

export async function apiLogout(): Promise<void> {
  try {
    await restFetch('/api/auth/logout', { method: 'POST' })
  } finally {
    useGameStore.getState().clearAuth()
  }
}

/**
 * 会话恢复：读当前角色快照进 game store。
 * 网络失败（后端未就位）静默返回 null，不阻塞占位页渲染。
 */
export async function fetchCharacterMe(): Promise<CharacterSnapshot | null> {
  try {
    const me = await restJson<Record<string, unknown>>('/api/character/me')
    const character = normalizeCharacter(me)
    if (character) {
      useGameStore.getState().setGame({ character })
    }
    return character
  } catch {
    return null
  }
}

function normalizeCharacter(raw: Record<string, unknown>): CharacterSnapshot | null {
  if (!raw || typeof raw !== 'object') return null
  const cls = String(raw.class ?? raw.class_ ?? '')
  const known = ['warrior', 'mage', 'taoist'] as const
  const characterClass = known.find((k) => k === cls)
  if (!characterClass || raw.id === undefined || raw.name === undefined) {
    return null
  }
  return {
    id: String(raw.id),
    name: String(raw.name),
    class: characterClass,
    level: Number(raw.level ?? 1),
    hp: Number(raw.hp ?? 0),
    mp: Number(raw.mp ?? 0),
    gold: Number(raw.gold ?? 0),
    bindCity: String(raw.bind_city ?? raw.bindCity ?? ''),
    exp: Number(raw.exp ?? 0),
    mapId: String(raw.map_id ?? raw.mapId ?? ''),
  }
}
