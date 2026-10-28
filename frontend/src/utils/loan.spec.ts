/**
 * 贷款测算（录入建档时的「每期应还」建议值与期数换算）。
 *
 * 这里算错的后果很直接：月供估偏 → 负债页的还款计划、现金流压力全都跟着错。
 * 期望值用独立的 Decimal 计算过（100 万 / 30 年 / 4.9% → 5307.27 等）。
 */
import { describe, expect, it } from 'vitest'

import { loanPeriods, loanPrincipal, paymentsPerYear, suggestedPayment } from './loan'

describe('paymentsPerYear', () => {
  it('按还款频率给出每年期数', () => {
    expect(paymentsPerYear('monthly')).toBe(12)
    expect(paymentsPerYear('quarterly')).toBe(4)
    expect(paymentsPerYear('half_yearly')).toBe(2)
    expect(paymentsPerYear('yearly')).toBe(1)
    expect(paymentsPerYear('weekly')).toBe(52)
    expect(paymentsPerYear('one_time')).toBe(1)
  })

  it('未知频率按月度处理，不返回 undefined', () => {
    expect(paymentsPerYear('')).toBe(12)
    expect(paymentsPerYear(null)).toBe(12)
    expect(paymentsPerYear('随便写')).toBe(12)
  })
})

describe('loanPrincipal：建档剩余本金优先', () => {
  it('有建档剩余本金时用它（存量贷款的真实剩余）', () => {
    expect(loanPrincipal('400000', '1000000')).toBe(400000)
  })

  it('没有剩余本金时退回贷款金额', () => {
    expect(loanPrincipal(null, '1000000')).toBe(1000000)
    expect(loanPrincipal('', '800000')).toBe(800000)
  })

  it('都没有则为 0，绝不返回 NaN', () => {
    expect(loanPrincipal(null, null)).toBe(0)
    expect(loanPrincipal('abc', undefined)).toBe(0)
  })
})

describe('loanPeriods：剩余期数优先，否则按月数换算', () => {
  it('填了剩余期数就按它算（与银行账单一致）', () => {
    expect(loanPeriods(18, 120, 'monthly')).toBe(18)
  })

  it('没填剩余期数时按合同月数换算', () => {
    expect(loanPeriods(null, 360, 'monthly')).toBe(360)
    expect(loanPeriods(0, 24, 'monthly')).toBe(24)
  })

  it('按频率换算：季度还款 10 年 = 40 期', () => {
    expect(loanPeriods(null, 120, 'quarterly')).toBe(40)
  })

  it('缺期限返回 0', () => {
    expect(loanPeriods(null, 0, 'monthly')).toBe(0)
    expect(loanPeriods(null, null, 'monthly')).toBe(0)
  })
})

describe('suggestedPayment：四种还款方式', () => {
  const base = { principal: 1_000_000, periods: 360, annualRatePercent: '4.9', frequency: 'monthly' }

  it('等额本息：100 万 / 30 年 / 4.9% → 5307.27', () => {
    const pay = suggestedPayment({ ...base, method: 'equal_installment' })!
    expect(pay.toFixed(2)).toBe('5307.27')
  })

  it('等额本息：20 万 / 5 年 / 3.6% → 3647.31', () => {
    const pay = suggestedPayment({
      principal: 200_000, periods: 60, annualRatePercent: '3.6', method: 'equal_installment',
    })!
    expect(pay.toFixed(2)).toBe('3647.31')
  })

  it('等额本金：取第一期（本金 + 首期利息）', () => {
    const pay = suggestedPayment({ ...base, method: 'equal_principal' })!
    // 1000000/360 + 1000000*0.049/12 = 2777.78 + 4083.33
    expect(pay.toFixed(2)).toBe('6861.11')
  })

  it('先息后本：每期只还利息', () => {
    const pay = suggestedPayment({ ...base, method: 'interest_first' })!
    expect(pay.toFixed(2)).toBe('4083.33')
  })

  it('一次还本付息：本金 + 本金×年利率×年数 = 100万 + 147万', () => {
    const pay = suggestedPayment({ ...base, method: 'lump_sum' })!
    expect(pay.toFixed(2)).toBe('2470000.00')
  })

  it('零利率时等额本息退化为本金均摊', () => {
    const pay = suggestedPayment({ ...base, annualRatePercent: '0', method: 'equal_installment' })!
    expect(pay.toFixed(2)).toBe('2777.78')
  })

  it('零利率时先息后本没有应还金额（返回 null，不显示 0 元）', () => {
    expect(suggestedPayment({ ...base, annualRatePercent: 0, method: 'interest_first' })).toBeNull()
  })

  it('还款方式为「其他」或为空时不给建议值', () => {
    expect(suggestedPayment({ ...base, method: 'other' })).toBeNull()
    expect(suggestedPayment({ ...base, method: '' })).toBeNull()
  })

  it('本金或期数不合法时返回 null', () => {
    expect(suggestedPayment({ ...base, principal: 0, method: 'equal_installment' })).toBeNull()
    expect(suggestedPayment({ ...base, principal: -100, method: 'equal_installment' })).toBeNull()
    expect(suggestedPayment({ ...base, periods: 0, method: 'equal_installment' })).toBeNull()
  })

  it('利率缺失按 0 处理，不产生 NaN', () => {
    const pay = suggestedPayment({ ...base, annualRatePercent: null, method: 'equal_installment' })!
    expect(Number.isFinite(pay)).toBe(true)
    expect(pay.toFixed(2)).toBe('2777.78')
  })
})
