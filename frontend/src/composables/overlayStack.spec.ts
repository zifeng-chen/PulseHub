/**
 * 弹层栈：抽屉里再开弹窗时，Esc 只能关最上面那层，滚动锁定最后才解锁。
 * 这两条错了的后果是「按一下 Esc 两层一起关，表单内容没了」，属于用户很难描述、
 * 但一出现就会被投诉的问题。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { isTopOverlay, popOverlay, pushOverlay } from './overlayStack'

const a = Symbol('drawer')
const b = Symbol('modal')

beforeEach(() => {
  // 清空可能残留的栈与滚动锁
  popOverlay(a)
  popOverlay(b)
  document.body.style.overflow = ''
})

describe('overlayStack', () => {
  it('只有最后压入的层是栈顶', () => {
    pushOverlay(a)
    pushOverlay(b)
    expect(isTopOverlay(a)).toBe(false)
    expect(isTopOverlay(b)).toBe(true)
  })

  it('栈底被关闭后，上一层重新成为栈顶', () => {
    pushOverlay(a)
    pushOverlay(b)
    popOverlay(b)
    expect(isTopOverlay(a)).toBe(true)
  })

  it('打开任意一层就锁定页面滚动', () => {
    pushOverlay(a)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('还有层开着时关闭其中一层，滚动保持锁定（不能提前解锁）', () => {
    pushOverlay(a)
    pushOverlay(b)
    popOverlay(b)
    expect(document.body.style.overflow).toBe('hidden')
    popOverlay(a)
    expect(document.body.style.overflow).toBe('')
  })

  it('同一层重复压入不会重复入栈（重复挂载不改变栈顶）', () => {
    pushOverlay(a)
    pushOverlay(a)
    pushOverlay(b)
    popOverlay(b)
    expect(isTopOverlay(a)).toBe(true)
    expect(isTopOverlay(b)).toBe(false)
  })

  it('弹出不在栈里的标识不抛错、不影响现状', () => {
    pushOverlay(a)
    const ghost = Symbol('ghost')
    expect(() => popOverlay(ghost)).not.toThrow()
    expect(isTopOverlay(a)).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('空栈时任何层都不是栈顶（组件未打开时不会误响应 Esc）', () => {
    expect(isTopOverlay(a)).toBe(false)
  })
})
