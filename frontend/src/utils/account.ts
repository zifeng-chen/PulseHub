import { formatAmount } from '@/utils/format'

/**
 * 账户下拉文案（全项目统一口径）：`名称 · 尾号 · 余额/欠款`。
 *
 * 为什么需要它：
 * - 同一家银行常有储蓄卡、信用卡多张，只显示名称根本分不清是哪一张，尾号是唯一稳定标识；
 * - 选择账户时用户往往需要看余额/欠款来决定用哪张，不该再跳去账户页查。
 *
 * 币种只在非人民币时补上，避免每行都拖一个「（CNY）」。
 */
export interface AccountLabelLike {
  name: string
  currency?: string | null
  /** 后端拼好的「尾号 0616」 */
  card_masked?: string | null
  card_last4?: string | null
  /** 信用类账户（有授信额度）：余额为负表示欠款 */
  is_credit?: boolean
  balance?: string | null
  credit_used?: string | null
}

export function accountLabel(
  a: AccountLabelLike,
  precision: number,
  withAmount = true,
): string {
  const bits: string[] = [a.name]
  const masked = a.card_masked || (a.card_last4 ? `尾号 ${a.card_last4}` : '')
  if (masked) bits.push(masked)
  else if (a.currency && a.currency !== 'CNY') bits.push(a.currency)

  if (withAmount) {
    if (a.is_credit) {
      const used = Number(a.credit_used || 0)
      bits.push(used > 0 ? `欠 ${formatAmount(a.credit_used, precision)}` : '无欠款')
    } else if (a.balance !== null && a.balance !== undefined && a.balance !== '') {
      bits.push(`余额 ${formatAmount(a.balance, precision)}`)
    }
  }
  return bits.join(' · ')
}
