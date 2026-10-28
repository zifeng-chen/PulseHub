/**
 * 日历与时间口径（纯函数）。
 *
 * 这层最容易出「差一天」的静默错误：后端给 UTC，账本按本地墙钟。
 * 测试固定 TZ=Asia/Shanghai（见 vite.config 的 test.env），断言才有意义。
 */
import { describe, expect, it } from 'vitest'

import {
  addDays,
  addMonths,
  daysInMonth,
  fmtDisplay,
  monthGrid,
  offsetSuffix,
  pad2,
  parseDay,
  parseMoment,
  sameDay,
  startOfDay,
  toLocalDateTimeValue,
  toLocalDay,
  toHm,
  toYm,
  toYmd,
  weekDayLabel,
} from './calendar'

describe('基础格式', () => {
  it('pad2 补零', () => {
    expect(pad2(3)).toBe('03')
    expect(pad2(12)).toBe('12')
  })

  it('toYmd / toYm / toHm 按本地时区取值', () => {
    const d = new Date(2026, 9, 5, 8, 7)
    expect(toYmd(d)).toBe('2026-10-05')
    expect(toYm(d)).toBe('2026-10')
    expect(toHm(d)).toBe('08:07')
  })

  it('weekDayLabel 以周一为一周起点', () => {
    expect(weekDayLabel(new Date(2026, 9, 5))).toBe('周一')
    expect(weekDayLabel(new Date(2026, 9, 11))).toBe('周日')
  })
})

describe('日期运算', () => {
  it('addDays 跨月与闰年都正确', () => {
    expect(toYmd(addDays(new Date(2026, 9, 31), 1))).toBe('2026-11-01')
    expect(toYmd(addDays(new Date(2024, 1, 28), 1))).toBe('2024-02-29')
    expect(toYmd(addDays(new Date(2026, 0, 1), -1))).toBe('2025-12-31')
  })

  it('addMonths 归一到当月 1 号，不会因为 31 号溢出到后一个月', () => {
    expect(toYmd(addMonths(new Date(2026, 0, 31), 1))).toBe('2026-02-01')
    expect(toYmd(addMonths(new Date(2026, 0, 15), -1))).toBe('2025-12-01')
  })

  it('daysInMonth 认得闰年二月', () => {
    expect(daysInMonth(2024, 1)).toBe(29)
    expect(daysInMonth(2026, 1)).toBe(28)
    expect(daysInMonth(2026, 9)).toBe(31)
  })

  it('startOfDay 清掉时分秒；sameDay 只比日期', () => {
    const d = startOfDay(new Date(2026, 9, 5, 23, 59, 59))
    expect(d.getHours()).toBe(0)
    expect(sameDay(d, new Date(2026, 9, 5, 0, 0))).toBe(true)
    expect(sameDay(d, new Date(2026, 9, 6))).toBe(false)
    expect(sameDay(null, d)).toBe(false)
  })
})

describe('parseDay：只认合法日期，非法值不静默修正', () => {
  it('YYYY-MM-DD 与 YYYY-MM', () => {
    expect(toYmd(parseDay('2026-10-05')!)).toBe('2026-10-05')
    expect(toYmd(parseDay('2026-10')!)).toBe('2026-10-01')
  })

  it('2026-02-31 这类溢出日期返回 null（而不是被 Date 修正成 3 月 3 日）', () => {
    expect(parseDay('2026-02-31')).toBeNull()
    expect(parseDay('2026-13-01')).toBeNull()
  })

  it('空值与垃圾串返回 null', () => {
    expect(parseDay('')).toBeNull()
    expect(parseDay(null)).toBeNull()
    expect(parseDay('随便写的')).toBeNull()
  })
})

describe('parseMoment：UTC 要换算成本地，墙钟时间不能丢时刻', () => {
  it('带 Z 的 UTC 时间换算到东八区会进到第二天（不能截前 10 位）', () => {
    const d = parseMoment('2026-10-14T16:30:00Z')!
    expect(toYmd(d)).toBe('2026-10-15')
    expect(toHm(d)).toBe('00:30')
  })

  it('带 +08:00 偏移的时间按本地读', () => {
    const d = parseMoment('2026-10-15T09:00:00+08:00')!
    expect(toYmd(d)).toBe('2026-10-15')
    expect(toHm(d)).toBe('09:00')
  })

  it('后端墙钟时间（无后缀）保留时分，不被截成 0 点', () => {
    const d = parseMoment('2026-10-15T12:34:00')!
    expect(toYmd(d)).toBe('2026-10-15')
    expect(toHm(d)).toBe('12:34')
  })

  it('空格分隔的时间也认', () => {
    expect(toHm(parseMoment('2026-10-15 08:05:00')!)).toBe('08:05')
  })
})

describe('转成选择器/展示用的字符串', () => {
  it('toLocalDateTimeValue 输出本地墙钟且不带偏移', () => {
    expect(toLocalDateTimeValue('2026-10-14T16:30:00Z')).toBe('2026-10-15T00:30:00')
    expect(toLocalDateTimeValue('2026-10-15T12:34:00')).toBe('2026-10-15T12:34:00')
    expect(toLocalDateTimeValue(null)).toBe('')
  })

  it('toLocalDay 只给日期', () => {
    expect(toLocalDay('2026-10-14T16:30:00Z')).toBe('2026-10-15')
    expect(toLocalDay('')).toBe('')
  })

  it('fmtDisplay 可带时间，未给时间时用当前时分', () => {
    const d = new Date(2026, 9, 5, 8, 7)
    expect(fmtDisplay(d)).toBe('2026-10-05')
    expect(fmtDisplay(d, true)).toBe('2026-10-05 08:07')
    expect(fmtDisplay(d, true, '21:30')).toBe('2026-10-05 21:30')
  })

  it('offsetSuffix 跟随设备时区，东八区为 +08:00', () => {
    expect(offsetSuffix(new Date(2026, 9, 5))).toBe('+08:00')
  })
})

describe('月视图', () => {
  it('固定 42 格，首格是周一', () => {
    const cells = monthGrid(2026, 9) // 2026-10，1 号是周四
    expect(cells).toHaveLength(42)
    expect(cells[0].date.getDay()).toBe(1)
    expect(toYmd(cells[0].date)).toBe('2026-09-28')
  })

  it('本月与补位月份标记正确', () => {
    const cells = monthGrid(2026, 9)
    const inMonth = cells.filter((c) => c.inMonth)
    expect(inMonth).toHaveLength(31)
    expect(toYmd(inMonth[0].date)).toBe('2026-10-01')
    expect(toYmd(inMonth.at(-1)!.date)).toBe('2026-10-31')
  })
})
