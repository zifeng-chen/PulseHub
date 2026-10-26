<script setup lang="ts">
/**
 * 账户页面 /ledger/accounts（06 §3、18 §4 M1 核心账务）。
 * 余额由后端确定性服务计算（期初余额 + 已确认资金变动），前端不自行计算。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import {
  Plus, Search, Sparkles, Wallet, Archive, RefreshCw, ArrowLeftRight,
  CalendarClock, Scale, Landmark, Banknote, Smartphone,
} from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import { formatAmount } from '@/utils/format'
import AppButton from '@/components/ui/AppButton.vue'
import AppToolbar from '@/components/ui/AppToolbar.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import BrandIcon from '@/components/ui/BrandIcon.vue'
import CategoryPicker from '@/components/CategoryPicker.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Account {
  id: string
  name: string
  account_type: string
  account_type_label: string
  category_id: string | null
  category_label: string
  institution: string | null
  card_last4: string | null
  card_masked: string
  holder_name: string | null
  credit_limit: string | null
  credit_used: string | null
  available_credit: string | null
  credit_overpaid: string | null
  is_credit: boolean
  card_expiry: string | null
  bill_day: number | null
  due_day: number | null
  interest_rate: string | null
  term_months: number | null
  maturity_date: string | null
  currency: string
  opening_balance: string
  opening_date: string | null
  balance: string
  available_balance: string
  include_in_net_worth: boolean
  status: string
  status_label: string
  note: string | null
  version: number
}

interface FlowRow {
  event_id: string
  event_no: string
  event_type: string
  title: string
  amount: string
  direction: string
  direction_label: string
  currency: string
  status: string
  occurred_at: string | null
}

interface AccountTypeTotal {
  account_type: string
  label: string
  count: number
  total: string
  other_currency_count: number
}
interface Summary {
  accounts: {
    count: number
    by_currency: { currency: string; total: string; count: number }[]
    by_type?: AccountTypeTotal[]
    base_currency?: string
    base_total?: string
  }
}

const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const rows = ref<Account[]>([])
const ACC_LOOK: Record<string, { icon: Component; tone: 'primary' | 'success' | 'ai' | 'neutral' }> = {
  bank: { icon: Landmark, tone: 'primary' },
  cash: { icon: Banknote, tone: 'success' },
  e_wallet: { icon: Smartphone, tone: 'ai' },
  other: { icon: Wallet, tone: 'neutral' },
}
function accLook(type: string) {
  return ACC_LOOK[type] || ACC_LOOK.other
}

const summary = ref<Summary | null>(null)
const keyword = ref('')
const typeFilter = ref('')
const statusFilter = ref('')
const categoryFilter = ref('')
const accountCategories = ref<{ id: string; name: string; status: string; applies_to: string | null; attribute_profile: string | null }[]>([])

const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const detailOpen = ref(false)
const detail = ref<(Account & { flow: FlowRow[] }) | null>(null)
const detailLoading = ref(false)

const form = reactive({
  id: '',
  version: 1,
  name: '',
  account_type: 'bank',
  category_id: '',
  institution: '',
  card_last4: '',
  holder_name: '',
  credit_limit: '',
  credit_used: '',
  card_expiry: '',
  bill_day: '',
  due_day: '',
  interest_rate: '',
  term_months: '',
  maturity_date: '',
  currentBalance: '',
  currency: 'CNY',
  opening_balance: '0',
  opening_date: '',
  include_in_net_worth: true,
  note: '',
})

// ---------------- 卡片视图：角标文案与配色 ----------------
/** 角标：优先显示二级分类（信用卡 / 储蓄卡 / 支付宝 / 花呗…），停用则显示状态 */
function cornerText(a: Account) {
  if (a.status !== 'active') return a.status_label || '已停用'
  // 未选分类时按账户性质兜底：信用类不能显示成「银行卡」
  return a.category_label || (a.is_credit ? '信用类' : a.account_type_label)
}
function cornerTone(a: Account) {
  if (a.status !== 'active') return 'off'
  if (a.is_credit) return 'credit'
  return a.account_type || 'other'
}

/** 还款日倒计时：今天到期显示「今天还款」，否则显示剩余天数 */
function dueInfo(a: Account) {
  if (!a.due_day) return null
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const day = Math.min(a.due_day, new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate())
  let due = new Date(today.getFullYear(), today.getMonth(), day)
  if (due.getTime() < today.getTime()) {
    const nextMonth = new Date(today.getFullYear(), today.getMonth() + 1, 1)
    const lastDay = new Date(nextMonth.getFullYear(), nextMonth.getMonth() + 1, 0).getDate()
    due = new Date(nextMonth.getFullYear(), nextMonth.getMonth(), Math.min(a.due_day, lastDay))
  }
  const days = Math.round((due.getTime() - today.getTime()) / 86400000)
  return { days, label: days === 0 ? '今天还款' : `还有 ${days} 天` }
}

