/**
 * 金额文本格式化（03 §3、23 §1）。
 * 与 `MoneyText.vue` 同一口径：千分位 + 固定小数位，避免同一页面出现两种写法。
 */

export function formatAmount(
  value: string | number | null | undefined,
  precision: number,
): string {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  if (Number.isNaN(num)) return String(value)
  return num.toLocaleString('zh-CN', {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  })
}
