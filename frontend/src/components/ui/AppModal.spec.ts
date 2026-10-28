/**
 * 统一弹窗：用户只关心「打开时能看到什么、点哪里关、点确认发出什么」，
 * 所以按黑盒方式断言可见文案与事件，不碰内部状态。
 * 组件用 Teleport 挂到 body，这里直接查 document。
 */
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import AppModal from './AppModal.vue'

function mountModal(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  return mount(AppModal, {
    props: { open: true, title: '生成本期账单', ...props },
    slots: { default: '<p>弹窗内容</p>', ...slots },
    attachTo: document.body,
  })
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('AppModal', () => {
  it('未打开时不渲染任何内容', () => {
    mountModal({ open: false })
    expect(document.querySelector('.modal-card')).toBeNull()
  })

  it('打开后显示标题与内容槽', () => {
    mountModal()
    expect(document.querySelector('.modal-head')?.textContent).toContain('生成本期账单')
    expect(document.body.textContent).toContain('弹窗内容')
  })

  it('点「取消」发出 close，不发出 confirm', async () => {
    const wrapper = mountModal()
    const cancel = Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((b) => b.textContent?.trim() === '取消')
    cancel?.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })

  it('点确认按钮发出 confirm', async () => {
    const wrapper = mountModal({ confirmText: '生成账单' })
    const confirm = Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((b) => b.textContent?.trim() === '生成账单')
    confirm?.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('confirm')).toHaveLength(1)
  })

  it('确认中：按钮禁用且提示「处理中…」，避免重复提交', () => {
    mountModal({ confirmText: '生成账单', confirmLoading: true })
    const confirm = Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((b) => b.textContent?.trim() === '处理中…')
    expect(confirm).toBeTruthy()
    expect(confirm?.disabled).toBe(true)
  })

  it('hideFooter 时不渲染底部按钮', () => {
    mountModal({ hideFooter: true })
    expect(document.querySelector('.modal-foot')).toBeNull()
  })

  it('按 Esc 发出 close', async () => {
    const wrapper = mountModal()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('escClosable=false 时 Esc 不关闭（未保存表单防误关）', async () => {
    const wrapper = mountModal({ escClosable: false })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toBeUndefined()
  })
})
