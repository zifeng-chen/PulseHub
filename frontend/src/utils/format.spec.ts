/** 金额格式化：全站金额显示的唯一入口，精度与「—」占位都从这里出。 */
import { describe, expect, it } from 'vitest'

import { formatAmount } from './format'

describe('formatAmount', () => {
  it('按精度补足小数并加千分位', () => {
    expect(formatAmount('23456.78', 2)).toBe('23,456.78')
    expect(formatAmount(1234.5, 2)).toBe('1,234.50')
    expect(formatAmount('0', 2)).toBe('0.00')
  })

  it('精度为 0 时四舍五入到整数', () => {
    expect(formatAmount('23456.78', 0)).toBe('23,457')
    expect(formatAmount('0.4', 0)).toBe('0')
  })

  it('负数保留符号', () => {
    expect(formatAmount('-12345.67', 2)).toBe('-12,345.67')
  })

  it('空值显示破折号，不显示 NaN 或空白', () => {
    expect(formatAmount(null, 2)).toBe('—')
    expect(formatAmount(undefined, 2)).toBe('—')
    expect(formatAmount('', 2)).toBe('—')
  })

  it('非数字原样返回，便于暴露上游数据问题', () => {
    expect(formatAmount('abc', 2)).toBe('abc')
  })
})
