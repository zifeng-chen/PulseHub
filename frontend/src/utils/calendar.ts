/**
 * 日历工具：日期选择器 / 期间选择器共用（纯函数，无第三方依赖）。
 * 统一按浏览器本地时区处理「哪一天」，与后端 +08:00 的展示口径保持一致。
 */

export const WEEK_LABELS = ['一', '二', '三', '四', '五', '六', '日'] as const

export function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

export function today(): Date {
  return startOfDay(new Date())
}

export function addDays(d: Date, days: number): Date {
  const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  copy.setDate(copy.getDate() + days)
  return copy
}

export function addMonths(d: Date, months: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + months, 1)
}

export function sameDay(a: Date | null, b: Date | null): boolean {
  if (!a || !b) return false
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

export function toYmd(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

export function toYm(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`
}

export function toHm(d: Date): string {
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** 解析 YYYY-MM-DD / YYYY-MM / ISO 日期时间；非法值返回 null。 */
export function parseDay(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = String(value).trim()
  const m = text.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/)
  if (m) {
    const year = Number(m[1])
    const month = Number(m[2]) - 1
    const day = m[3] ? Number(m[3]) : 1
    if (month < 0 || month > 11 || day < 1 || day > 31) return null
    const d = new Date(year, month, day)
    // 防止 2026-02-31 这类溢出值被 Date 静默修正
    if (d.getFullYear() !== year || d.getMonth() !== month || d.getDate() !== day) return null
    return d
  }
  const parsed = new Date(text)
  return Number.isNaN(parsed.getTime()) ? null : startOfDay(parsed)
}

/**
 * 解析任意日期时间：带时区的 ISO（后端 UTC）要换算成本地日期，
 * 不能直接截取字符串前 10 位，否则东八区会差一天。
 */
export function parseMoment(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = String(value).trim()
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(text)) {
    const parsed = new Date(text)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }
  // 后端业务时间是「本地墙钟」且不带时区后缀（2026-10-15T12:00:00），
  // 直接按本地解析，不能只截日期，否则时间会丢。
  if (text.includes('T') || text.includes(' ')) {
    const parsed = new Date(text.replace(' ', 'T'))
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }
  return parseDay(text)
}

/** 42 格月视图（周一开头），含上下月补位。 */
export function monthGrid(year: number, month: number): { date: Date; inMonth: boolean }[] {
  const first = new Date(year, month, 1)
  const offset = (first.getDay() + 6) % 7 // 周一 = 0
  const start = addDays(first, -offset)
  const cells: { date: Date; inMonth: boolean }[] = []
  for (let i = 0; i < 42; i += 1) {
    const date = addDays(start, i)
    cells.push({ date, inMonth: date.getMonth() === month && date.getFullYear() === year })
  }
  return cells
}

export function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate()
}

export function fmtDisplay(d: Date, withTime = false, time = ''): string {
  const base = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
  return withTime ? `${base} ${time || toHm(d)}` : base
}

export function monthLabel(year: number, month: number): string {
  return `${year} 年 ${month + 1} 月`
}

/** 本地时区偏移（+08:00 这类），随设备时区变化，不写死东八区。 */
export function offsetSuffix(d: Date): string {
  const minutes = -d.getTimezoneOffset()
  const sign = minutes >= 0 ? '+' : '-'
  const abs = Math.abs(minutes)
  return `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`
}

/**
 * 把后端返回的任意时间值转成选择器可用的「账本墙钟」时间（不带时区偏移）。
 * 不带偏移是有意的：业务时间本身就是账本时区的墙钟值，带上设备偏移会在
 * 回传时被后端再折算一次，导致跨时区使用时时间整体偏移。
 */
export function toLocalDateTimeValue(value: string | null | undefined): string {
  const d = parseMoment(value)
  if (!d) return ''
  return `${fmtDisplay(d)}T${toHm(d)}:00`
}

/** 把后端返回的任意时间值转成本地日期（YYYY-MM-DD）。 */
export function toLocalDay(value: string | null | undefined): string {
  const d = parseMoment(value)
  return d ? fmtDisplay(d) : ''
}

export function weekDayLabel(d: Date): string {
  return `周${WEEK_LABELS[(d.getDay() + 6) % 7]}`
}