const canCreate = computed(() => auth.can('create_draft'))
const canEdit = computed(() => auth.can('edit_draft'))
const canArchive = computed(() => auth.can('archive'))

const typeOptions = [
  { value: '', label: '全部类型' },
  ...['bank', 'cash', 'e_wallet', 'other'].map((v) => ({ value: v, label: zh('account_type', v) })),
]
const formTypeOptions = ['bank', 'cash', 'e_wallet', 'other'].map((v) => ({
  value: v, label: zh('account_type', v),
}))
const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'active', label: '正常' },
  { value: 'disabled', label: '已停用' },
]
const categoryFilterOptions = computed(() => [
  { value: '', label: '全部分类' },
  ...accountCategories.value.map((c) => ({ value: c.id, label: c.name })),
])
const currencyOptions = [
  { value: 'CNY', label: '人民币 CNY' },
  { value: 'USD', label: '美元 USD' },
  { value: 'HKD', label: '港币 HKD' },
  { value: 'EUR', label: '欧元 EUR' },
]

// ---------------- 开户机构联动（按账户类型给候选，可自定义） ----------------
const CUSTOM_INSTITUTION = '__custom__'
const institutionCustom = ref(false)

const INSTITUTION_PRESETS: Record<string, string[]> = {
  bank: [
    '中国工商银行', '中国农业银行', '中国银行', '中国建设银行', '交通银行',
    '中国邮政储蓄银行', '招商银行', '浦发银行', '中信银行', '中国民生银行',
    '兴业银行', '中国光大银行', '华夏银行', '平安银行', '广发银行',
    '北京银行', '上海银行', '宁波银行',
  ],
  e_wallet: ['支付宝', '微信支付', '云闪付', '京东', '美团', '抖音'],
  cash: [],
  other: [],
}

/** 候选 = 该账户类型的常见机构 + 已有账户里用过的机构（去重） */
const institutionPresets = computed(() => {
  const history = rows.value.map((r) => (r.institution || '').trim()).filter(Boolean)
  return [...new Set([...(INSTITUTION_PRESETS[form.account_type] || []), ...history])]
})

const institutionOptions = computed(() => [
  { value: '', label: '未选择' },
  ...institutionPresets.value.map((v) => ({ value: v, label: v })),
  { value: CUSTOM_INSTITUTION, label: '其他（手动填写）' },
])

const institutionSelectValue = computed(() =>
  institutionCustom.value ? CUSTOM_INSTITUTION : (form.institution || ''),
)

const institutionHint = computed(() => {
  switch (form.account_type) {
    case 'bank': return '按账户类型联动：可直接选常见银行，选「其他」可手动填写'
    case 'e_wallet': return '支付宝 / 微信支付等第三方平台；也可手动填写'
    case 'cash': return '现金账户一般无需填写开户机构'
    default: return '可手动填写开户机构名称'
  }
})

function onInstitutionChange(value: string) {
  if (value === CUSTOM_INSTITUTION) {
    institutionCustom.value = true
    form.institution = ''
  } else {
    institutionCustom.value = false
    form.institution = value
  }
}

/** 切换账户类型：清掉不适用于新类型的分类与机构（仅在用户主动切换时触发）。 */
function onAccountTypeChange(value: string) {
  const prev = form.account_type
  form.account_type = value
  if (!value || value === prev) return
  const picked = accountCategories.value.find((c) => c.id === form.category_id)
  if (picked) {
    const keys = (picked.applies_to || '').split(',').map((s) => s.trim()).filter(Boolean)
    if (keys.length && !keys.includes(value)) form.category_id = ''
  }
  institutionCustom.value = false
  form.institution = ''
}

// ---------------- 按所选分类的「字段档案」显示专属字段（按分类优化细节） ----------------
const selectedProfile = computed(() => {
  const cat = accountCategories.value.find((c) => c.id === form.category_id)
  return cat?.attribute_profile || ''
})

const isCreditCard = computed(() => selectedProfile.value === 'credit_card')
const isCreditLine = computed(() => selectedProfile.value === 'credit_line')
const isLoanLine = computed(() => selectedProfile.value === 'loan_line')
/** 信用类（信用卡 / 消费信贷 / 借款额度）：有额度概念，不填期初余额 */
const isCreditAccount = computed(
  () => isCreditCard.value || isCreditLine.value || isLoanLine.value,
)
const isSavingsCurrent = computed(() => selectedProfile.value === 'savings_current')
const isSavingsFixed = computed(() => selectedProfile.value === 'savings_fixed')
const isSavings = computed(() => isSavingsCurrent.value || isSavingsFixed.value)

/** 额度 / 已用的措辞随分类变化 */
const creditLimitLabel = computed(() => {
  if (isCreditCard.value) return '信用额度'
  if (isLoanLine.value) return '借款额度'
  return '授信额度'
})
const creditUsedLabel = computed(() => {
  if (isCreditCard.value) return '当前已用额度'
  if (isLoanLine.value) return '已借金额'
  return '已用额度'
})

