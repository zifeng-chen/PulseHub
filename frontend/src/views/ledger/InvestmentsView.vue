<script setup lang="ts">
/**
 * 投资页面 /ledger/investments（06 §8、07 §6、M3）。
 * 账户列表显示账户余额、持仓市值、成本、损益；持仓页显示标的、数量、成本价、当前价、
 * 当前市值、已实现/未实现损益、占比、估值日期；交易表单支持买入、卖出、分红、费用；
 * 估值手工录入；无行情数据时明确标注「仅基于手工估值」。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Coins, Landmark, LineChart, PiggyBank, Plus, RefreshCw, Scale, Search, Sparkles, TrendingUp, Wallet,
} from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { formatAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppToolbar from '@/components/ui/AppToolbar.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppModal from '@/components/ui/AppModal.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import CategoryPicker from '@/components/CategoryPicker.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { fmtDate, type Member } from './asset-shared'

interface Position {
  symbol: string
  symbol_name: string | null
  quantity: string
  avg_cost: string
  cost: string
  current_price: string | null
  market_value: string
  market_value_is_estimated: boolean
  realized_pnl: string
  unrealized_pnl: string | null
  valuation_date: string | null
  valuation_source_label: string
  weight: string
  sold_out: boolean
  currency: string
}

interface InvestmentAccount {
  id: string
  name: string
  account_type: string
  account_type_label: string
  category_id: string | null
  category_label: string
  institution: string | null
  currency: string
  opening_cash: string
  include_in_net_worth: boolean
  cash_balance?: string
  market_value?: string
  cost?: string
  unrealized_pnl?: string
  realized_pnl?: string
  total_value?: string
  annualized_return_pct?: string | null
  return_days?: number | null
  position_count?: number
  all_priced?: boolean
  cash_negative?: boolean
  status_label: string
  note: string | null
  version: number
}

interface Trade {
  id: string
  trade_type: string
  trade_type_label: string
  symbol: string | null
  symbol_name: string | null
  quantity: string | null
  price: string | null
  amount: string
  fee: string
  net_amount: string
  currency: string
  traded_at: string | null
  note: string | null
}

interface Valuation {
  id: string
  symbol: string
  price: string
  currency: string
  valued_at: string | null
  source_label: string
  note: string | null
}

interface QuoteItem {
  symbol: string
  symbol_name: string | null
  quantity: string
  avg_cost: string
  cost: string
  price: string | null
  prev_close: string | null
  change: string | null
  change_pct: string | null
  income_per_10k: string | null
  return_pct: string | null
  market_value: string | null
  pnl: string | null
  as_of: string | null
  source: string | null
  source_label: string | null
  kind: string | null
  available: boolean
  reason: string | null
}

interface QuotePreview {
  account_id: string
  currency: string
  as_of: string
  items: QuoteItem[]
  summary: { updated: number; skipped: number; skipped_symbols: string[] }
}

interface InvestmentDetail extends InvestmentAccount {
  positions?: Position[]
  trades: Trade[]
  valuations: Valuation[]
  ai: { allowed: string[]; forbidden: string[] }
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const rows = ref<InvestmentAccount[]>([])
const summary = ref<Record<string, any> | null>(null)
const members = ref<Member[]>([])

const keyword = ref('')
const typeFilter = ref('')
const categoryFilter = ref('')
const categories = ref<{ id: string; name: string }[]>([])

const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const detailOpen = ref(false)
const detail = ref<InvestmentDetail | null>(null)
const detailLoading = ref(false)
const activeTab = ref('positions')
const positions = ref<Position[]>([])

const tradeOpen = ref(false)
const tradeSaving = ref(false)
const tradeErrors = ref<Record<string, string>>({})
const tradeForm = reactive({
  trade_type: 'buy', symbol: '', symbol_name: '', quantity: '', price: '',
  amount: '', fee: '', traded_at: '', note: '',
})

const valuationOpen = ref(false)
const valuationSaving = ref(false)
const valuationErrors = ref<Record<string, string>>({})
const valuationForm = reactive({ symbol: '', symbol_name: '', price: '', valued_at: '', source: 'manual', note: '' })

const form = reactive({
  id: '', version: 1, name: '', account_type: 'securities', category_id: '', institution: '',
  currency: 'CNY', opening_cash: '', member_id: '', note: '', include_in_net_worth: true,
})

/** 卡片外观：图标与角标配色按投资账户类型区分 */
const INVEST_LOOK: Record<string, { icon: unknown; tone: string }> = {
  securities: { icon: TrendingUp, tone: 'danger' },
  fund: { icon: PiggyBank, tone: 'ai' },
  bank_wm: { icon: Landmark, tone: 'primary' },
  other: { icon: Coins, tone: 'neutral' },
}
function lookOf(type: string) {
  return INVEST_LOOK[type] || INVEST_LOOK.other
}

