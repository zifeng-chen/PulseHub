/**
 * 流水筛选 ↔ URL 的转换（纯函数）。
 * 这层逻辑一旦出错，用户会看到「地址栏是 A、列表是 B」，所以按边界情况逐个钉住。
 */
import { describe, expect, it } from 'vitest'

import {
  FILTER_KEYS,
  buildFilterQuery,
  emptyFilters,
  filterStateKey,
  filtersFromQuery,
  queryStateKey,
} from './filter-query'

describe('filtersFromQuery', () => {
  it('只认识白名单字段，其他 query 参数（如 new、tab）不会混进筛选', () => {
    const state = filtersFromQuery({ status: 'draft', new: '1', tab: 'transfers' })
    expect(state.status).toBe('draft')
    expect(Object.keys(state).sort()).toEqual([...FILTER_KEYS].sort())
    expect(state).not.toHaveProperty('new')
    expect(state).not.toHaveProperty('tab')
  })

  it('缺省字段补空串，非字符串值（数组、null）一律当空，避免 ?a=1&a=2 脏值', () => {
    const state = filtersFromQuery({ status: ['draft', 'confirmed'], account_id: null, keyword: '买菜' })
    expect(state.status).toBe('')
    expect(state.account_id).toBe('')
    expect(state.keyword).toBe('买菜')
    expect(state.amount_min).toBe('')
  })

  it('空 query 得到全空筛选', () => {
    expect(filtersFromQuery({})).toEqual(emptyFilters())
  })
})

describe('buildFilterQuery', () => {
  it('默认值不写进 URL：空条件与第 1 页都不出现', () => {
    expect(buildFilterQuery(emptyFilters(), 1)).toEqual({})
  })

  it('只写生效的条件，页码大于 1 才写 page', () => {
    const query = buildFilterQuery({ ...emptyFilters(), status: 'draft', keyword: '房租' }, 3)
    expect(query).toEqual({ status: 'draft', keyword: '房租', page: '3' })
  })

  it('金额区间与日期区间作为普通字符串保留', () => {
    const query = buildFilterQuery(
      { ...emptyFilters(), date_from: '2026-10-01', date_to: '2026-10-31', amount_min: '100' },
      1,
    )
    expect(query).toEqual({ date_from: '2026-10-01', date_to: '2026-10-31', amount_min: '100' })
  })
})

describe('状态比较（避免无谓的 router.replace）', () => {
  it('URL 与筛选一致时两个 key 相等', () => {
    const filters = { ...emptyFilters(), status: 'confirmed' }
    expect(filterStateKey(filters, 2)).toBe(queryStateKey({ status: 'confirmed', page: '2' }))
  })

  it('顺序不同但内容相同视为一致（不被对象键序干扰）', () => {
    const a = queryStateKey({ status: 'draft', keyword: 'x' })
    const b = queryStateKey({ keyword: 'x', status: 'draft' })
    expect(a).toBe(b)
  })

  it('页码差异会被识别出来', () => {
    expect(queryStateKey({ status: 'draft', page: '2' })).not.toBe(queryStateKey({ status: 'draft' }))
  })

  it('脏值（数组、非法页码）不会被误判为一致', () => {
    expect(queryStateKey({ status: ['draft'], page: 'abc' })).toBe(
      queryStateKey({}),
    )
  })
})
