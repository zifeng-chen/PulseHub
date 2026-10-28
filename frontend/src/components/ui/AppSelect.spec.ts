/** 统一下拉：真正影响用户行为的是「选项对不对、选中后报什么值」。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppSelect from './AppSelect.vue'

const options = [
  { value: '', label: '全部状态' },
  { value: 'draft', label: '草稿' },
  { value: 'confirmed', label: '已确认' },
]

describe('AppSelect', () => {
  it('按 options 渲染全部选项', () => {
    const wrapper = mount(AppSelect, { props: { modelValue: '', options } })
    const labels = wrapper.findAll('option').map((o) => o.text())
    expect(labels).toEqual(['全部状态', '草稿', '已确认'])
  })

  it('传入的 modelValue 会选中对应选项', () => {
    const wrapper = mount(AppSelect, { props: { modelValue: 'confirmed', options } })
    expect(wrapper.find('select').element.value).toBe('confirmed')
  })

  it('用户选择后发出 update:modelValue', async () => {
    const wrapper = mount(AppSelect, { props: { modelValue: '', options } })
    await wrapper.find('select').setValue('draft')
    expect(wrapper.emitted('update:modelValue')).toEqual([['draft']])
  })

  it('placeholder 渲染成禁用空选项，不会与真实值混淆', () => {
    const wrapper = mount(AppSelect, {
      props: { modelValue: '', options: options.slice(1), placeholder: '请选择账户' },
    })
    const first = wrapper.findAll('option')[0]
    expect(first.text()).toBe('请选择账户')
    expect(first.attributes('disabled')).toBeDefined()
    expect(first.element.value).toBe('')
  })

  it('disabled 透传给原生 select', () => {
    const wrapper = mount(AppSelect, { props: { modelValue: '', options, disabled: true } })
    expect(wrapper.find('select').attributes('disabled')).toBeDefined()
  })

  it('modelValue 为 null/undefined 时回退到空值，不抛错', () => {
    const wrapper = mount(AppSelect, { props: { modelValue: null, options } })
    expect(wrapper.find('select').element.value).toBe('')
  })
})