/** 卡号后四位：借记卡 / 信用卡 / 存单；未设档案时按账户类型兜底 */
const showCardNo = computed(
  () => selectedProfile.value === 'debit_card' || isCreditCard.value || isSavings.value
    || (!selectedProfile.value && form.account_type === 'bank'),
)

/** 开户人：电子钱包 / 消费信贷 / 借款额度；未设档案时按账户类型兜底 */
const showHolder = computed(
  () => selectedProfile.value === 'wallet' || isCreditLine.value || isLoanLine.value
    || (!selectedProfile.value && form.account_type !== 'bank'),
)

/** 开户机构：电子钱包类型的分类本身就是机构（支付宝 / 微信零钱 / 花呗等），不再重复填写 */
const showInstitution = computed(
  () => form.account_type !== 'e_wallet' && selectedProfile.value !== 'wallet',
)

/** 期初余额：信用类用「已用额度」表达，不再重复填写期初欠款 */
const showOpeningBalance = computed(() => !isCreditAccount.value)

/** 当前可用额度 = 信用额度 − 已用额度（确定性计算，只读展示） */
const availableCredit = computed(() => {
  if (form.credit_limit === '' || form.credit_limit === null) return ''
  const limit = Number(form.credit_limit)
  if (!Number.isFinite(limit)) return ''
  const used = Number(form.credit_used || 0)
  return (limit - (Number.isFinite(used) ? used : 0)).toFixed(ui.amountPrecision)
})

/** 利率展示：去掉多余的 0（1.5000 → 1.5） */
function fmtRate(value: string | null | undefined) {
  if (value === null || value === undefined || value === '') return ''
  const num = Number(value)
  return Number.isFinite(num) ? String(num) : String(value)
}

/** 详情里按该账户的分类档案调整措辞 */
const detailProfile = computed(() => {
  const cat = accountCategories.value.find((c) => c.id === detail.value?.category_id)
  return cat?.attribute_profile || ''
})

/** 信用卡 / 电子钱包：余额类字段由额度与待还表达，详情不再重复展示 */
const hideBalanceRows = computed(() => {
  if (!detail.value) return false
  return detail.value.is_credit || detailProfile.value === 'wallet'
})

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const [list, sum] = await Promise.all([
      api.get<{ items: Account[] }>('/ledger/accounts', {
        keyword: keyword.value || undefined,
        account_type: typeFilter.value || undefined,
        status: statusFilter.value || undefined,
        category_id: categoryFilter.value || undefined,
      }),
      api.get<Summary>('/ledger/summary'),
    ])
    if (seq !== loadSeq) return
    rows.value = list.items
    summary.value = sum
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function openCreate() {
  fieldErrors.value = {}
  institutionCustom.value = false
  Object.assign(form, {
    id: '', version: 1, name: '', account_type: 'bank', category_id: '', institution: '',
    card_last4: '', holder_name: auth.displayName || '',
    credit_limit: '', credit_used: '', card_expiry: '', bill_day: '', due_day: '',
    interest_rate: '', term_months: '', maturity_date: '', currentBalance: '',
    currency: 'CNY', opening_balance: '0', opening_date: '', include_in_net_worth: true, note: '',
  })
  editorMode.value = 'create'
  editorOpen.value = true
}

function openEdit(row: Account) {
  fieldErrors.value = {}
  Object.assign(form, {
    id: row.id,
    version: row.version,
    name: row.name,
    account_type: row.account_type,
    category_id: row.category_id || '',
    institution: row.institution || '',
    card_last4: row.card_last4 || '',
    holder_name: row.holder_name || '',
    credit_limit: row.credit_limit ?? '',
    credit_used: row.credit_used ?? '',
    card_expiry: row.card_expiry || '',
    bill_day: row.bill_day != null ? String(row.bill_day) : '',
    due_day: row.due_day != null ? String(row.due_day) : '',
    interest_rate: row.interest_rate ?? '',
    term_months: row.term_months != null ? String(row.term_months) : '',
    maturity_date: row.maturity_date ? row.maturity_date.slice(0, 10) : '',
    currentBalance: row.balance,
    currency: row.currency,
    opening_balance: row.opening_balance,
    opening_date: row.opening_date ? row.opening_date.slice(0, 10) : '',
    include_in_net_worth: row.include_in_net_worth,
    note: row.note || '',
  })
  // 存量机构不在候选里时，切到「其他（手动填写）」保留原值
  const current = (row.institution || '').trim()
  institutionCustom.value = Boolean(current) && !institutionPresets.value.includes(current)
  editorMode.value = 'edit'
  editorOpen.value = true
}

