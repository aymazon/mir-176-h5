import '@testing-library/jest-dom/vitest'

// jsdom 未实现 AnimationEvent：React DOM 检测到缺失后降级为监听 webkitAnimationEnd，
// 导致 fireEvent.animationEnd 无法触发 onAnimationEnd（react-dom-client 的
// getVendorPrefixedEventName 在 jsdom style 上命中 WebkitAnimation）。
// setupFiles 先于 react-dom 求值，补最小 polyfill 使 React 走标准 animationend。
if (typeof window !== 'undefined' && !('AnimationEvent' in window)) {
  class AnimationEventPolyfill extends Event {
    readonly animationName: string
    readonly elapsedTime: number
    readonly pseudoElement: string
    constructor(
      type: string,
      init: EventInit & {
        animationName?: string
        elapsedTime?: number
        pseudoElement?: string
      } = {},
    ) {
      super(type, init)
      this.animationName = init.animationName ?? ''
      this.elapsedTime = init.elapsedTime ?? 0
      this.pseudoElement = init.pseudoElement ?? ''
    }
  }
  Object.defineProperty(window, 'AnimationEvent', {
    value: AnimationEventPolyfill,
    writable: true,
    configurable: true,
  })
}
