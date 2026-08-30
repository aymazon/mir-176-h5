import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

/**
 * 场景单位锚点注册表（todo 12）：
 *  - MonsterUnit/PetUnit/PlayerUnit 挂载时用 useAnchor(id) 注册
 *    `id → () => DOMRect`（惰性读取 getBoundingClientRect，避免渲染期强制布局）
 *  - ActionLayer 用 useAnchorRegistry() 读取全部锚点，按 rect 相对容器定位浮字
 *  - 注册/注销走 provider state（每次生成新 Map），锚点增删触发消费方重渲染
 */

export type AnchorFn = () => DOMRect
export type AnchorMap = Map<string, AnchorFn>

interface AnchorContextValue {
  anchors: AnchorMap
  register: (id: string, fn: AnchorFn) => void
  unregister: (id: string) => void
}

const AnchorContext = createContext<AnchorContextValue | null>(null)

/** 无 Provider 兜底：空表（ActionLayer 对缺失锚点走居中兜底位） */
const EMPTY_MAP: AnchorMap = new Map()

export function AnchorProvider({ children }: { children: ReactNode }) {
  const [anchors, setAnchors] = useState<AnchorMap>(() => new Map())

  const register = useCallback((id: string, fn: AnchorFn) => {
    setAnchors((prev) => {
      const next = new Map(prev)
      next.set(id, fn)
      return next
    })
  }, [])

  const unregister = useCallback((id: string) => {
    setAnchors((prev) => {
      if (!prev.has(id)) return prev
      const next = new Map(prev)
      next.delete(id)
      return next
    })
  }, [])

  const value = useMemo(
    () => ({ anchors, register, unregister }),
    [anchors, register, unregister],
  )

  return (
    <AnchorContext.Provider value={value}>{children}</AnchorContext.Provider>
  )
}

/** 注册/注销锚点：返回 ref 回调，挂载时注册、卸载时注销 */
export function useAnchor(id: string): (el: HTMLElement | null) => void {
  const ctx = useContext(AnchorContext)
  const register = ctx?.register
  const unregister = ctx?.unregister
  return useCallback(
    (el: HTMLElement | null) => {
      if (register === undefined || unregister === undefined) return
      if (el === null) unregister(id)
      else register(id, () => el.getBoundingClientRect())
    },
    [register, unregister, id],
  )
}

/** 读取全部锚点（锚点增删时触发重渲染） */
export function useAnchorRegistry(): AnchorMap {
  const ctx = useContext(AnchorContext)
  return ctx?.anchors ?? EMPTY_MAP
}