async function save() {
  saving.value = true
  fieldErrors.value = {}
  const body: Record<string, unknown> = {
    name: form.name,
    account_type: form.account_type,
    category_id: form.category_id || null,
    institution: form.institution || null,
    card_last4: form.card_last4 || null,
    holder_name: form.holder_name || null,
    // 信用类字段仅在对应分类下提交，避免切到其他分类后残留旧值
    credit_limit: isCreditAccount.value ? (form.credit_limit || null) : null,
    credit_used: isCreditAccount.value ? (form.credit_used || null) : null,
    card_expiry: isCreditAccount.value ? (form.card_expiry || null) : null,
    bill_day: isCreditAccount.value ? (form.bill_day || null) : null,
    due_day: isCreditAccount.value ? (form.due_day || null) : null,
    // 利率：借呗类借款与储蓄存款共用；存期/到期日仅定期存款
    interest_rate: (isLoanLine.value || isSavings.value) ? (form.interest_rate || null) : null,
    term_months: isSavingsFixed.value ? (form.term_months || null) : null,
    maturity_date: isSavingsFixed.value ? (form.maturity_date || null) : null,
    currency: form.currency,
    opening_balance: form.opening_balance || '0',
    opening_date: form.opening_date || null,
    include_in_net_worth: form.include_in_net_worth,
    note: form.note || null,
  }
  try {
    if (editorMode.value === 'create') {
      await api.post('/ledger/accounts', body)
      toast.success('账户已创建')
    } else {
      await api.put(`/ledger/accounts/${form.id}`, { ...body, version: form.version })
      toast.success('账户已更新')
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

async function openDetail(row: Account) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.get(`/ledger/accounts/${row.id}`)
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    detailLoading.value = false
  }
}

async function archive(row: Account) {
  const res = await ui.askConfirm({
    title: '确认停用账户',
    message: `停用后该账户不再参与余额与净资产统计，历史流水保留。目标账户：${row.name}。`,
    detail: [
      '有资金变动的账户不允许删除，只能停用。',
      '停用属于高影响操作，会记录操作人、时间与原因。',
    ],
    confirmText: '确认停用',
    danger: true,
    irreversible: false,
    requireReason: true,
    reasonLabel: '停用原因（必填）',
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'account.archive', object_type: 'account', object_id: row.id,
    })
    await api.delete(`/ledger/accounts/${row.id}`, {
      confirm_token: tokenRes.token, reason: res.reason,
    })
    toast.success('账户已停用')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

function fmt(v: string | null) {
  return v ? new Date(v).toLocaleString('zh-CN') : '—'
}

async function loadCategories() {
  try {
    const res = await api.get<{ items: { id: string; name: string; status: string; applies_to: string | null; attribute_profile: string | null }[] }>(
      '/settings/categories', { domain: 'account' })
    accountCategories.value = (res.items || []).filter((c) => c.status === 'active')
  } catch {
    accountCategories.value = []
  }
}

onMounted(() => {
  load()
  loadCategories()
})
</script>

<template>
  <div class="accounts">
    <PageHeader
      title="账户"
      subtitle="余额 = 期初余额 + 已确认资金变动净额；草稿、已取消、已冲销事件不计入。"
      :breadcrumb="['家庭台账', '账户']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="router.push('/ledger/reconciliation')">
        <Scale :size="15" /> 对账
      </AppButton>
      <AppButton variant="secondary" @click="router.push('/ledger/transactions?transfer=1')">
        <ArrowLeftRight :size="15" /> 内部转账
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiAnalysis('cashflow', 'AI 现金流分析')">
        <Sparkles :size="15" /> AI 现金流分析
      </AppButton>
      <AppButton v-if="canCreate" variant="primary" @click="openCreate">
        <Plus :size="15" /> 新增账户
      </AppButton>
    </PageHeader>

    <section v-if="summary" class="stats">
      <StatCard
        :icon="Wallet" tone="primary" label="账户合计"
        :value="summary.accounts.base_total || '0'"
        :currency="summary.accounts.base_currency || 'CNY'"
        :hint="`${summary.accounts.count} 个账户 · ${summary.accounts.base_currency || 'CNY'} 口径`"
      />
      <StatCard
        v-for="t in summary.accounts.by_type || []"
        :key="t.account_type"
        :icon="accLook(t.account_type).icon" :tone="accLook(t.account_type).tone"
        :label="t.label"
        :value="t.total" :currency="summary.accounts.base_currency || 'CNY'"
        :hint="t.other_currency_count
          ? `${t.count} 个账户 · 另有 ${t.other_currency_count} 个外币账户未计入`
          : `${t.count} 个账户`"
      />
    </section>

    <AppCard>
      <template #header>
        <AppToolbar>
          <div class="search">
            <Search :size="15" />
            <input v-model="keyword" placeholder="搜索账户名称" @keyup.enter="load" />
          </div>
          <AppSelect v-model="typeFilter" :options="typeOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="categoryFilter" :options="categoryFilterOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="statusFilter" :options="statusOptions" class="sel" @update:model-value="load" />
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
      <StateBlock
        v-else-if="!rows.length"
        state="empty"
        title="还没有账户"
        description="先新增一个账户，之后的收入、支出与转账都挂在账户上。"
      >
        <template #actions>
          <AppButton v-if="canCreate" variant="primary" @click="openCreate">
            <Wallet :size="15" /> 新增账户
          </AppButton>
        </template>
      </StateBlock>

      <div v-else class="acc-grid">
        <article
          v-for="a in rows"
          :key="a.id"
          class="acc-card"
          :class="{ off: a.status !== 'active' }"
          role="button"
          tabindex="0"
          @click="openDetail(a)"
          @keyup.enter="openDetail(a)"
        >
          <!-- 角标：分类（信用卡 / 储蓄卡 / 支付宝…），停用显示状态 -->
          <span class="corner" :class="cornerTone(a)">{{ cornerText(a) }}</span>

          <header class="acc-head">
            <BrandIcon
              :institution="a.institution"
              :name="a.name"
              :type="a.account_type"
              class="acc-brand"
            />
            <div class="acc-title">
              <h3>{{ a.name }}</h3>
              <p>{{ a.institution || a.holder_name || a.account_type_label }}</p>
            </div>
          </header>

          <div class="acc-bal">
            <p class="k">{{ a.is_credit ? '待还' : '当前余额' }}</p>
            <div class="v">
              <template v-if="a.is_credit">
                <MoneyText
                  v-if="Number(a.credit_used || 0) > 0"
                  :value="a.credit_used" :currency="a.currency" show-currency
                />
                <span v-else-if="a.credit_overpaid" class="muted">
                  溢缴 {{ formatAmount(a.credit_overpaid, ui.amountPrecision) }}
                </span>
                <span v-else class="muted">无欠款</span>
              </template>
              <MoneyText v-else :value="a.balance" :currency="a.currency" show-currency />
            </div>
          </div>

          <div class="acc-meta">
            <span v-if="a.credit_limit">
              可用 {{ formatAmount(a.available_credit, ui.amountPrecision) }} /
              {{ formatAmount(a.credit_limit, ui.amountPrecision) }}
            </span>
            <span v-else-if="a.interest_rate">
              {{ fmtRate(a.interest_rate) }}%<template v-if="a.term_months"> · {{ a.term_months }} 个月</template>
            </span>
            <span v-else>{{ a.account_type_label }}</span>
            <span v-if="a.card_masked">{{ a.card_masked }}</span>
            <span v-else-if="a.holder_name">{{ a.holder_name }}</span>
            <span>{{ a.currency }}</span>
          </div>

          <div
            v-if="dueInfo(a)"
            class="acc-due"
            :class="{ today: dueInfo(a)!.days === 0 }"
          >
            <CalendarClock :size="13" />
            <span>还款日 {{ a.due_day }} 日</span>
            <strong>{{ dueInfo(a)!.label }}</strong>
          </div>

          <footer class="acc-actions" @click.stop>
            <button class="act" type="button" @click="openDetail(a)">详情</button>
            <button
              v-if="canEdit && a.status === 'active'"
              class="act"
              type="button"
              @click="openEdit(a)"
            >
              编辑
            </button>
            <button
              v-if="canArchive && a.status === 'active'"
              class="act danger"
              type="button"
              @click="archive(a)"
            >
              <Archive :size="13" /> 停用
            </button>
          </footer>
        </article>
      </div>
    </AppCard>

    <!-- 新增 / 编辑抽屉 -->
    <AppDrawer
      :open="editorOpen"
      :title="editorMode === 'create' ? '新增账户' : '编辑账户'"
      @close="editorOpen = false"
    >
      <div class="form">
        <AppField label="账户类型" :error="fieldErrors.account_type"
                  hint="先选类型，分类会自动只显示与之相关的选项">
          <AppSelect :model-value="form.account_type" :options="formTypeOptions"
                     @update:model-value="onAccountTypeChange" />
        </AppField>
        <CategoryPicker
          v-model="form.category_id" domain="account" :filter-key="form.account_type"
          :label="form.account_type === 'e_wallet' ? '开户机构' : '分类'"
          :leaf-only="form.account_type === 'e_wallet'"
          :error="fieldErrors.category_id"
          :hint="form.account_type === 'e_wallet'
            ? '电子钱包的二级分类就是开户机构（支付宝 / 微信零钱 / 花呗 / 借呗 / 白条等），选完无需再填机构'
            : '只显示与所选账户类型相关的分类（信用卡 / 花呗 / 借呗 / 白条等可直接选）'"
        />
        <!-- 先采集核心身份信息：名称 / 机构 / 卡号或开户人 / 币种 -->
        <AppField label="账户名称" required :error="fieldErrors.name">
          <AppInput v-model="form.name" placeholder="例如：招行工资卡 / 我的花呗" />
        </AppField>
        <AppField v-if="showInstitution" label="开户机构" :error="fieldErrors.institution" :hint="institutionHint">
          <div class="inst-row">
            <AppSelect :model-value="institutionSelectValue" :options="institutionOptions"
                       @update:model-value="onInstitutionChange" />
            <AppInput v-if="institutionCustom" v-model="form.institution"
                      placeholder="请输入开户机构名称" />
          </div>
        </AppField>
        <AppField v-if="showCardNo" label="卡号后四位"
                  :error="fieldErrors.card_last4" hint="只保存后四位，不会留存完整卡号">
          <AppInput v-model="form.card_last4" placeholder="例如：1234" maxlength="4" />
        </AppField>
        <AppField v-if="showHolder" label="开户人" :error="fieldErrors.holder_name"
                  hint="默认当前用户；属于其他家庭成员的可直接改成对方姓名">
          <AppInput v-model="form.holder_name" placeholder="默认当前登录用户" />
        </AppField>
        <AppField label="币种" :error="fieldErrors.currency" hint="已有流水的账户不可修改币种">
          <AppSelect v-model="form.currency" :options="currencyOptions" />
        </AppField>

        <!-- 信用类账户专属字段（信用卡 / 花呗白条 / 借呗金条，按所选分类自动出现） -->
        <template v-if="isCreditAccount">
          <AppField :label="creditLimitLabel" :error="fieldErrors.credit_limit"
                    hint="银行 / 平台授予的总额度">
            <AppInput v-model="form.credit_limit" type="number" placeholder="例如：50000" />
          </AppField>
          <AppField :label="creditUsedLabel" :error="fieldErrors.credit_used"
                    :hint="editorMode === 'edit'
                      ? '已用额度由记账自动计算（消费增加、还款与退款减少）；修正历史请用冲销或更正'
                      : '账单或 App 上显示的已用 / 已借金额，会转为期初欠款'">
            <AppInput v-model="form.credit_used" type="number" placeholder="例如：12345.67"
                      :disabled="editorMode === 'edit'" />
          </AppField>
          <AppField label="当前可用额度" :hint="`自动计算：${creditLimitLabel} − ${creditUsedLabel}`">
            <AppInput :model-value="availableCredit" disabled placeholder="填写额度后自动计算" />
          </AppField>
          <AppField v-if="isCreditCard" label="卡片有效期" :error="fieldErrors.card_expiry"
                    hint="卡片正面的有效期（选择年月）">
            <AppDatePicker v-model="form.card_expiry" value-format="month" placeholder="选择有效期" />
          </AppField>
          <AppField v-if="isLoanLine" label="年化利率（%）" :error="fieldErrors.interest_rate"
                    hint="借呗 / 金条显示的日利率可换算成年化，填百分数">
            <AppInput v-model="form.interest_rate" type="number" placeholder="例如：7.3" />
          </AppField>
          <AppField v-if="!isLoanLine" label="账单日" :error="fieldErrors.bill_day"
                    hint="每月出账日（1-28）">
            <AppInput v-model="form.bill_day" type="number" placeholder="例如：5" />
          </AppField>
          <AppField label="还款日" :error="fieldErrors.due_day" hint="每月最后还款日（1-28）">
            <AppInput v-model="form.due_day" type="number" placeholder="例如：23" />
          </AppField>
        </template>

        <!-- 储蓄存款：活期只看利率，定期再加存期与到期日 -->
        <template v-if="isSavings">
          <AppField label="年利率（%）" :error="fieldErrors.interest_rate"
                    hint="填写百分数，例如 1.5 表示 1.5%">
            <AppInput v-model="form.interest_rate" type="number" placeholder="例如：1.5" />
          </AppField>
          <template v-if="isSavingsFixed">
            <AppField label="存期（月）" :error="fieldErrors.term_months"
                      hint="定期存款期限，例如 12 表示一年期">
              <AppInput v-model="form.term_months" type="number" placeholder="例如：12" />
            </AppField>
            <AppField label="到期日" :error="fieldErrors.maturity_date" hint="存款到期日期">
              <AppDatePicker v-model="form.maturity_date" placeholder="选择到期日" />
            </AppField>
          </template>
        </template>

        <AppField v-if="showOpeningBalance"
                  :label="editorMode === 'create' ? '当前余额' : '期初余额'"
                  :error="fieldErrors.opening_balance"
                  :hint="editorMode === 'create'
                    ? '录入时该账户的实际余额；之后余额由流水自动累计'
                    : '期初余额；当前余额 = 期初 + 已确认流水净额'">
          <AppInput v-model="form.opening_balance" type="number" placeholder="0.00" />
        </AppField>
        <AppField v-if="editorMode === 'edit' && showOpeningBalance" label="当前余额"
                  hint="期初余额 + 已确认流水净额（只读）">
          <AppInput :model-value="form.currentBalance" disabled />
        </AppField>
        <AppField label="纳入净资产"
                  :hint="isCreditAccount
                    ? '信用卡欠款会作为负项减少净资产；关闭后该卡不计入家庭净值'
                    : '关闭后该账户余额不计入家庭净资产'">
          <AppSwitch v-model="form.include_in_net_worth" label="计入净资产" />
        </AppField>
        <AppField label="备注" :error="fieldErrors.note">
          <AppInput v-model="form.note" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">
          {{ editorMode === 'create' ? '创建账户' : '保存修改' }}
        </AppButton>
      </template>
    </AppDrawer>

    <!-- 详情抽屉 -->
    <AppDrawer :open="detailOpen" title="账户详情" :width="560" @close="detailOpen = false">
      <StateBlock v-if="detailLoading" state="loading" />
      <template v-else-if="detail">
        <ul class="kv">
          <li><span class="k">账户名称</span><span class="v">{{ detail.name }}</span></li>
          <li><span class="k">类型 / 机构</span><span class="v">{{ detail.account_type_label }} · {{ detail.institution || '—' }}</span></li>
          <li><span class="k">币种</span><span class="v">{{ detail.currency }}</span></li>
          <template v-if="!hideBalanceRows">
            <li><span class="k">期初余额</span><span class="v">{{ formatAmount(detail.opening_balance, ui.amountPrecision) }}</span></li>
            <li><span class="k">当前余额</span><span class="v strong">{{ formatAmount(detail.balance, ui.amountPrecision) }}</span></li>
            <li><span class="k">可用余额</span><span class="v">{{ formatAmount(detail.available_balance, ui.amountPrecision) }}</span></li>
          </template>
          <template v-if="detail.credit_limit">
            <li>
              <span class="k">{{ detailProfile === 'credit_card' ? '信用额度' : detailProfile === 'loan_line' ? '借款额度' : '授信额度' }}</span>
              <span class="v">{{ formatAmount(detail.credit_limit, ui.amountPrecision) }}</span>
            </li>
            <li>
              <span class="k">{{ detailProfile === 'loan_line' ? '已借金额' : '已用额度' }}</span>
              <span class="v">{{ formatAmount(detail.credit_used || '0', ui.amountPrecision) }}</span>
            </li>
            <li><span class="k">当前可用额度</span><span class="v strong">{{ formatAmount(detail.available_credit, ui.amountPrecision) }}</span></li>
            <li v-if="detail.bill_day">
              <span class="k">账单日 / 还款日</span>
              <span class="v">{{ detail.bill_day }} 日 / {{ detail.due_day || '—' }} 日</span>
            </li>
            <li v-if="detail.card_expiry"><span class="k">卡片有效期</span><span class="v">{{ detail.card_expiry }}</span></li>
          </template>
          <li v-if="detail.interest_rate">
            <span class="k">年利率</span><span class="v">{{ fmtRate(detail.interest_rate) }}%</span>
          </li>
          <li v-if="detail.term_months"><span class="k">存期</span><span class="v">{{ detail.term_months }} 个月</span></li>
          <li v-if="detail.maturity_date">
            <span class="k">到期日</span><span class="v">{{ detail.maturity_date.slice(0, 10) }}</span>
          </li>
          <li><span class="k">纳入净资产</span><span class="v">{{ detail.include_in_net_worth ? '是' : '否' }}</span></li>
          <li><span class="k">状态</span><span class="v">{{ detail.status_label }}</span></li>
        </ul>

        <h4 class="sec">账户流水（最近 {{ detail.flow.length }} 条）</h4>
        <StateBlock v-if="!detail.flow.length" state="empty" title="暂无流水" description="该账户还没有已确认的资金变动。" />
        <ul v-else class="flow">
          <li v-for="f in detail.flow" :key="f.event_id">
            <div class="f-main">
              <strong>{{ f.title }}</strong>
              <small>{{ f.event_no }} · {{ fmt(f.occurred_at) }} · {{ f.status === 'confirmed' ? '已确认' : f.status }}</small>
            </div>
            <span class="f-amount" :class="f.direction">
              {{ f.direction === 'in' ? '+' : '−' }}{{ f.amount }} {{ f.currency }}
            </span>
          </li>
        </ul>

        <div class="drawer-actions">
          <AppButton variant="secondary" @click="router.push({ path: '/ledger/reconciliation', query: { account_id: detail.id } })">
            <Scale :size="15" /> 去对账
          </AppButton>
          <AppButton variant="secondary" @click="router.push({ path: '/ledger/transactions', query: { account_id: detail.id } })">
            查看全部流水
          </AppButton>
        </div>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.accounts { display: flex; flex-direction: column; gap: var(--space-4); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(196px, 1fr)); gap: var(--space-3); }
/* 卡片里只有标题+数字，之前用默认内边距显得又大又空 */
.stats :deep(.card-body) { padding: 12px 14px 13px; }
.stat .k {
  display: flex; align-items: center; gap: 6px;
  font-size: 12.5px; color: var(--ink-muted);
}
.stat .k .cnt { margin-left: auto; font-weight: 600; color: var(--ink-strong); }
.stat .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-muted); }
.stat.type-bank .dot { background: var(--primary); }
.stat.type-cash .dot { background: var(--success); }
.stat.type-e_wallet .dot { background: var(--accent-ai); }
.stat.type-other .dot { background: var(--ink-muted); }
.stat .n { font-size: 26px; font-weight: 700; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.stat .n small { font-size: 13px; font-weight: 500; color: var(--ink-muted); }
.stat .sum { margin-top: 4px; font-size: 22px; font-weight: 700; color: var(--ink-strong); letter-spacing: -0.01em; }
.stat .x-note { margin-top: 2px; font-size: 11px; color: var(--ink-muted); }
.stat-hero { background: var(--glass-2); }
.toolbar { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; width: 100%; }
.search {
  display: flex; align-items: center; gap: 6px; flex: 1; min-width: 180px;
  height: 34px; padding: 0 10px; border: 1px solid var(--border);
  border-radius: var(--glass-radius-control); background: var(--glass-1); color: var(--ink-muted);
}
.search input { flex: 1; border: none; background: transparent; color: var(--ink-strong); font-size: 13.5px; outline: none; }
.sel { width: 150px; }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act {
  display: inline-flex; align-items: center; gap: 4px;
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.inst-row { display: flex; flex-direction: column; gap: var(--space-2); }
.muted { color: var(--ink-muted); }
.kv { display: flex; flex-direction: column; }
.kv li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 9px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.kv .k { color: var(--ink-muted); }
.kv .v { color: var(--ink-strong); }
.kv .v.strong { font-size: 16px; font-weight: 600; }
.sec { margin: var(--space-5) 0 var(--space-2); font-size: 13.5px; color: var(--ink-strong); }
.flow { display: flex; flex-direction: column; gap: 8px; }
.flow li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.f-main { display: flex; flex-direction: column; min-width: 0; }
.f-main strong { font-size: 13.5px; color: var(--ink-strong); }
.f-main small { font-size: 11.5px; color: var(--ink-muted); }
.f-amount { font-variant-numeric: tabular-nums; font-size: 13.5px; white-space: nowrap; }
.f-amount.in { color: var(--success); }
.f-amount.out { color: var(--ink-strong); }
.drawer-actions { margin-top: var(--space-4); display: flex; justify-content: flex-end; }
@media (max-width: 700px) { .sel { width: 100%; } }

/* ---------------- 账户卡片视图 ---------------- */
.acc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
  gap: var(--space-4);
}
.acc-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 16px 12px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
  background: var(--glass-1);
  cursor: pointer;
  overflow: hidden;
  transition: transform var(--motion-fast) var(--ease-out),
              border-color var(--motion-fast) var(--ease-out),
              box-shadow var(--motion-fast) var(--ease-out);
}
.acc-card:hover {
  transform: translateY(-2px);
  border-color: var(--primary);
  box-shadow: var(--shadow-card);
}
.acc-card:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.acc-card.off { opacity: 0.62; }

