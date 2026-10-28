/**
 * 请求时序守卫：快速切换筛选时，慢响应的旧请求不能覆盖新结果。
 * 这层逻辑错了会表现为「列表显示的是上一个账户的数据」，排查成本很高，所以单独钉住。
 */
import { describe, expect, it } from 'vitest'

import { useLatestRequest } from './useLatestRequest'

describe('useLatestRequest', () => {
  it('只有最后一次 begin 的令牌是当前令牌', () => {
    const latest = useLatestRequest()
    const first = latest.begin()
    expect(latest.isCurrent(first)).toBe(true)

    const second = latest.begin()
    expect(latest.isCurrent(second)).toBe(true)
    expect(latest.isCurrent(first)).toBe(false)
  })

  it('旧请求晚返回时被判定为过期（模拟乱序响应）', async () => {
    const latest = useLatestRequest()
    const slow = latest.begin()
    const fast = latest.begin()

    const results: string[] = []
    const request = (token: number, value: string, delay: number) =>
      new Promise<void>((resolve) => {
        setTimeout(() => {
          if (latest.isCurrent(token)) results.push(value)
          resolve()
        }, delay)
      })

    // 先发后到（慢），后发先到（快）
    await Promise.all([request(slow, '旧结果', 30), request(fast, '新结果', 5)])
    expect(results).toEqual(['新结果'])
  })

  it('各自实例互不干扰（不同组件各自守卫自己的请求）', () => {
    const a = useLatestRequest()
    const b = useLatestRequest()
    const tokenA = a.begin()
    b.begin()
    expect(a.isCurrent(tokenA)).toBe(true)
  })
})
