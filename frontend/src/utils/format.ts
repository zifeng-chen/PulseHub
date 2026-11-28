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


/**
 * 日期工具：一律按「本地墙钟」处理。
 *
 * 后端返回的业务时间是本地 naive ISO（如 2026-11-27T00:00:00），所以取前 10 位就是当天；
 * 不要用 `new Date(...).toISOString()` 再截断——那是 UTC，东八区凌晨会差一天。
 */

/** 表单/接口用的日期：YYYY-MM-DD（本地） */
export function toDateInput(value: Date | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'string') {
    // 已经是 ISO 或 YYYY-MM-DD：直接取日期部分，避免时区换算
    const matched = /^(\d{4}-\d{2}-\d{2})/.exec(value)
    if (matched) return matched[1]
  }
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

/** 展示用日期：YYYY-MM-DD；空值显示 — */
export function formatDate(value: Date | string | null | undefined): string {
  return toDateInput(value) || '—'
}

/** 展示用日期时间：YYYY-MM-DD HH:mm */
export function formatDateTime(value: Date | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') {
    const matched = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value)
    if (matched) return `${matched[1]} ${matched[2]}`
  }
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`
}
