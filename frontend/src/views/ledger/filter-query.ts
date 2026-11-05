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

/** 每页条数可选项：流水列表只允许这三档，避免一次拉太多拖慢渲染 */
export const PAGE_SIZE_OPTIONS = [20, 30, 50] as const
export const DEFAULT_PAGE_SIZE = 20

/** 把 URL / 表单里的脏值归一成合法档位；不认识的一律回落到默认 20 */
export function normalizePageSize(raw: unknown): number {
  const n = Number(raw)
  return (PAGE_SIZE_OPTIONS as readonly number[]).includes(n) ? n : DEFAULT_PAGE_SIZE
}

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

/** 筛选 + 页码 + 每页条数 → URL query：默认值不写，保持链接干净（第 1 页、20 条/页都不出现） */
export function buildFilterQuery(
  filters: FilterState,
  page: number,
  pageSize: number = DEFAULT_PAGE_SIZE,
): Record<string, string> {
  const query: Record<string, string> = {}
  for (const key of FILTER_KEYS) {
    if (filters[key]) query[key] = filters[key]
  }
  if (page > 1) query.page = String(page)
  if (pageSize !== DEFAULT_PAGE_SIZE) query.page_size = String(pageSize)
  return query
}

/** 规范化后的状态串：用于比较「当前筛选」与「地址栏」是否已经一致，避免无谓的 replace */
export function filterStateKey(
  filters: FilterState,
  page: number,
  pageSize: number = DEFAULT_PAGE_SIZE,
): string {
  return JSON.stringify(buildFilterQuery(filters, page, pageSize))
}

/** 规范化后的 query 串：与 filterStateKey 口径相同，可直接比较 */
export function queryStateKey(query: Record<string, unknown>): string {
  const page = Number(query.page)
  return JSON.stringify(buildFilterQuery(
    filtersFromQuery(query),
    Number.isFinite(page) ? page : 1,
    normalizePageSize(query.page_size),
  ))
}
