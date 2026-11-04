import { describe, expect, it } from 'vitest'
import { focusLink } from './use-focus-targets'

/** 从生成的地址里读回 focus，避免直接断言 URL 编码细节（逗号会被编码成 %2C） */
function focusOf(url: string): string | null {
  return new URL(url, 'http://localhost').searchParams.get('focus')
}

describe('focusLink', () => {
  it('没有 focus 时原样返回，不额外加问号', () => {
    expect(focusLink('/ledger/assets', '')).toBe('/ledger/assets')
    expect(focusLink('/ledger/assets', null)).toBe('/ledger/assets')
    expect(focusLink('/ledger/assets', undefined)).toBe('/ledger/assets')
  })

  it('单条目标拼成 ?focus=id', () => {
    expect(focusLink('/ledger/liabilities', 'li-1')).toBe('/ledger/liabilities?focus=li-1')
  })

  it('多条目标用逗号连接（缺口可能同时命中多条）', () => {
    expect(focusOf(focusLink('/ledger/assets', 'a1,a2,a3'))).toBe('a1,a2,a3')
  })

  it('保留目标页已有的查询参数', () => {
    const url = focusLink('/ledger/assets?tab=vehicle', 'a1')
    expect(url.startsWith('/ledger/assets?')).toBe(true)
    expect(new URL(url, 'http://localhost').searchParams.get('tab')).toBe('vehicle')
    expect(focusOf(url)).toBe('a1')
  })

  it('清理空白与空段，避免拼出 ?focus=,,', () => {
    expect(focusOf(focusLink('/ledger/assets', ' a1 ,, a2 '))).toBe('a1,a2')
    expect(focusLink('/ledger/assets', ' , ')).toBe('/ledger/assets')
  })

  it('目标数超过 60 时截断（后端也会截，这里再兜一层，避免整屏高亮）', () => {
    const ids = Array.from({ length: 80 }, (_, i) => `id-${i}`)
    expect(focusOf(focusLink('/ledger/assets', ids.join(',')))?.split(',')).toHaveLength(60)
  })
})