/** 账户总值：优先后端给的 total_value，缺省用持仓市值 */
function totalOf(row: InvestmentAccount) {
  return row.total_value || row.market_value || '0'
}

const canCreate = computed(() => auth.can('create_draft'))
const canEdit = computed(() => auth.can('edit_draft'))
const canConfirm = computed(() => auth.can('confirm'))

const typeOptions = [
  { value: '', label: '全部类型' },
  ...['securities', 'fund', 'bank_wm', 'other'].map((v) => ({ value: v, label: zh('investment_account_type', v) })),
]
const categoryOptions = [
  { value: '', label: '全部分类' },
  ...categories.value.map((c) => ({ value: c.id, label: c.name })),
]
const formTypeOptions = ['securities', 'fund', 'bank_wm', 'other'].map((v) => ({
  value: v, label: zh('investment_account_type', v),
}))
const tradeTypeOptions = ['buy', 'sell', 'dividend', 'fee'].map((v) => ({
  value: v, label: zh('investment_trade_type', v),
}))
const sourceOptions = ['manual', 'market', 'other'].map((v) => ({ value: v, label: zh('valuation_source', v) }))
// 理财是少数支持多币种的模块（港美股、离岸基金等）
const currencyOptions = [
  { value: 'CNY', label: '人民币 CNY' },
  { value: 'USD', label: '美元 USD' },
  { value: 'HKD', label: '港币 HKD' },
  { value: 'EUR', label: '欧元 EUR' },
  { value: 'JPY', label: '日元 JPY' },
  { value: 'GBP', label: '英镑 GBP' },
  { value: 'SGD', label: '新加坡元 SGD' },
]
const needsSymbol = computed(() => ['buy', 'sell'].includes(tradeForm.trade_type))

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const [list, household, cats] = await Promise.all([
      api.get<{ items: InvestmentAccount[]; summary: Record<string, any> }>('/ledger/investments', {
        keyword: keyword.value || undefined,
        account_type: typeFilter.value || undefined,
        category_id: categoryFilter.value || undefined,
      }),
      api.get<{ members: Member[] }>('/settings/household'),
      api.get<{ items: { id: string; name: string; status: string }[] }>('/settings/categories', { domain: 'investment' }),
    ])
    if (seq !== loadSeq) return
    rows.value = list.items
    summary.value = list.summary
    members.value = household.members || []
    categories.value = (cats.items || []).filter((c) => c.status === 'active')
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function openCreate() {
  fieldErrors.value = {}
  Object.assign(form, {
    id: '', version: 1, name: '', account_type: 'securities', category_id: '', institution: '',
    currency: 'CNY', opening_cash: '', member_id: '', note: '', include_in_net_worth: true,
  })
  editorMode.value = 'create'
  editorOpen.value = true
}

function openEdit(row: InvestmentAccount) {
  fieldErrors.value = {}
  Object.assign(form, {
    id: row.id, version: row.version, name: row.name, account_type: row.account_type,
    category_id: row.category_id || '', institution: row.institution || '', currency: row.currency,
    opening_cash: row.opening_cash, member_id: '', note: row.note || '',
    include_in_net_worth: row.include_in_net_worth !== false,
  })
  editorMode.value = 'edit'
  editorOpen.value = true
}

