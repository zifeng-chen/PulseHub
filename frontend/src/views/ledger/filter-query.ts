/**
 * 流水筛选 ↔ URL 的纯函数（不依赖组件，便于单测）。
 *
 * 为什么要进 URL：筛选条件代表「当前看到的是哪一屏数据」。只放组件里的话，
 * 刷新即丢、链接不可分享、后退也回不去。这里把「状态 → query」和「query → 状态」
 * 双向转换收在一处，页面只负责调用，避免两边各写一份正则/默认值。
 */

export const FILTER_KEYS = [
  'direction',
  'account_id',
  'category_id',
  'status',
  'source',
  'keyword',
  'date_from',
  'date_to',
  'amount_min',
  'amount_max',
] as const

export type FilterKey = (typeof FILTER_KEYS)[number]
export type FilterState = Record<FilterKey, string>

/** 空筛选（所有条件都为空字符串，利于表单双向绑定） */
export function emptyFilters(): FilterState {
  const out = {} as FilterState
  for (const key of FILTER_KEYS) out[key] = ''
  return out
}

/** 从路由 query 读筛选：非字符串（数组、null）一律当空，避免 ?a=1&a=2 这类脏值 */
export function filtersFromQuery(query: Record<string, unknown>): FilterState {
  const out = emptyFilters()
  for (const key of FILTER_KEYS) {
    const raw = query[key]
    if (typeof raw === 'string') out[key] = raw
  }
  return out
}

/** 筛选 + 页码 → URL query：默认值不写，保持链接干净（第 1 页、空条件都不出现） */
export function buildFilterQuery(filters: FilterState, page: number): Record<string, string> {
  const query: Record<string, string> = {}
  for (const key of FILTER_KEYS) {
    if (filters[key]) query[key] = filters[key]
  }
  if (page > 1) query.page = String(page)
  return query
}

/** 规范化后的状态串：用于比较「当前筛选」与「地址栏」是否已经一致，避免无谓的 replace */
export function filterStateKey(filters: FilterState, page: number): string {
  return JSON.stringify(buildFilterQuery(filters, page))
}

/** 规范化后的 query 串：与 filterStateKey 口径相同，可直接比较 */
export function queryStateKey(query: Record<string, unknown>): string {
  const page = Number(query.page)
  return JSON.stringify(buildFilterQuery(filtersFromQuery(query), Number.isFinite(page) ? page : 1))
}
