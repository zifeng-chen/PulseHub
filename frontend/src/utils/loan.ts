/**
 * 贷款测算（纯函数）：建档时自动填「每期应还」，以及剩余期数的推导。
 *
 * 这些数字会直接落库成为月供、影响负债页与还款计划，所以口径必须固定：
 * - 本金优先「建档剩余本金」，没有才用贷款金额（与后端 base_outstanding 一致）；
 * - 期数优先「剩余期数」，没有才用合同期限换算；
 * - 利率按「年利率百分数」，月供按每期利率（年利率 ÷ 每年期数）。
 *
 * 注意：这里只做**录入时的建议值**，真实月供以银行账单为准，用户可手改。
 */

/** 每年有几期（按还款频率） */
export const PAYMENTS_PER_YEAR: Record<string, number> = {
  monthly: 12,
  quarterly: 4,
  half_yearly: 2,
  yearly: 1,
  weekly: 52,
  one_time: 1,
}

export function paymentsPerYear(frequency: string | null | undefined): number {
  return PAYMENTS_PER_YEAR[frequency || 'monthly'] ?? 12
}

/** 计算用的本金：建档剩余本金优先，其次贷款/合同金额 */
export function loanPrincipal(openingOutstanding: unknown, initialAmount: unknown): number {
  const opening = Number(openingOutstanding || 0)
  if (opening > 0) return opening
  const initial = Number(initialAmount || 0)
  return initial > 0 ? initial : 0
}

/** 计算用的期数：剩余期数优先，否则按合同月数换算成「每期」 */
export function loanPeriods(
  remainingPeriods: unknown,
  termMonths: unknown,
  frequency: string,
): number {
  const remaining = Number(remainingPeriods || 0)
  if (remaining > 0) return Math.round(remaining)
  const term = Number(termMonths || 0)
  if (!term) return 0
  const years = term / 12
  return Math.max(0, Math.round(years * paymentsPerYear(frequency)))
}

export interface PaymentInput {
  principal: number
  periods: number
  /** 年利率，百分数（4.9 表示 4.9%） */
  annualRatePercent: number | string | null | undefined
  /** equal_installment / equal_principal / interest_first / lump_sum / other */
  method: string
  frequency?: string
}

/**
 * 每期应还的建议值（未取整前的元）：
 * - equal_installment 等额本息：整期固定；
 * - equal_principal 等额本金：取第一期（之后逐期递减）；
 * - interest_first 先息后本：每期只还利息；
 * - lump_sum 一次还本付息：整笔合计（本金 + 利息总额）；
 * - other / 参数不合法：null（不猜）。
 */
export function suggestedPayment(input: PaymentInput): number | null {
  const { principal, periods, method } = input
  if (!Number.isFinite(principal) || principal <= 0) return null
  if (!Number.isFinite(periods) || periods <= 0) return null
  if (method === 'other' || !method) return null

  const perYear = paymentsPerYear(input.frequency)
  const rawRate = Number(input.annualRatePercent ?? 0)
  const rate = (Number.isFinite(rawRate) ? rawRate : 0) / 100 / perYear

  let payment = 0
  switch (method) {
    case 'equal_principal':
      payment = principal / periods + principal * rate
      break
    case 'interest_first':
      payment = principal * rate
      break
    case 'lump_sum':
      payment = principal + principal * rate * periods
      break
    default:
      payment = rate === 0
        ? principal / periods
        : (principal * rate * (1 + rate) ** periods) / ((1 + rate) ** periods - 1)
  }
  if (!Number.isFinite(payment) || payment <= 0) return null
  return payment
}