/* 角标：右上角，颜色按账户类型区分 */
.corner {
  position: absolute;
  top: 0; right: 0;
  padding: 3px 12px 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #fff;
  border-bottom-left-radius: var(--radius-md);
  background: var(--ink-muted);
}
.corner.bank { background: var(--primary); }
.corner.cash { background: var(--success); }
.corner.e_wallet { background: var(--accent-ai); }
.corner.credit { background: var(--danger); }
.corner.other, .corner.off { background: var(--ink-muted); }

.acc-head { display: flex; align-items: center; gap: 10px; padding-right: 56px; }
.acc-brand { flex: none; }
.acc-title { min-width: 0; }
.acc-title h3 {
  margin: 0;
  font-size: 14.5px;
  font-weight: 600;
  color: var(--ink-strong);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.acc-title p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--ink-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-bal { margin-top: 2px; }
.acc-bal .k { margin: 0; font-size: 11.5px; color: var(--ink-muted); }
.acc-bal .v { font-size: 21px; font-weight: 650; color: var(--ink-strong); line-height: 1.3; }

.acc-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: 11.5px;
  color: var(--ink-muted);
}
.acc-due {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--ink);
  background: var(--glass-2);
}
.acc-due strong { margin-left: auto; font-weight: 600; color: var(--ink-strong); }
.acc-due.today { color: var(--danger); background: rgba(232, 93, 117, 0.12); }
.acc-due.today strong { color: var(--danger); }

.acc-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
@media (max-width: 640px) {
  .acc-grid { grid-template-columns: 1fr; }
}

/* 移动端：账户合计 + 各类型合计两列排布 */
@media (max-width: 640px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .stat :deep(.card-body) { padding: 11px 12px 12px; }
  .stat-hero { grid-column: 1 / -1; }
  .stat .n { font-size: 22px; }
  .stat .sum { font-size: 15.5px; }
}

</style>