async function save() {
  saving.value = true
  fieldErrors.value = {}
  const body: Record<string, unknown> = {
    name: form.name, account_type: form.account_type, category_id: form.category_id || null,
    institution: form.institution || null,
    currency: form.currency, opening_cash: form.opening_cash || '0',
    member_id: form.member_id || null, note: form.note || null,
    include_in_net_worth: form.include_in_net_worth,
  }
  try {
    if (editorMode.value === 'create') {
      await api.post('/ledger/investments', body)
      toast.success('投资账户已创建')
    } else {
      await api.put(`/ledger/investments/${form.id}`, { ...body, version: form.version })
      toast.success('投资账户已更新')
    }
    editorOpen.value = false
    await load()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function openDetail(row: InvestmentAccount) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  activeTab.value = 'positions'
  try {
    const data = await api.get<InvestmentDetail>(`/ledger/investments/${row.id}`)
    detail.value = data
    const pos = await api.get<{ items: Position[] }>(`/ledger/investments/${row.id}/positions`)
    positions.value = pos.items
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    detailLoading.value = false
  }
}

async function reloadDetail() {
  if (!detail.value) return
  const data = await api.get<InvestmentDetail>(`/ledger/investments/${detail.value.id}`)
  detail.value = data
  const pos = await api.get<{ items: Position[] }>(`/ledger/investments/${detail.value.id}/positions`)
  positions.value = pos.items
  await load()
}

function openTrade(preset?: { type?: string; symbol?: string; symbol_name?: string; price?: string }) {
  tradeErrors.value = {}
  Object.assign(tradeForm, {
    trade_type: preset?.type || 'buy', symbol: preset?.symbol || '', symbol_name: preset?.symbol_name || '',
    quantity: '', price: preset?.price || '', amount: '', fee: '', traded_at: '', note: '',
  })
  tradeOpen.value = true
}

async function submitTrade() {
  if (!detail.value) return
  tradeSaving.value = true
  tradeErrors.value = {}
  const body: Record<string, unknown> = {
    trade_type: tradeForm.trade_type,
    symbol: tradeForm.symbol || null,
    symbol_name: tradeForm.symbol_name || null,
    fee: tradeForm.fee || '0',
    traded_at: tradeForm.traded_at || null,
    note: tradeForm.note || null,
  }
  if (needsSymbol.value) {
    body.quantity = tradeForm.quantity
    body.price = tradeForm.price
  } else {
    body.amount = tradeForm.amount
  }
  try {
    await api.post(`/ledger/investments/${detail.value.id}/trades`, body)
    toast.success('交易已登记')
    tradeOpen.value = false
    await reloadDetail()
  } catch (err) {
    tradeErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    tradeSaving.value = false
  }
}

function openValuation(position?: Position) {
  valuationErrors.value = {}
  Object.assign(valuationForm, {
    symbol: position?.symbol || '', symbol_name: position?.symbol_name || '',
    price: position?.current_price || '', valued_at: '', source: 'manual', note: '',
  })
  valuationOpen.value = true
}

async function submitValuation() {
  if (!detail.value) return
  valuationSaving.value = true
  valuationErrors.value = {}
  try {
    await api.post(`/ledger/investments/${detail.value.id}/valuations`, {
      symbol: valuationForm.symbol, symbol_name: valuationForm.symbol_name || null,
      price: valuationForm.price, valued_at: valuationForm.valued_at || null,
      source: valuationForm.source, note: valuationForm.note || null,
    })
    toast.success('估值已录入，市值与未实现损益已更新')
    valuationOpen.value = false
    await reloadDetail()
  } catch (err) {
    valuationErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    valuationSaving.value = false
  }
}

async function remove(row: InvestmentAccount) {
  const res = await ui.askConfirm({
    title: '删除投资账户',
    message: `仅允许删除无持仓的账户；删除为软删除并保留审计。目标：${row.name}。`,
    confirmText: '确认删除', danger: true, requireReason: true, reasonLabel: '删除原因（必填）',
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'investment.delete', object_type: 'investment_account', object_id: row.id,
    })
    await api.delete(`/ledger/investments/${row.id}`, {
      reason: res.reason, confirm_token: tokenRes.token,
    })
    toast.success('投资账户已删除，历史保留')
    detailOpen.value = false
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

// ---- 行情（每天取一次，前端只做预览；写入估值需确认） ----
const quoteOpen = ref(false)
const quoteLoading = ref(false)
const quoteSaving = ref(false)
const quotePreview = ref<QuotePreview | null>(null)

async function openQuotes() {
  if (!detail.value) return
  quoteOpen.value = true
  quoteLoading.value = true
  quotePreview.value = null
  try {
    quotePreview.value = await api.get<QuotePreview>(`/ledger/investments/${detail.value.id}/quotes`)
  } catch (err) {
    toast.error(errorMessage(err))
    quoteOpen.value = false
  } finally {
    quoteLoading.value = false
  }
}

async function applyQuotes() {
  if (!detail.value) return
  quoteSaving.value = true
  try {
    const res = await api.post<{ updated: number; skipped: string[] }>(
      `/ledger/investments/${detail.value.id}/quotes/apply`, {},
    )
    toast.success(`已按行情更新 ${res.updated} 个持仓的估值`)
    if (res.skipped?.length) toast.info(`跳过 ${res.skipped.length} 个无公开行情的标的`)
    quoteOpen.value = false
    await reloadDetail()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    quoteSaving.value = false
  }
}

function pnlClass(value: string | null | undefined) {
  if (!value) return ''
  const num = Number(value)
  if (num > 0) return 'up'
  if (num < 0) return 'down'
  return ''
}

onMounted(load)
</script>

<template>
  <div class="inv">
    <PageHeader
      title="投资"
      subtitle="账户余额 = 初始可用资金 + 交易净影响；持仓按加权平均成本；当前价来自手工估值。"
      :breadcrumb="['家庭台账', '投资']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiAnalysis('investments', 'AI 持仓分析')">
        <Sparkles :size="15" /> AI 持仓分析
      </AppButton>
      <AppButton v-if="canCreate" variant="primary" @click="openCreate">
        <Plus :size="15" /> 新增投资账户
      </AppButton>
    </PageHeader>

    <section v-if="summary" class="stats">
      <StatCard
        :icon="TrendingUp" tone="primary" :label="`投资总额（${summary.base_currency}）`"
        :value="summary.total_value" :currency="summary.base_currency"
        :hint="`持仓 ${summary.market_value} + 现金 ${summary.cash}`"
      />
      <StatCard
        :icon="Wallet" tone="neutral" label="持仓成本"
        :value="summary.cost" :currency="summary.base_currency"
        hint="买入成本合计"
      />
      <StatCard
        :icon="Scale" tone="ai" label="未实现损益"
        :value="summary.unrealized_pnl" :currency="summary.base_currency" signed
        hint="市值 − 成本（浮动盈亏）"
      />
      <StatCard
        :icon="Coins" tone="warning" label="已实现损益"
        :value="summary.realized_pnl" :currency="summary.base_currency" signed
        hint="卖出部分已落袋的盈亏"
      />
    </section>
    <p v-if="summary?.multi_currency" class="multi">
      存在多币种投资账户，顶部统计仅展示基础币种 {{ summary.base_currency }}；不同币种不做相加。
    </p>

    <AppCard>
      <template #header>
        <AppToolbar>
          <div class="search">
            <Search :size="15" />
            <input v-model="keyword" placeholder="搜索账户名称" @keyup.enter="load" />
          </div>
          <AppSelect v-model="typeFilter" :options="typeOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="categoryFilter" :options="categoryOptions" class="sel" @update:model-value="load" />
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
      <StateBlock
        v-else-if="!rows.length" state="empty" title="还没有投资账户"
        description="登记证券、基金或银行理财账户后，可以记录交易、持仓与手工估值。"
      >
        <template #actions>
          <AppButton v-if="canCreate" variant="primary" @click="openCreate">
            <Plus :size="15" /> 新增投资账户
          </AppButton>
        </template>
      </StateBlock>

      <div v-else class="card-grid">
        <article
          v-for="row in rows"
          :key="row.id"
          class="entity-card"
          role="button"
          tabindex="0"
          @click="openDetail(row)"
          @keyup.enter="openDetail(row)"
        >
          <span class="entity-corner" :class="`tone-${lookOf(row.account_type).tone}`">
            {{ row.category_label || row.account_type_label }}
          </span>

          <header class="entity-head">
            <span class="entity-icon" :class="`tone-${lookOf(row.account_type).tone}`">
              <component :is="lookOf(row.account_type).icon" :size="18" />
            </span>
            <div class="entity-title">
              <h3>{{ row.name }}</h3>
              <p>{{ row.institution || row.account_type_label }}</p>
            </div>
          </header>

          <div class="entity-value">
            <p class="k">账户总值</p>
            <p class="v">
              <MoneyText :value="totalOf(row)" :currency="row.currency" show-currency />
            </p>
          </div>

          <div v-if="row.all_priced === false" class="entity-note warn">
            <span>部分持仓还没有价格</span>
            <strong>市值可能偏低</strong>
          </div>

          <div class="entity-meta">
            <span v-if="row.cash_balance">现金 <b>{{ formatAmount(row.cash_balance, ui.amountPrecision) }}</b></span>
            <span v-if="row.position_count">持仓 <b>{{ row.position_count }}</b> 项</span>
            <span v-if="row.unrealized_pnl && Number(row.unrealized_pnl) !== 0">
              浮动盈亏
              <b :class="pnlClass(row.unrealized_pnl)">{{ formatAmount(row.unrealized_pnl, ui.amountPrecision) }}</b>
            </span>
            <span v-if="row.annualized_return_pct">
              年化 <b :class="pnlClass(row.annualized_return_pct)">{{ row.annualized_return_pct }}%</b>
            </span>
          </div>

          <footer class="entity-actions" @click.stop>
            <button class="act" type="button" @click="openDetail(row)">详情</button>
            <button v-if="canEdit" class="act" type="button" @click="openEdit(row)">编辑</button>
          </footer>
        </article>
      </div>
    </AppCard>

    <!-- 新增 / 编辑 -->
    <AppDrawer
      :open="editorOpen" :title="editorMode === 'create' ? '新增投资账户' : '编辑投资账户'" :width="520"
      @close="editorOpen = false"
    >
      <div class="form">
        <AppField label="账户类型" :error="fieldErrors.account_type">
          <AppSelect v-model="form.account_type" :options="formTypeOptions" />
        </AppField>
        <CategoryPicker v-model="form.category_id" domain="investment" :error="fieldErrors.category_id"
                        hint="可点「＋」新增分类；子分类直接可选" />
        <AppField label="账户名称" required :error="fieldErrors.name">
          <AppInput v-model="form.name" placeholder="例如：某券商证券账户" />
        </AppField>
        <AppField label="机构" :error="fieldErrors.institution">
          <AppInput v-model="form.institution" />
        </AppField>
        <AppField label="币种" :error="fieldErrors.currency"
                  hint="支持多币种（港美股 / 离岸基金等）；已有交易记录的账户不可修改币种">
          <AppSelect v-model="form.currency" :options="currencyOptions" />
        </AppField>
        <AppField label="初始可用资金" :error="fieldErrors.opening_cash" hint="已有交易记录后不可直接修改">
          <AppInput v-model="form.opening_cash" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="归属成员">
          <AppSelect
            v-model="form.member_id"
            :options="[{ value: '', label: '未指定' }, ...members.map((m) => ({ value: m.id, label: m.display_name }))]"
          />
        </AppField>
        <AppField label="纳入净资产"
                  hint="账户总值（持仓市值 + 现金）是净资产的加项：关闭后该账户不计入家庭净值（如代持或测试账户）">
          <AppSwitch v-model="form.include_in_net_worth" label="账户总值计入净资产" />
        </AppField>
        <AppField label="备注"><AppInput v-model="form.note" /></AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">
          {{ editorMode === 'create' ? '创建账户' : '保存修改' }}
        </AppButton>
      </template>
    </AppDrawer>

    <!-- 详情 -->
    <AppDrawer :open="detailOpen" :title="detail?.name || '投资详情'" :width="760" @close="detailOpen = false">
      <StateBlock v-if="detailLoading" state="loading" />
      <template v-else-if="detail">
        <div class="head">
          <div class="row">
            <StatusBadge :label="detail.account_type_label" tone="info" />
            <StatusBadge :label="detail.status_label" tone="success" />
            <StatusBadge v-if="detail.all_priced === false" label="部分持仓为成本估值" tone="warning" />
            <StatusBadge v-if="detail.cash_negative" label="账户资金为负，请核对" tone="danger" />
          </div>
          <ul class="head-kv">
            <li><span class="k">账户余额</span><span class="v strong">{{ detail.cash_balance }}</span></li>
            <li><span class="k">持仓市值</span><span class="v strong">{{ detail.market_value }}</span></li>
            <li><span class="k">持仓成本</span><span class="v">{{ detail.cost }}</span></li>
            <li><span class="k">未实现损益</span><span class="v" :class="pnlClass(detail.unrealized_pnl)">{{ detail.unrealized_pnl }}</span></li>
            <li><span class="k">已实现损益</span><span class="v" :class="pnlClass(detail.realized_pnl)">{{ detail.realized_pnl }}</span></li>
            <li><span class="k">总价值</span><span class="v strong">{{ detail.total_value }}</span></li>
            <li>
              <span class="k">年化收益率（XIRR）</span>
              <span class="v" :class="pnlClass(detail.annualized_return_pct)">
                {{ detail.annualized_return_pct ? detail.annualized_return_pct + '%' : '—（需 ≥30 天交易记录）' }}
              </span>
            </li>
          </ul>
        </div>

        <div class="tabs">
          <button
            v-for="t in [
              { key: 'positions', label: `持仓（${positions.filter((p) => !p.sold_out).length}）` },
              { key: 'trades', label: `交易（${detail.trades.length}）` },
              { key: 'valuations', label: `估值与收益（${detail.valuations.length}）` },
              { key: 'ai', label: 'AI 分析' },
            ]"
            :key="t.key" class="tab" :class="{ 'is-active': activeTab === t.key }"
            type="button" @click="activeTab = t.key"
          >
            {{ t.label }}
          </button>
        </div>

        <div v-if="activeTab === 'positions'" class="pane">
          <div class="pane-actions">
            <AppButton v-if="canConfirm" variant="secondary" @click="openTrade({ type: 'buy' })">
              <Plus :size="15" /> 买入
            </AppButton>
            <AppButton v-if="canConfirm" variant="secondary" @click="openTrade({ type: 'sell' })">卖出</AppButton>
            <AppButton v-if="canEdit" variant="secondary" @click="openQuotes">
              <RefreshCw :size="15" /> 刷新收益率
            </AppButton>
          </div>
          <StateBlock v-if="!positions.filter((p) => !p.sold_out).length" state="empty" title="暂无持仓" description="登记买入交易后这里会显示持仓成本与损益。" />
          <ul v-else class="list">
            <li v-for="p in positions.filter((item) => !item.sold_out)" :key="p.symbol">
              <div class="l-main">
                <strong>{{ p.symbol_name || p.symbol }} <small class="sym">{{ p.symbol }}</small></strong>
                <small>数量 {{ p.quantity }} · 成本价 {{ p.avg_cost }} · 当前价 {{ p.current_price || '—' }}（{{ p.valuation_source_label || '未估值' }}）</small>
                <small>市值 {{ p.market_value }} · 成本 {{ p.cost }} · 占比 {{ p.weight }}%<template v-if="p.valuation_date"> · 估值 {{
                  fmtDate(p.valuation_date) }}</template></small>
                <small v-if="p.market_value_is_estimated" class="warn">无手工估值，暂按成本展示（仅基于手工估值）</small>
              </div>
              <div class="l-right">
                <div class="pnl">
                  <span :class="pnlClass(p.unrealized_pnl)">未实现 {{ p.unrealized_pnl || '—' }}</span>
                  <span :class="pnlClass(p.realized_pnl)">已实现 {{ p.realized_pnl }}</span>
                </div>
                <button v-if="canEdit" class="act" type="button" @click="openValuation(p)">录入估值</button>
              </div>
            </li>
          </ul>
          <template v-if="positions.some((p) => p.sold_out)">
            <h4 class="sec">已清仓</h4>
            <ul class="list">
              <li v-for="p in positions.filter((item) => item.sold_out)" :key="`${p.symbol}-closed`">
                <div class="l-main">
                  <strong>{{ p.symbol_name || p.symbol }} <small class="sym">{{ p.symbol }}</small></strong>
                  <small>已清仓 · 已实现损益 {{ p.realized_pnl }}</small>
                </div>
              </li>
            </ul>
          </template>
        </div>

        <div v-else-if="activeTab === 'trades'" class="pane">
          <div class="pane-actions">
            <AppButton v-if="canConfirm" variant="secondary" @click="openTrade({ type: 'buy' })">
              <Plus :size="15" /> 登记交易
            </AppButton>
          </div>
          <StateBlock v-if="!detail.trades.length" state="empty" title="暂无交易" description="买入、卖出、分红与费用都会记录在这里。" />
          <ul v-else class="list">
            <li v-for="t in detail.trades" :key="t.id">
              <div class="l-main">
                <strong>{{ t.trade_type_label }}<template v-if="t.symbol"> · {{ t.symbol_name || t.symbol }}</template></strong>
                <small>
                  {{ fmtDate(t.traded_at) }}
                  <template v-if="t.quantity"> · {{ t.quantity }} × {{ t.price }}</template>
                  · 金额 {{ t.amount }} · 费用 {{ t.fee }}
                </small>
              </div>
              <span class="l-amount" :class="Number(t.net_amount) >= 0 ? 'up' : 'down'">{{ t.net_amount }} {{ t.currency }}</span>
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'valuations'" class="pane">
          <div class="pane-actions">
            <AppButton v-if="canEdit" variant="secondary" @click="openValuation()">
              <LineChart :size="15" /> 录入估值
            </AppButton>
          </div>
          <p class="tip">
            持仓页签的「刷新收益率」可一次取回股票、场内基金与场外基金的最新价（每天一次足够，
            已缓存 30 分钟）；银行理财、私募没有公开行情，仍用「录入估值」手填。
            账户年化收益率按全部交易现金流 + 当前总价值用 XIRR 计算。
          </p>
          <StateBlock v-if="!detail.valuations.length" state="empty" title="暂无估值记录" description="录入估值后持仓市值与未实现损益会自动更新。" />
          <ul v-else class="list">
            <li v-for="v in detail.valuations" :key="v.id">
              <div class="l-main">
                <strong>{{ v.symbol }} · {{ v.price }} {{ v.currency }}</strong>
                <small>{{ fmtDate(v.valued_at) }} · {{ v.source_label }}</small>
              </div>
            </li>
          </ul>
        </div>

        <div v-else class="pane">
          <div class="ai">
            <h4><Sparkles :size="14" /> AI 可以做什么</h4>
            <ul><li v-for="c in detail.ai.allowed" :key="c">{{ c }}</li></ul>
            <h4>AI 不能做什么</h4>
            <ul class="forbidden"><li v-for="c in detail.ai.forbidden" :key="c">{{ c }}</li></ul>
            <AppButton variant="secondary" @click="ui.openAiPanel(`投资：${detail.name}`)">
              <Sparkles :size="15" /> 就这笔向 AI 追问
            </AppButton>
          </div>
        </div>
      </template>
      <template #footer>
        <div class="footer">
          <AppButton v-if="canConfirm && detail" variant="secondary" @click="openTrade({ type: 'buy' })">登记交易</AppButton>
          <AppButton v-if="auth.can('delete') && detail" variant="secondary" @click="remove(detail)">删除</AppButton>
          <AppButton variant="primary" @click="detailOpen = false">关闭</AppButton>
        </div>
      </template>
    </AppDrawer>

    <!-- 交易登记 -->
    <AppModal
      :open="tradeOpen" title="登记投资交易" confirm-text="保存交易"
      :confirm-loading="tradeSaving" @close="tradeOpen = false" @confirm="submitTrade"
    >
      <div class="form">
        <AppField label="交易类型" required :error="tradeErrors.trade_type">
          <AppSelect v-model="tradeForm.trade_type" :options="tradeTypeOptions" />
        </AppField>
        <AppField v-if="tradeForm.trade_type !== 'fee'" label="标的代码" :required="needsSymbol" :error="tradeErrors.symbol">
          <AppInput v-model="tradeForm.symbol" placeholder="例如：000001 / 510300" />
        </AppField>
        <AppField v-if="tradeForm.trade_type !== 'fee'" label="标的名称" :error="tradeErrors.symbol_name">
          <AppInput v-model="tradeForm.symbol_name" placeholder="例如：某指数基金" />
        </AppField>
        <AppField v-if="needsSymbol" label="数量 / 单价" required>
          <div class="grid-2">
            <AppInput v-model="tradeForm.quantity" type="number" placeholder="数量" />
            <AppInput v-model="tradeForm.price" type="number" placeholder="单价" />
          </div>
        </AppField>
        <AppField v-else label="金额" required :error="tradeErrors.amount">
          <AppInput v-model="tradeForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="费用" :error="tradeErrors.fee">
          <AppInput v-model="tradeForm.fee" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="交易日期">
          <AppDatePicker v-model="tradeForm.traded_at" value-format="datetime" placeholder="留空为今天" />
        </AppField>
        <AppField label="备注"><AppInput v-model="tradeForm.note" /></AppField>
      </div>
    </AppModal>

    <!-- 估值录入 -->
    <AppModal
      :open="valuationOpen" title="录入估值" confirm-text="保存估值"
      :confirm-loading="valuationSaving" @close="valuationOpen = false" @confirm="submitValuation"
    >
      <div class="form">
        <AppField label="标的代码" required :error="valuationErrors.symbol">
          <AppInput v-model="valuationForm.symbol" placeholder="例如：000001" />
        </AppField>
        <AppField label="标的名称" :error="valuationErrors.symbol_name">
          <AppInput v-model="valuationForm.symbol_name" />
        </AppField>
        <AppField label="当前价" required :error="valuationErrors.price">
          <AppInput v-model="valuationForm.price" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="估值日期">
          <AppDatePicker v-model="valuationForm.valued_at" value-format="datetime" placeholder="留空为今天" />
        </AppField>
        <AppField label="估值来源" :error="valuationErrors.source">
          <AppSelect v-model="valuationForm.source" :options="sourceOptions" />
        </AppField>
        <AppField label="备注" hint="市场行情需在备注中写明来源与日期"><AppInput v-model="valuationForm.note" /></AppField>
      </div>
    </AppModal>

    <!-- 按行情更新收益率（每天一次即可） -->
    <AppModal
      :open="quoteOpen" title="按行情更新收益率" size="lg"
      confirm-text="写入今日估值" :confirm-loading="quoteSaving"
      @close="quoteOpen = false" @confirm="applyQuotes"
    >
      <p v-if="quoteLoading" class="tip">正在获取最新行情…（同一天只查一次，已缓存 30 分钟）</p>
      <template v-else-if="quotePreview">
        <ul class="quote-list">
          <li v-for="q in quotePreview.items" :key="q.symbol">
            <div class="q-main">
              <strong>{{ q.symbol_name || q.symbol }} <small class="sym">{{ q.symbol }}</small></strong>
              <template v-if="q.available">
                <small>
                  最新 {{ q.price }} · 成本 {{ q.avg_cost }} · 收益率
                  <b :class="pnlClass(q.return_pct)">{{ q.return_pct }}%</b>
                  <template v-if="q.change_pct"> · 当日 <b :class="pnlClass(q.change_pct)">{{ q.change_pct }}%</b></template>
                  <template v-if="q.income_per_10k"> · 万份收益 <b>{{ q.income_per_10k }}</b></template>
                </small>
                <small class="q-asof">数据日 {{ q.as_of || '—' }} · {{ q.source_label }}</small>
              </template>
              <template v-else-if="q.kind === 'money_fund'">
                <small>万份收益 <b>{{ q.income_per_10k }}</b> · 货币基金没有净值涨跌</small>
                <small class="q-asof">数据日 {{ q.as_of || '—' }} · {{ q.source_label }}</small>
              </template>
              <small v-else class="warn">{{ q.reason || '暂无公开行情' }}</small>
            </div>
            <div v-if="q.available" class="q-right">
              <span>市值 {{ q.market_value }}</span>
              <span :class="pnlClass(q.pnl)">浮动 {{ q.pnl }}</span>
            </div>
          </li>
        </ul>
        <p class="tip">
          确认后把这些价格写成今天的估值，市值与未实现损益随之重算；
          没有行情的标的（银行理财、私募等）会跳过，不会用估算值冒充净值。
        </p>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.inv { display: flex; flex-direction: column; gap: var(--space-4); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(196px, 1fr)); gap: var(--space-3); }
