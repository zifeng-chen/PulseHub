/**
 * 测试环境兜底：jsdom 没有实现部分浏览器 API，而组件（主题、浮层、图表容器）
 * 会直接调用它们。这里统一打桩，避免每个用例各写一遍。
 */
import { vi } from 'vitest'

// 主题 store 用 matchMedia 判断系统深色偏好
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

// 浮层/图表容器会观察尺寸
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver || (ResizeObserverStub as unknown as typeof ResizeObserver)

class IntersectionObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
  readonly root = null
  readonly rootMargin = ''
  readonly thresholds: number[] = []
}
globalThis.IntersectionObserver =
  globalThis.IntersectionObserver || (IntersectionObserverStub as unknown as typeof IntersectionObserver)

// jsdom 不实现布局滚动
if (!window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = vi.fn()
}
if (!window.scrollTo) {
  Object.defineProperty(window, 'scrollTo', { writable: true, value: vi.fn() })
}
