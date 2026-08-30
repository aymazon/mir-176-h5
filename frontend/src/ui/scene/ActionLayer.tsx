import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type AnimationEvent,
} from 'react'
import { useSceneStore } from '../../store/scene'
import type { SceneAction } from '../../store/scene'
import { skillVisualOf } from '../../data/skill-visuals'
import { useAnchorRegistry, type AnchorMap } from './anchorContext'

/**
 * 战斗动作动画层（todo 12）：
 *  - pointer-events-none 绝对定位覆盖层，读 scene store actions 按 kind 渲染浮字/横幅
 *  - 定位：单位锚点注册表（anchorContext）取 `id → () => DOMRect`，
 *    相对本层容器换算坐标（锚点中心水平对齐、锚点顶部上方垂直对齐）
 *  - 回收纪律：默认每个浮字 onAnimationEnd → removeAction(id)；
 *    reduced-motion 豁免（CSS 已禁动画、onAnimationEnd 不触发）→ 挂载后下一帧
 *    requestAnimationFrame 立即 removeAction（静态闪现一帧即消），并监听 matchMedia.change
 *  - 同帧去重已在 store 层完成（pushLogAction 队尾合并），本层不重复去重
 */

/** 玩家锚点 id（PlayerUnit 注册） */
const PLAYER_ANCHOR = 'player'

/** 技能名：取 log 文本去除「你施放了」前缀；无文本回落 skillId */
function skillNameOf(action: SceneAction): string {
  if (action.text !== undefined) {
    const stripped = action.text.replace(/^你施放了/, '')
    if (stripped !== '') return stripped
  }
  return action.skillId ?? ''
}

interface ActionVisual {
  text: string
  colorClass: string
  animClass: string
  anchorId: string | undefined
  fallbackId: string
}

/** 按 kind 描述浮字视觉；返回 null = 不渲染（如 hpRestore<=0 的 potion） */
function describeAction(action: SceneAction): ActionVisual | null {
  switch (action.kind) {
    case 'attack':
      return {
        text: `-${action.dmg ?? 0}`,
        colorClass:
          action.skillId !== undefined
            ? skillVisualOf(action.skillId).colorClass
            : 'text-amber-300',
        animClass: 'scene-dmg-float',
        anchorId: action.targetId,
        fallbackId: PLAYER_ANCHOR,
      }
    case 'miss':
    case 'dodge':
      return {
        text: 'MISS',
        colorClass: 'text-slate-400',
        animClass: 'scene-dmg-float',
        anchorId: action.targetId,
        fallbackId: PLAYER_ANCHOR,
      }
    case 'kill':
      return {
        text: `+${action.exp ?? 0} 经验`,
        colorClass: 'text-green-400',
        animClass: 'scene-dmg-float',
        anchorId: action.targetId,
        fallbackId: PLAYER_ANCHOR,
      }
    case 'defend':
      return {
        text: `-${action.dmg ?? 0}`,
        colorClass: 'text-red-400',
        animClass: 'scene-dmg-float',
        anchorId: PLAYER_ANCHOR,
        fallbackId: PLAYER_ANCHOR,
      }
    case 'skill': {
      const visual = skillVisualOf(action.skillId ?? '')
      return {
        text: `${visual.emoji} ${skillNameOf(action)}`,
        colorClass: visual.colorClass,
        animClass: 'scene-banner-pop',
        anchorId: PLAYER_ANCHOR,
        fallbackId: PLAYER_ANCHOR,
      }
    }
    case 'potion':
      if ((action.hpRestore ?? 0) <= 0) return null
      return {
        text: `+${action.hpRestore}`,
        colorClass: 'text-green-400',
        animClass: 'scene-heal-pulse',
        anchorId: PLAYER_ANCHOR,
        fallbackId: PLAYER_ANCHOR,
      }
    case 'pet_miss':
      return {
        text: 'MISS',
        colorClass: 'text-slate-400',
        animClass: 'scene-dmg-float',
        anchorId: action.petId,
        fallbackId: PLAYER_ANCHOR,
      }
    case 'pet_kill':
      return {
        text: `+${action.exp ?? 0} 经验`,
        colorClass: 'text-green-400',
        animClass: 'scene-dmg-float',
        anchorId: action.petId,
        fallbackId: PLAYER_ANCHOR,
      }
    default:
      return null
  }
}