.stat .k { font-size: 12.5px; color: var(--ink-muted); }
.multi { font-size: 12.5px; color: var(--ink-muted); }
.toolbar { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; width: 100%; }
.search {
  display: flex; align-items: center; gap: 6px; flex: 1; min-width: 180px; height: 34px;
  padding: 0 10px; border: 1px solid var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1); color: var(--ink-muted);
}
.search input { flex: 1; border: none; background: transparent; color: var(--ink-strong); font-size: 13.5px; outline: none; }
.sel { width: 150px; }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act { height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer; }
.act:hover { border-color: var(--primary); color: var(--primary); }
.quote-list { display: flex; flex-direction: column; gap: 8px; max-height: 52vh; overflow: auto; }
.quote-list li {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 9px 11px; border-radius: 10px;
  border: 1px solid var(--border); background: var(--glass-1);
}
.q-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.q-main strong { font-size: 13px; color: var(--ink-strong); }
.q-main small { font-size: 12px; color: var(--ink-muted); }
.q-main .q-asof { font-size: 11.5px; opacity: 0.8; }
.q-right { display: flex; flex-direction: column; gap: 3px; align-items: flex-end; font-size: 12px; color: var(--ink-muted); white-space: nowrap; }
.up { color: var(--danger); }
.down { color: var(--success); }
.head { margin-bottom: var(--space-3); }
.head .row { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.head-kv { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; }
.head-kv li { display: flex; flex-direction: column; gap: 2px; font-size: 12.5px; }
.head-kv .k { color: var(--ink-muted); }
.head-kv .v { color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.head-kv .v.strong { font-weight: 600; font-size: 14px; }
.tabs { display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid var(--border); margin-bottom: var(--space-3); }
.tab { flex: none; padding: 8px 12px; border: none; background: transparent; cursor: pointer; font-size: 13px; color: var(--ink-muted); border-bottom: 2px solid transparent; }
.tab.is-active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 500; }
.pane { display: flex; flex-direction: column; gap: var(--space-3); }
.pane-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.list { display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.l-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.l-main strong { font-size: 13.5px; color: var(--ink-strong); }
.l-main .sym { font-weight: 400; color: var(--ink-muted); font-size: 11.5px; }
.l-main small { font-size: 11.5px; color: var(--ink-muted); }
.l-main small.warn { color: var(--warning, #d97706); }
.l-right { display: flex; align-items: center; gap: 10px; }
.pnl { display: flex; flex-direction: column; align-items: flex-end; font-size: 12.5px; }
.l-amount { font-variant-numeric: tabular-nums; font-size: 13.5px; white-space: nowrap; }
.sec { font-size: 13px; color: var(--ink-strong); margin-top: var(--space-3); }
.tip { font-size: 12.5px; color: var(--ink-muted); line-height: 1.6; }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.ai { display: flex; flex-direction: column; gap: 10px; }
.ai h4 { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul.forbidden li { color: var(--ink-muted); }
.footer { display: flex; gap: var(--space-2); justify-content: flex-end; flex-wrap: wrap; }
@media (max-width: 700px) {
  .sel { width: 100%; }
  .grid-2 { grid-template-columns: 1fr; }
  .head-kv { grid-template-columns: 1fr 1fr; }
}

/* 移动端：统计卡两列排布，别再一人一行把首屏占满 */
@media (max-width: 640px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .stat :deep(.card-body) { padding: 11px 12px 12px; }
  .stat .k { font-size: 11.5px; }
  .stat .n { font-size: 19px; }
  .stat :deep(.money) { font-size: 17px; }
}

</style>
