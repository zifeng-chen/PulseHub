/**
 * 资产模块共享类型与工具（06 §2、15）。
 * 金额一律由后端计算与格式化，前端只负责展示与提交。
 */

import { api } from '@/api/client'
import { zh } from '@/i18n'

export interface AssetRow {
  id: string
  name: string
  asset_type: string
  asset_type_label: string
  sub_type: string | null
  category_id: string | null
  category_label: string
  owner_member_id: string | null
  currency: string
  acquire_date: string | null
  acquire_cost: string
  current_value: string
  value_source: string | null
  value_source_label: string
  last_valuation_date: string | null
  status: string
  status_label: string
  include_in_net_worth: boolean
  linked_account_id: string | null
  linked_liability_id: string | null
  depreciation_method: string | null
  depreciation_method_label: string
  maintenance_date: string | null
  liquidity: string
  liquidity_label: string
  counts_in_net_worth: boolean
  custom_attributes: Record<string, unknown> | null
  note: string | null
  version: number
  pending_dependency_note: string
  extension?: Record<string, unknown> | null
}

export interface AssetSummary {
  base_currency: string
  count: number
  total_value: string
  current_value: string
  non_current_value: string
  pending_valuation_count: number
  by_currency: { currency: string; total: string; current: string; non_current: string; count: number; pending_valuation: number }[]
  multi_currency: boolean
  caliber: Record<string, string>
}

export interface Member {
  id: string
  display_name: string
  role: string
  role_label?: string
}

export interface AccountLite {
  id: string
  name: string
  currency: string
  status: string
}

export interface AssetCategory {
  id: string
  name: string
  status: string
  parent_id?: string | null
}

export interface AssetListResponse {
  items: AssetRow[]
  total: number
  page: number
  page_size: number
  has_next: boolean
  summary?: AssetSummary
  caliber?: Record<string, string>
  module_notes?: Record<string, string>
}

export interface ValuationRow {
  id: string
  value: string
  currency: string
  valued_at: string | null
  source: string
  source_label: string
  source_note: string | null
  reason: string | null
  note: string | null
  created_at: string
}

export interface AssetEventRow {
  id: string
  event_no: string
  event_type: string
  event_type_label: string
  status: string
  status_label: string
  title: string
  amount: string
  currency: string
  occurred_at: string | null
  realized_gain: string | null
  movements: { id: string; target_type: string; direction: string; direction_label: string; amount: string; currency: string }[]
}

export interface AssetDetail extends AssetRow {
  valuations: ValuationRow[]
  events: AssetEventRow[]
  attachments: { id: string; file_name: string; mime_type: string; size_bytes: number; status: string }[]
  related: Record<string, unknown>
  ai: { allowed: string[]; forbidden: string[] }
}

/** 下拉选项构造：value + 中文标签（来自 /meta/enums 或内置字典） */
export function optionsOf(namespace: string, values: string[], allLabel?: string) {
  const opts = values.map((v) => ({ value: v, label: zh(namespace, v) }))
  return allLabel ? [{ value: '', label: allLabel }, ...opts] : opts
}

export const ASSET_TYPE_VALUES = ['real_estate', 'parking', 'vehicle', 'precious_metal', 'other']
export const ASSET_STATUS_VALUES = ['active', 'rented', 'idle', 'mortgaged', 'disposed', 'scrapped', 'archived']
export const CURRENCY_OPTIONS = [
  { value: 'CNY', label: '人民币 CNY' },
  { value: 'USD', label: '美元 USD' },
  { value: 'HKD', label: '港币 HKD' },
  { value: 'EUR', label: '欧元 EUR' },
]

export function fmtDate(v: string | null | undefined) {
  if (!v) return '—'
  return new Date(v).toLocaleDateString('zh-CN')
}

export function fmtDateTime(v: string | null | undefined) {
  if (!v) return '—'
  return new Date(v).toLocaleString('zh-CN')
}

export function statusTone(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'active') return 'success'
  if (status === 'rented' || status === 'mortgaged') return 'warning'
  if (status === 'disposed' || status === 'scrapped') return 'danger'
  return 'neutral'
}

/** 各类型专属页签（06 §2、15 §3.5/§4.3/§5.5/§6.3） */
export const ASSET_TABS: Record<string, { key: string; label: string }[]> = {
  real_estate: [
    { key: 'overview', label: '概览' },
    { key: 'profile', label: '产权与基础资料' },
    { key: 'mortgage', label: '房贷' },
    { key: 'valuations', label: '估值历史' },
    { key: 'leases', label: '租赁' },
    { key: 'utility', label: '水电煤户号' },
    { key: 'events', label: '费用流水' },
    { key: 'attachments', label: '文件' },
    { key: 'ai', label: 'AI 分析' },
  ],
  parking: [
    { key: 'overview', label: '概览' },
    { key: 'profile', label: '基础资料' },
    { key: 'linked', label: '所属不动产' },
    { key: 'events', label: '关联事件' },
    { key: 'valuations', label: '估值' },
    { key: 'attachments', label: '附件' },
    { key: 'ai', label: 'AI 分析' },
  ],
  vehicle: [
    { key: 'overview', label: '概览' },
    { key: 'profile', label: '车辆档案' },
    { key: 'loan', label: '贷款' },
    { key: 'insurance', label: '保险' },
    { key: 'events', label: '费用流水' },
    { key: 'maintenance', label: '保养维修' },
    { key: 'valuations', label: '估值' },
    { key: 'attachments', label: '文件' },
    { key: 'ai', label: 'AI 分析' },
  ],
  precious_metal: [
    { key: 'overview', label: '概览' },
    { key: 'profile', label: '批次与明细' },
    { key: 'valuations', label: '估值' },
    { key: 'events', label: '买卖事件' },
    { key: 'attachments', label: '证书附件' },
    { key: 'storage', label: '保管备注' },
    { key: 'ai', label: 'AI 分析' },
  ],
  other: [
    { key: 'overview', label: '概览' },
    { key: 'receivable', label: '借出与回款' },
    { key: 'valuations', label: '估值时间线' },
    { key: 'events', label: '资金事件' },
    { key: 'related', label: '关联负债' },
    { key: 'attachments', label: '附件' },
    { key: 'ai', label: 'AI 分析' },
  ],
}

/** 载入家庭成员与账户（资产表单与处置共用） */
export async function loadAssetMeta() {
  const [household, accounts] = await Promise.all([
    api.get<{ members: Member[] }>('/settings/household'),
    api.get<{ items: AccountLite[] }>('/ledger/accounts'),
  ])
  return {
    members: household.members || [],
    accounts: (accounts.items || []).filter((a) => a.status === 'active'),
  }
}