/** 取锚点 rect：优先 anchorId，缺失回落 fallbackId；都缺返回 null */
function anchorRect(
  anchors: AnchorMap,
  anchorId: string | undefined,
  fallbackId: string,
): DOMRect | null {
  if (anchorId !== undefined) {
    const fn = anchors.get(anchorId)
    if (fn !== undefined) return fn()
  }
  const fb = anchors.get(fallbackId)
  return fb !== undefined ? fb() : null
}

/** 视口坐标 → 本层容器坐标（锚点中心水平、锚点顶部垂直）；未测量返回 null 走居中兜底 */
function toLayerPos(
  rect: DOMRect | null,
  layerRect: DOMRect | null,
): { left: number; top: number } | null {
  if (rect === null || layerRect === null) return null
  return {
    left: rect.left + rect.width / 2 - layerRect.left,
    top: rect.top - layerRect.top,
  }
}

/** prefers-reduced-motion 探测 + change 监听（reduced-motion 回收豁免用） */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (
      typeof window === 'undefined' ||
      typeof window.matchMedia !== 'function'
    ) {
      return false
    }
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })

  useEffect(() => {
    if (
      typeof window === 'undefined' ||
      typeof window.matchMedia !== 'function'
    ) {
      return
    }
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return reduced
}

function ActionItem(props: {
  action: SceneAction
  anchors: AnchorMap
  layerRect: DOMRect | null
  reduced: boolean
  removeAction: (id: string) => void
}) {
  const { action, anchors, layerRect, reduced, removeAction } = props
  const visual = describeAction(action)
  const rect = anchorRect(
    anchors,
    visual?.anchorId,
    visual?.fallbackId ?? PLAYER_ANCHOR,
  )
  const pos = toLayerPos(rect, layerRect)

  const done = useCallback(
    () => removeAction(action.id),
    [removeAction, action.id],
  )

  // reduced-motion 豁免：CSS 已禁动画、onAnimationEnd 不触发 → 静态闪现一帧后 rAF 回收
  useEffect(() => {
    if (!reduced) return
    const raf = requestAnimationFrame(done)
    return () => cancelAnimationFrame(raf)
  }, [reduced, done])

  if (visual === null) return null

  const handleAnimationEnd = (e: AnimationEvent<HTMLDivElement>) => {
    if (e.currentTarget !== e.target) return
    done()
  }

  return (
    <div
      data-testid={`action-${action.kind}`}
      className={`absolute ${reduced ? '' : visual.animClass} ${visual.colorClass}`}
      style={
        pos === null
          ? { left: '50%', top: '50%' }
          : { left: pos.left, top: pos.top }
      }
      onAnimationEnd={reduced ? undefined : handleAnimationEnd}
    >
      <div className="-translate-x-1/2 -translate-y-full whitespace-nowrap text-sm font-bold">
        {visual.text}
      </div>
    </div>
  )
}

export default function ActionLayer() {
  const actions = useSceneStore((s) => s.actions)
  const removeAction = useSceneStore((s) => s.removeAction)
  const anchors = useAnchorRegistry()
  const reduced = usePrefersReducedMotion()
  const layerRef = useRef<HTMLDivElement>(null)
  const [layerRect, setLayerRect] = useState<DOMRect | null>(null)

  useEffect(() => {
    const el = layerRef.current
    if (el === null) return
    const measure = () => setLayerRect(el.getBoundingClientRect())
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  return (
    <div
      ref={layerRef}
      data-testid="action-layer"
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      {actions.map((action) => (
        <ActionItem
          key={action.id}
          action={action}
          anchors={anchors}
          layerRect={layerRect}
          reduced={reduced}
          removeAction={removeAction}
        />
      ))}
    </div>
  )
}