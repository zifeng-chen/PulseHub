/**
 * 账户下拉文案：`名称 · 尾号 · 余额/欠款`。
 * 这是用户分辨「哪张卡」的唯一依据，尾号丢失或金额写错都会导致选错账户。
 */
import { describe, expect, it } from 'vitest'

import { accountLabel } from './account'

const base = { name: '招商储蓄卡', currency: 'CNY', balance: '23456.78' }

describe('accountLabel', () => {
  it('银行卡带尾号与余额', () => {
    expect(accountLabel({ ...base, card_masked: '尾号 2963' }, 2))
      .toBe('招商储蓄卡 · 尾号 2963 · 余额 23,456.78')
  })

  it('只有 card_last4 时也能拼出尾号', () => {
    expect(accountLabel({ ...base, card_last4: '0616' }, 2))
      .toBe('招商储蓄卡 · 尾号 0616 · 余额 23,456.78')
  })

  it('信用卡显示欠款而不是余额', () => {
    expect(accountLabel({
      name: '招商信用卡', currency: 'CNY', is_credit: true,
      balance: '-12345.67', credit_used: '12345.67', card_masked: '尾号 0616',
    }, 2)).toBe('招商信用卡 · 尾号 0616 · 欠 12,345.67')
  })

  it('信用卡没欠款时显示「无欠款」，不显示 0 元', () => {
    expect(accountLabel({
      name: '招商信用卡', currency: 'CNY', is_credit: true, credit_used: '0',
    }, 2)).toBe('招商信用卡 · 无欠款')
  })

  it('外币账户在没有尾号时补币种', () => {
    expect(accountLabel({ name: '花旗美元', currency: 'USD', balance: '100' }, 2))
      .toBe('花旗美元 · USD · 余额 100.00')
  })

  it('人民币账户不重复标 CNY', () => {
    expect(accountLabel({ ...base }, 2)).toBe('招商储蓄卡 · 余额 23,456.78')
  })

  it('withAmount=false 只给名称与尾号（用于不需要金额的紧凑列表）', () => {
    expect(accountLabel({ ...base, card_masked: '尾号 2963' }, 2, false))
      .toBe('招商储蓄卡 · 尾号 2963')
  })

  it('精度跟随家庭设置（0 位时不显示小数）', () => {
    expect(accountLabel({ ...base, balance: '23456.78' }, 0)).toBe('招商储蓄卡 · 余额 23,457')
  })

  it('余额缺失时不硬塞「余额 undefined」', () => {
    expect(accountLabel({ name: '现金', currency: 'CNY' }, 2)).toBe('现金')
  })
})
