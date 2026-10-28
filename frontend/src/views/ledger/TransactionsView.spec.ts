/**
 * 流水页的筛选集成测试：挂真实组件、只 mock 接口与路由。
 *
 * 这里盯的是「改一次筛选，只发一次请求」和「URL 参数原样带进首次请求」——
 * 这两点坏了不会报错，只会让列表抖动或显示别的账户的数据。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const route: { query: Record<string, unknown> } = { query: {} }
  const replace = vi.fn(async (loc: { query?: Record<string, unknown> }) => {
    route.query = { ...(loc.query || {}) }
  })
  return { route, replace }
})

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ replace: mocks.replace, push: vi.fn() }),
}))

vi.mock('@/api/client', () => {
  const get = vi.fn()
  return {
    api: { get, post: vi.fn(), put: vi.fn(), delete: vi.fn() },
    errorMessage: (err: unknown) => String(err),
    errorFields: () => ({}),
  }
})

import { api } from '@/api/client'
import TransactionsView from './TransactionsView.vue'

const eventRow = (id: string, title: string) => ({
  id,
  event_no: id,
  event_type: 'expense',
  event_type_label: '支出',
  title,
  amount: '100.00',
  currency: 'CNY',
  occurred_at: '2026-10-01T10:00:00',
  status: 'confirmed' as string,
  status_label: '已确认',
  flow_direction_label: '支出',
  category_name: '—',
  source: 'manual',
  account_id: 'acc-1',
  account_name: '招商储蓄卡',
  category_id: null,
  category_label: '—',
  member_id: null,
  note: null,
  repayment: null,
  transfer_group_id: null,
})

/** 按 URL 返回不同的列表，便于断言「请求参数里带了什么」 */
function wireApi(rows: ReturnType<typeof eventRow>[]) {
  vi.mocked(api.get).mockImplementation((url: string, params?: Record<string, unknown>) => {
    if (url === '/ledger/events') {
      const filtered = params?.status ? rows.filter((r) => r.status === params.status) : rows
      return Promise.resolve({ items: filtered, total: filtered.length }) as never
    }
    if (url === '/ledger/transfers') return Promise.resolve({ items: [], total: 0 }) as never
    if (url === '/ledger/accounts') return Promise.resolve({ items: [] }) as never
    if (url === '/ledger/categories') return Promise.resolve({ items: [] }) as never
    if (url === '/ledger/assets') return Promise.resolve({ items: [] }) as never
    if (url === '/ledger/liabilities') return Promise.resolve({ items: [] }) as never
    if (url === '/settings/household') return Promise.resolve({ members: [] }) as never
    return Promise.resolve({ items: [] }) as never
  })
}

function eventCalls() {
  return vi.mocked(api.get).mock.calls.filter(([url]) => url === '/ledger/events')
}

async function mountView(query: Record<string, unknown> = {}) {
  mocks.route.query = { ...query }
  mocks.replace.mockClear()
  const wrapper = mount(TransactionsView, {
    global: { plugins: [createPinia()] },
    attachTo: document.body,
  })
  // 等 loadRefs + load 全部落地
  await vi.waitFor(() => expect(eventCalls().length).toBeGreaterThan(0))
  await wrapper.vm.$nextTick()
  await new Promise((r) => setTimeout(r, 0))
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(api.get).mockReset()
})

describe('流水页筛选', () => {
  it('从 URL 进入时，首个请求就带上筛选参数（不是先查全部再过滤）', async () => {
    wireApi([{ ...eventRow('e1', '草稿'), status: 'draft' } as never])
    await mountView({ status: 'draft' })

    const calls = eventCalls()
    expect(calls).toHaveLength(1)
    expect(calls[0][1]).toMatchObject({ status: 'draft' })
  })

  it('首屏只请求一次，不会因为水合再补一刀', async () => {
    wireApi([eventRow('e1', '甲') as never, eventRow('e2', '乙') as never])
    await mountView()
    expect(eventCalls()).toHaveLength(1)
  })

  it('改状态筛选：只发一次新请求，并把参数写回 URL', async () => {
    wireApi([
      { ...eventRow('e1', '甲'), status: 'confirmed' } as never,
      { ...eventRow('e2', '乙'), status: 'draft' } as never,
    ])
    const wrapper = await mountView()
    const before = eventCalls().length

    const statusSelect = wrapper
      .findAll('select')
      .find((s) => s.findAll('option').some((o) => o.text().includes('全部状态')))
    expect(statusSelect).toBeTruthy()
    await statusSelect!.setValue('confirmed')
    await vi.waitFor(() => expect(eventCalls().length).toBe(before + 1))

    const last = eventCalls().at(-1)!
    expect(last[1]).toMatchObject({ status: 'confirmed', page: 1 })
    expect(mocks.replace).toHaveBeenCalledTimes(1)
    expect(mocks.replace.mock.calls[0][0]).toMatchObject({ query: { status: 'confirmed' } })
  })

  it('重置：清空条件、回到第 1 页，URL 里不留筛选参数', async () => {
    wireApi([eventRow('e1', '甲') as never])
    const wrapper = await mountView({ status: 'draft', keyword: '房租' })

    const reset = wrapper.findAll('button').find((b) => b.text().trim() === '重置')
    expect(reset).toBeTruthy()
    await reset!.trigger('click')
    await vi.waitFor(() => expect(mocks.replace).toHaveBeenCalled())

    const last = eventCalls().at(-1)!
    expect(last[1]).toMatchObject({ page: 1 })
    expect((last[1] as Record<string, unknown>).status).toBeUndefined()
    expect((last[1] as Record<string, unknown>).keyword).toBeUndefined()
    expect(mocks.replace.mock.calls.at(-1)![0]).toMatchObject({ query: {} })
  })
})
