<script setup lang="ts">
/**
 * 负债页面 /ledger/liabilities（06 §6、15 §3.2/§5.2、M3）。
 * 列表显示债务名称、类型、剩余本金、利率、下次还款日、月度应还、状态；
 * 详情页签：概览、还款计划、还款记录、利息费用、关联资产、AI 分析；
 * 登记还款输入总额和账户，系统按本金/利息/费用拆分，用户可调整但需说明原因。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle, Briefcase, CalendarClock, Car, CreditCard, FileText, HandCoins, Home,
  Plus, RefreshCw, Search, ShoppingBag, Sparkles, TrendingDown,
} from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import { formatAmount } from '@/utils/format'
import AppButton from '@/components/ui/AppButton.vue'
import AppToolbar from '@/components/ui/AppToolbar.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppDateRangePicker from '@/components/ui/AppDateRangePicker.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppModal from '@/components/ui/AppModal.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import CategoryPicker from '@/components/CategoryPicker.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { fmtDate, type AccountLite, type Member } from './asset-shared'

interface Liability {
  id: string
  name: string
  liability_type: string
  liability_type_label: string
  category_id: string | null
  category_label: string
  creditor: string | null
  currency: string
  include_in_net_worth: boolean
  initial_amount: string
  opening_outstanding: string | null
  remaining_periods: number | null
  outstanding: string
  interest_rate: string | null
  rate_type: string | null
  rate_type_label: string
  rate_adjustment_cycle: string | null
  repayment_method: string | null
  repayment_method_label: string
  term_months: number | null
  start_date: string | null
  end_date: string | null
  payment_frequency: string
  payment_frequency_label: string
  payment_day: number | null
  monthly_payment: string | null
  first_payment_date: string | null
  next_payment_date: string | null
  linked_account_id: string | null
  linked_asset_id: string | null
  status: string
  status_label: string
  settled: boolean
  note: string | null
  version: number
  extension?: Record<string, unknown> | null
}

interface LiabilityDetail extends Liability {
  repayments: {
    id: string; amount: string; principal: string; interest: string; fee: string
    occurred_at: string | null; account_id: string | null; reason: string | null
  }[]
  repayment_plan: {
    period: number; total_periods: number | null; due_date: string
    principal: string; interest: string; payment: string; remaining: string; status: string
  }[]
  repayment_plan_meta: {
    method_label: string; frequency_label: string; periods: number
    total_periods: number; paid_periods: number; per_period: string | null; caliber: string
  }
  interest_summary: { total_interest: string; caliber: string }
  interest_events: {
    id: string; event_no: string; title: string
    category_id: string | null; category_label: string
    amount: string; currency: string; occurred_at: string | null
  }[]
  linked_asset: { id: string; name: string; asset_type: string; asset_type_label: string; current_value: string; currency: string } | null
  ai: { allowed: string[]; forbidden: string[] }
}

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const rows = ref<Liability[]>([])
const summary = ref<Record<string, any> | null>(null)
const members = ref<Member[]>([])
const accounts = ref<AccountLite[]>([])
const realEstates = ref<{ id: string; name: string }[]>([])
const vehicles = ref<{ id: string; name: string }[]>([])

const keyword = ref('')
const typeFilter = ref('')
const categoryFilter = ref('')
const statusFilter = ref('')
const categories = ref<{ id: string; name: string }[]>([])

const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const detailOpen = ref(false)
const detail = ref<LiabilityDetail | null>(null)

/** 详情里的类型扩展字段：键名转中文、贷款方式用枚举标签而不是原始 code */
const EXT_LABELS: Record<string, string> = {
  contract_no: '合同编号',
  prepayment_rules: '提前还款规则',
  note: '补充说明',
}
const extRows = computed(() => {
  const ext = (detail.value?.extension || {}) as Record<string, any>
  const rows: { key: string; label: string; value: string }[] = []
  for (const [key, value] of Object.entries(ext)) {
    if (key === 'loan_method_label') continue
    // 利率调整周期已提到主字段区展示，这里不重复
    if (key === 'rate_adjustment_cycle') continue
    if (key === 'loan_method') {
      rows.push({ key, label: '贷款方式', value: String(ext.loan_method_label || '—') })
      continue
    }
    rows.push({ key, label: EXT_LABELS[key] || key, value: value ? String(value) : '—' })
  }
  return rows
})
const detailLoading = ref(false)
const activeTab = ref('overview')

const repayOpen = ref(false)
const repaySaving = ref(false)
const repayErrors = ref<Record<string, string>>({})
const repayAdjust = ref(false)
const repayForm = reactive({
  amount: '', account_id: '', principal: '', interest: '', fee: '',
  occurred_at: '', reason: '', note: '',
})

const drawOpen = ref(false)
const drawSaving = ref(false)
const drawErrors = ref<Record<string, string>>({})
const drawForm = reactive({ amount: '', account_id: '', occurred_at: '', note: '' })

const form = reactive({
  id: '', version: 1, name: '', liability_type: 'personal', category_id: '', creditor: '', currency: 'CNY',
  initial_amount: '', opening_outstanding: '', remaining_periods: '', interest_rate: '', rate_type: '',
  repayment_method: 'equal_installment',
  term_months: '', start_date: '', end_date: '', payment_frequency: 'monthly', payment_day: '',
  monthly_payment: '', first_payment_date: '', linked_account_id: '', linked_asset_id: '',
  member_id: '', note: '', include_in_net_worth: true,
  ext_loan_method: '', ext_contract_no: '', ext_rate_adjustment_cycle: '', ext_prepayment_rules: '',
  ext_credit_limit: '', ext_bill_day: '', ext_due_day: '', ext_card_no: '', ext_issuer: '',
})

/** 卡片外观：图标与角标配色按债务类型区分 */
const LIABILITY_LOOK: Record<string, { icon: unknown; tone: string }> = {
  mortgage: { icon: Home, tone: 'primary' },
  vehicle_loan: { icon: Car, tone: 'success' },
  consumer_loan: { icon: ShoppingBag, tone: 'ai' },
  business_loan: { icon: Briefcase, tone: 'warning' },
  personal: { icon: HandCoins, tone: 'neutral' },
  credit_card: { icon: CreditCard, tone: 'danger' },
  other: { icon: FileText, tone: 'neutral' },
}
function lookOf(type: string) {
  return LIABILITY_LOOK[type] || LIABILITY_LOOK.other
}

const canCreate = computed(() => auth.can('create_draft'))
const canEdit = computed(() => auth.can('edit_draft'))
const canConfirm = computed(() => auth.can('confirm'))

/** 还款计划一次展示 24 期，可继续展开 */
const planLimit = ref(24)
const visiblePlan = computed(() => (detail.value?.repayment_plan || []).slice(0, planLimit.value))
/** 未还合计：把计划里各期应还加总（仅当前剩余期数） */
const planUnpaidTotal = computed(() => {
  const rows = detail.value?.repayment_plan || []
  const total = rows.reduce((sum, row) => sum + Number(row.payment || 0), 0)
  return Number.isFinite(total) ? total.toFixed(ui.amountPrecision) : '0'
})

/** 提前还款测算：一次性还 X 后，「缩短年限」与「减少月供」各能省多少利息 */
interface PrepayPlan {
  periods: number
  saved_periods?: number
  monthly_payment: string
  total_interest: string
  saved_interest: string
  available: boolean
  monthly_reduction?: string
}
interface PrepayResult {
  outstanding: string
  amount: string
  new_outstanding: string
  currency: string
  base: { periods: number; monthly_payment: string; total_interest: string }
  shorten_term: PrepayPlan
  reduce_payment: PrepayPlan
  caliber: string
}
const prepayAmount = ref('')
const prepayLoading = ref(false)
const prepayResult = ref<PrepayResult | null>(null)

async function runPrepaySimulation() {
  const raw = Number(prepayAmount.value)
  if (!raw || raw <= 0) {
    toast.push('warning', '请先填写提前还款金额')
    return
  }
  prepayLoading.value = true
  try {
    prepayResult.value = await api.get<PrepayResult>(
      `/ledger/liabilities/${detail.value?.id}/prepay-simulation?amount=${encodeURIComponent(raw)}`,
    )
  } catch (err) {
    toast.push('error', errorMessage(err))
  } finally {
    prepayLoading.value = false
  }
}

const typeOptions = [  { value: '', label: '全部类型' },
  ...['mortgage', 'vehicle_loan', 'consumer_loan', 'business_loan', 'personal', 'credit_card', 'other']
    .map((v) => ({ value: v, label: zh('liability_type', v) })),
]
const categoryOptions = [
  { value: '', label: '全部分类' },
  ...categories.value.map((c) => ({ value: c.id, label: c.name })),
]
const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'active', label: '正常' },
  { value: 'overdue', label: '逾期' },
  { value: 'settled', label: '已结清' },
]
// 信用卡不属于债务：它的欠款在「账户」里以负余额表达，这里只保留真正的债务类型
const formTypeOptions = [
  'mortgage', 'vehicle_loan', 'consumer_loan', 'business_loan', 'personal', 'other',
].map((v) => ({ value: v, label: zh('liability_type', v) }))

const loanMethodOptions = [
  { value: '', label: '未设置' },
  ...['commercial', 'provident_fund', 'combined', 'other'].map((v) => ({
    value: v, label: zh('loan_method', v),
  })),
]
const rateTypeOptions = [
  { value: '', label: '未设置' },
  ...['fixed', 'floating', 'lpr_plus', 'other'].map((v) => ({ value: v, label: zh('rate_type', v) })),
]
const methodOptions = [
  { value: '', label: '未设置' },
  ...['equal_installment', 'equal_principal', 'interest_first', 'lump_sum', 'other'].map((v) => ({
    value: v, label: zh('repayment_method', v),
  })),
]
const frequencyOptions = ['monthly', 'quarterly', 'half_yearly', 'yearly', 'weekly', 'one_time'].map((v) => ({
  value: v, label: zh('payment_frequency', v),
}))
const currencyOptions = [
  { value: 'CNY', label: '人民币 CNY' },
  { value: 'USD', label: '美元 USD' },
  { value: 'HKD', label: '港币 HKD' },
  { value: 'EUR', label: '欧元 EUR' },
]

// ---------------- 债务类型 → 用途分类联动（只显示相关的一级分类及其子分类） ----------------
/** 每种债务类型允许的「用途」一级分类；「其他」始终可选，空数组表示不限。 */
const TYPE_CATEGORY_ROOTS: Record<string, string[]> = {
  mortgage: ['购房', '其他'],
  vehicle_loan: ['购车', '其他'],
  consumer_loan: ['装修', '教育', '医疗', '日常周转', '其他'],
  business_loan: ['经营周转', '其他'],
  personal: ['装修', '教育', '医疗', '日常周转', '其他'],
  other: [],
}
const categoryRoots = computed(() => TYPE_CATEGORY_ROOTS[form.liability_type] || [])

/** 切换债务类型：分类的作用域变了，清空已选分类避免类型与用途错配 */
function onLiabilityTypeChange(value: string) {
  if (!value || value === form.liability_type) return
  form.liability_type = value
  form.category_id = ''
  if (!needsLoanMethod.value) form.ext_loan_method = ''
  if (!needsAssetLink.value) form.linked_asset_id = ''
}

// ---------------- 贷款方式 / 关联资产（放在债务名称之后，先描述「这笔钱怎么借的」） ----------------
const needsLoanMethod = computed(
  // 只有房贷 / 车贷有「商业 / 公积金 / 组合」之分，其余债务不显示该项
  () => ['mortgage', 'vehicle_loan'].includes(form.liability_type),
)
const needsContract = computed(() => ['mortgage', 'vehicle_loan'].includes(form.liability_type))

// ---------------- 债权机构：常见机构候选 + 手动填写 ----------------
const CUSTOM_CREDITOR = '__custom__'
const creditorCustom = ref(false)
const CREDITOR_PRESETS = [
  '中国工商银行', '中国农业银行', '中国银行', '中国建设银行', '交通银行',
  '中国邮政储蓄银行', '招商银行', '浦发银行', '中信银行', '中国民生银行',
  '兴业银行', '中国光大银行', '华夏银行', '平安银行', '广发银行',
  '北京银行', '上海银行', '宁波银行',
  '支付宝（花呗 / 借呗）', '京东（白条 / 金条）', '微信（微粒贷）',
  '住房公积金管理中心', '汽车金融公司', '亲友',
]
/** 候选 = 常见机构 + 已录入过的机构（去重） */
const creditorOptions = computed(() => {
  const history = rows.value.map((r) => (r.creditor || '').trim()).filter(Boolean)
  return [
    { value: '', label: '未选择' },
    ...[...new Set([...CREDITOR_PRESETS, ...history])].map((v) => ({ value: v, label: v })),
    { value: CUSTOM_CREDITOR, label: '其他（手动填写）' },
  ]
})
const creditorSelectValue = computed(() =>
  creditorCustom.value ? CUSTOM_CREDITOR : (form.creditor || ''),
)
function onCreditorChange(value: string) {
  if (value === CUSTOM_CREDITOR) {
    creditorCustom.value = true
    form.creditor = ''
  } else {
    creditorCustom.value = false
    form.creditor = value
  }
}

// ---------------- 利率调整周期：改成可选项（原来是一句话自由输入） ----------------
const RATE_CYCLE_OPTIONS = [
  { value: 'next_period', label: '次期调整（下期生效）' },
  { value: 'next_year', label: '次年对应日调整' },
  { value: 'jan_1', label: '每年 1 月 1 日调整' },
  { value: 'quarterly', label: '每季度调整' },
  { value: 'monthly', label: '每月调整' },
  { value: 'fixed', label: '固定不变' },
  { value: 'other', label: '其他（自定义）' },
]
/** 存量数据可能是自由文本（如「每年 1 月 1 日」），作为额外选项保留，避免编辑时丢失 */
const rateCycleOptions = computed(() => {
  const current = (form.ext_rate_adjustment_cycle || '').trim()
  if (!current) return [{ value: '', label: '未设置' }, ...RATE_CYCLE_OPTIONS]
  if (RATE_CYCLE_OPTIONS.some((o) => o.value === current)) return RATE_CYCLE_OPTIONS
  return [{ value: current, label: `${current}（已录入）` }, ...RATE_CYCLE_OPTIONS]
})

// ---------------- 每期应还：按金额 / 利率 / 期限 / 还款方式自动测算 ----------------
const PER_YEAR: Record<string, number> = {
  monthly: 12, quarterly: 4, half_yearly: 2, yearly: 1, weekly: 52, one_time: 1,
}
/** 利率调整周期的展示名（兼容存量的自由文本） */
function cycleLabel(value: string | null) {
  if (!value) return '—'
  return RATE_CYCLE_OPTIONS.find((o) => o.value === value)?.label || value
}
const loanPrincipal = computed(() => Number(form.opening_outstanding || form.initial_amount || 0))
const periodCount = computed(() => {
  const perYear = PER_YEAR[form.payment_frequency] || 12
  // 优先「剩余期数」：存量贷款只剩多少期就按多少期测算，结果才和银行账单一致
  const remaining = Number(form.remaining_periods || 0)
  if (remaining > 0) return Math.round(remaining)
  const term = Number(form.term_months || 0)
  if (!term) return 0
  return Math.max(0, Math.round((term * perYear) / 12))
})
/** 自动计算的每期应还（等额本息为整期固定值，等额本金取首期，先息后本取利息） */
const computedPayment = computed(() => {
  const principal = loanPrincipal.value
  const n = periodCount.value
  if (!(principal > 0) || n <= 0) return ''
  const perYear = PER_YEAR[form.payment_frequency] || 12
  const rate = Number(form.interest_rate || 0) / 100 / perYear
  let payment = 0
  switch (form.repayment_method) {
    case 'equal_principal':
      payment = principal / n + principal * rate
      break
    case 'interest_first':
      payment = principal * rate
      break
    case 'lump_sum':
      payment = principal + principal * rate * n
      break
    case 'other':
      return ''
    default:
      payment = rate === 0
        ? principal / n
        : (principal * rate * (1 + rate) ** n) / ((1 + rate) ** n - 1)
  }
  if (!Number.isFinite(payment) || payment <= 0) return ''
  return payment.toFixed(ui.amountPrecision)
})
/** 用户是否手动改过「每期应还」：改过之后不再用后端测算值覆盖 */
const paymentTouched = ref(false)
function onPaymentInput(value: string) {
  paymentTouched.value = true
  form.monthly_payment = value
}
/** 放弃手填，交给后端按「本金/利率/期数/方式」测算（B-6：计算权在后端） */
function useComputedPayment() {
  paymentTouched.value = false
  form.monthly_payment = ''
}
// 说明：不再把前端测算值自动回填进 monthly_payment——那样会把 JS 浮点结果当成
// 「用户手填值」提交，污染还款计划与月供汇总。computedPayment 仅作输入框下方的
// 「按测算」提示；用户不点它、留空提交，后端会自动用 Decimal 计算回填。
// 改了测算的输入（金额/期数/利率/方式/频率）就重新自动测算，避免旧值把新结果挡住；
// 打开表单时的那一次批量赋值不算「用户改动」（否则编辑会悄悄覆盖银行给的金额）
const paymentInputs = () => [
  form.opening_outstanding, form.initial_amount, form.remaining_periods,
  form.term_months, form.interest_rate, form.repayment_method, form.payment_frequency,
].join('|')
let paymentBaseline = ''
watch(paymentInputs, (sig) => {
  if (sig === paymentBaseline) return
  paymentBaseline = sig
  paymentTouched.value = false
})

const assetOptions = computed(() => {
  if (form.liability_type === 'mortgage') {
    return [{ value: '', label: '请选择不动产' }, ...realEstates.value.map((a) => ({ value: a.id, label: a.name }))]
  }
  if (form.liability_type === 'vehicle_loan') {
    return [{ value: '', label: '请选择车辆' }, ...vehicles.value.map((a) => ({ value: a.id, label: a.name }))]
  }
  return []
})
const needsAssetLink = computed(() => ['mortgage', 'vehicle_loan'].includes(form.liability_type))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, household, acc, re, vh, cats] = await Promise.all([
      api.get<{ items: Liability[]; summary: Record<string, any> }>('/ledger/liabilities', {
        keyword: keyword.value || undefined,
        liability_type: typeFilter.value || undefined,
        category_id: categoryFilter.value || undefined,
        status: statusFilter.value || undefined,
      }),
      api.get<{ members: Member[] }>('/settings/household'),
      api.get<{ items: AccountLite[] }>('/ledger/accounts'),
      api.get<{ items: { id: string; name: string }[] }>('/ledger/assets/real-estates'),
      api.get<{ items: { id: string; name: string }[] }>('/ledger/assets/vehicles'),
      api.get<{ items: { id: string; name: string; status: string }[] }>('/settings/categories', { domain: 'liability' }),
    ])
    rows.value = list.items
    summary.value = list.summary
    members.value = household.members || []
    accounts.value = (acc.items || []).filter((a) => a.status === 'active')
    realEstates.value = re.items || []
    vehicles.value = vh.items || []
    categories.value = (cats.items || []).filter((c) => c.status === 'active')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function buildExtension() {
  if (form.liability_type === 'mortgage') {
    return {
      loan_method: form.ext_loan_method || null,
      contract_no: form.ext_contract_no || null,
      rate_adjustment_cycle: form.ext_rate_adjustment_cycle || null,
      prepayment_rules: form.ext_prepayment_rules || null,
    }
  }
  if (form.liability_type === 'vehicle_loan') {
    return {
      contract_no: form.ext_contract_no || null,
      loan_method: form.ext_loan_method || null,
    }
  }
  if (form.liability_type === 'credit_card') {
    return {
      credit_limit: form.ext_credit_limit || '0',
      bill_day: form.ext_bill_day === '' ? null : form.ext_bill_day,
      due_day: form.ext_due_day === '' ? null : form.ext_due_day,
      card_no: form.ext_card_no || null,
      issuer: form.ext_issuer || form.creditor || null,
    }
  }
  return undefined
}

function openCreate(presetType?: string) {
  fieldErrors.value = {}
  Object.assign(form, {
    id: '', version: 1, name: '', liability_type: presetType || 'personal', category_id: '', creditor: '',
    currency: 'CNY', initial_amount: '', interest_rate: '', rate_type: '',
    repayment_method: 'equal_installment', term_months: '', start_date: '', end_date: '',
    payment_frequency: 'monthly', payment_day: '', monthly_payment: '', first_payment_date: '',
    linked_account_id: '', linked_asset_id: '', member_id: '', note: '', include_in_net_worth: true,
    ext_loan_method: '', ext_contract_no: '', ext_rate_adjustment_cycle: '', ext_prepayment_rules: '',
    ext_credit_limit: '', ext_bill_day: '', ext_due_day: '', ext_card_no: '', ext_issuer: '',
  })
  paymentBaseline = paymentInputs()
  paymentTouched.value = false
  editorMode.value = 'create'
  editorOpen.value = true
}

function openEdit(row: Liability) {
  fieldErrors.value = {}
  const ext = (row.extension || {}) as Record<string, any>
  Object.assign(form, {
    id: row.id, version: row.version, name: row.name, liability_type: row.liability_type,
    category_id: row.category_id || '', creditor: row.creditor || '', currency: row.currency,
    initial_amount: row.initial_amount,
    opening_outstanding: row.opening_outstanding ?? '',
    remaining_periods: row.remaining_periods ?? '',
    interest_rate: row.interest_rate || '', rate_type: row.rate_type || '',
    repayment_method: row.repayment_method || '', term_months: row.term_months ?? '',
    start_date: row.start_date ? row.start_date.slice(0, 10) : '',
    end_date: row.end_date ? row.end_date.slice(0, 10) : '',
    payment_frequency: row.payment_frequency, payment_day: row.payment_day ?? '',
    monthly_payment: row.monthly_payment || '',
    first_payment_date: row.first_payment_date ? row.first_payment_date.slice(0, 10) : '',
    linked_account_id: row.linked_account_id || '', linked_asset_id: row.linked_asset_id || '',
    member_id: '', note: row.note || '',
    include_in_net_worth: row.include_in_net_worth !== false,
    ext_loan_method: (ext.loan_method as string) || '',
    ext_contract_no: (ext.contract_no as string) || '',
    ext_rate_adjustment_cycle: (ext.rate_adjustment_cycle as string) || row.rate_adjustment_cycle || '',
    ext_prepayment_rules: (ext.prepayment_rules as string) || '',
    ext_credit_limit: '', ext_bill_day: '', ext_due_day: '', ext_card_no: '', ext_issuer: '',
  })
  // 编辑时保留账单上的实际月供：把当前输入记成基线，并视为「用户已定」，不再被测算值覆盖
  paymentBaseline = paymentInputs()
  paymentTouched.value = true
  editorMode.value = 'edit'
  editorOpen.value = true
}

async function save() {
  fieldErrors.value = {}
  if (!form.name.trim()) {
    fieldErrors.value = { name: '请填写债务名称' }
    return
  }
  if (!form.initial_amount) {
    fieldErrors.value = { initial_amount: '请填写贷款金额' }
    return
  }
  saving.value = true
  const body: Record<string, unknown> = {
    name: form.name,
    liability_type: form.liability_type,
    category_id: form.category_id || null,
    creditor: form.creditor || null,
    currency: form.currency,
    initial_amount: form.initial_amount || '0',
    opening_outstanding: form.opening_outstanding === '' ? null : form.opening_outstanding,
    remaining_periods: form.remaining_periods === '' ? null : form.remaining_periods,
    rate_adjustment_cycle: form.ext_rate_adjustment_cycle || null,
    interest_rate: form.interest_rate || null,
    rate_type: form.rate_type || null,
    repayment_method: form.repayment_method || null,
    term_months: form.term_months === '' ? null : form.term_months,
    start_date: form.start_date || null,
    end_date: form.end_date || null,
    payment_frequency: form.payment_frequency,
    payment_day: form.payment_day === '' ? null : form.payment_day,
    // 每期应还：用户显式手填才提交；否则交后端按本金/利率/期数/方式确定性计算，
    // 避免前端 JS 浮点计算的分位误差污染还款计划与月供汇总（B-6）。
    monthly_payment: paymentTouched.value && form.monthly_payment ? form.monthly_payment : null,
    first_payment_date: form.first_payment_date || null,
    linked_account_id: form.linked_account_id || null,
    linked_asset_id: form.linked_asset_id || null,
    member_id: form.member_id || null,
    note: form.note || null,
    include_in_net_worth: form.include_in_net_worth,
  }
  const extension = buildExtension()
  if (extension) body.extension = extension
  try {
    if (editorMode.value === 'create') {
      await api.post('/ledger/liabilities', body)
      toast.success('负债已创建')
    } else {
      body.version = form.version
      await api.put(`/ledger/liabilities/${form.id}`, body)
      toast.success('负债已更新')
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

async function openDetail(row: Liability) {
  detailOpen.value = true
  planLimit.value = 24
  detailLoading.value = true
  detail.value = null
  activeTab.value = 'overview'
  try {
    detail.value = await api.get<LiabilityDetail>(`/ledger/liabilities/${row.id}`)
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    detailLoading.value = false
  }
}

async function reloadDetail() {
  if (!detail.value) return
  detail.value = await api.get<LiabilityDetail>(`/ledger/liabilities/${detail.value.id}`)
  await load()
}

function openRepay() {
  repayErrors.value = {}
  repayAdjust.value = false
  Object.assign(repayForm, {
    amount: detail.value?.repayment_plan_meta?.per_period || detail.value?.monthly_payment || '', account_id: detail.value?.linked_account_id || '',
    principal: '', interest: '', fee: '', occurred_at: '', reason: '', note: '',
  })
  repayOpen.value = true
}

async function submitRepay() {
  if (!detail.value) return
  repaySaving.value = true
  repayErrors.value = {}
  const body: Record<string, unknown> = {
    amount: repayForm.amount,
    account_id: repayForm.account_id,
    fee: repayForm.fee || '0',
    occurred_at: repayForm.occurred_at || null,
    reason: repayForm.reason || null,
    note: repayForm.note || null,
  }
  if (repayAdjust.value) {
    body.principal = repayForm.principal || '0'
    body.interest = repayForm.interest || '0'
  }
  try {
    await api.post(`/ledger/liabilities/${detail.value.id}/repayments`, body)
  } catch (err) {
    repayErrors.value = errorFields(err)
    toast.error(errorMessage(err))
    return
  } finally {
    repaySaving.value = false
  }
  // 还款已经入账：后续刷新失败只能提示「刷新失败」，绝不能报成「登记失败」，
  // 否则用户会再登记一次，造成重复还款。
  toast.success('还款已登记：本金不计支出，利息与费用计入支出')
  repayOpen.value = false
  try {
    await reloadDetail()
  } catch (err) {
    toast.error(`还款已登记，但详情刷新失败：${errorMessage(err)}`)
  }
}

function openDraw() {
  drawErrors.value = {}
  Object.assign(drawForm, {
    amount: '', account_id: detail.value?.linked_account_id || '', occurred_at: '', note: '',
  })
  drawOpen.value = true
}

async function submitDraw() {
  if (!detail.value) return
  drawSaving.value = true
  drawErrors.value = {}
  try {
    await api.post(`/ledger/liabilities/${detail.value.id}/draws`, {
      amount: drawForm.amount, account_id: drawForm.account_id,
      occurred_at: drawForm.occurred_at || null, note: drawForm.note || null,
    })
  } catch (err) {
    drawErrors.value = errorFields(err)
    toast.error(errorMessage(err))
    return
  } finally {
    drawSaving.value = false
  }
  // 提款已经入账：刷新失败不能提示成「登记失败」，否则容易重复提款
  toast.success('借款已到账：账户增加、负债本金增加，不计入收入')
  drawOpen.value = false
  try {
    await reloadDetail()
  } catch (err) {
    toast.error(`借款已登记，但详情刷新失败：${errorMessage(err)}`)
  }
}

async function remove(row: Liability) {
  const res = await ui.askConfirm({
    title: '删除负债',
    message: `仅允许删除已结清的负债；删除为软删除并保留审计。目标：${row.name}。`,
    confirmText: '确认删除', danger: true, requireReason: true, reasonLabel: '删除原因（必填）',
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'liability.delete', object_type: 'liability', object_id: row.id,
    })
    await api.delete(`/ledger/liabilities/${row.id}`, {
      reason: res.reason, confirm_token: tokenRes.token,
    })
    toast.success('负债已删除，历史保留')
    detailOpen.value = false
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

function statusTone(status: string) {
  if (status === 'settled') return 'success' as const
  if (status === 'overdue') return 'danger' as const
  return 'warning' as const
}

function progressPercent(row: Liability) {
  const initial = Number(row.initial_amount || 0)
  const outstanding = Number(row.outstanding || 0)
  if (initial <= 0) return 0
  return Math.max(0, Math.min(100, ((initial - outstanding) / initial) * 100))
}

async function openById(id: string) {
  try {
    const data = await api.get<Liability>(`/ledger/liabilities/${id}`)
    await openDetail(data)
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

onMounted(async () => {
  await load()
  // 从「新增资产」勾选房贷 / 车贷后跳转过来：直接打开新增表单并带上关联资产
  if (route.query.new === '1') {
    const type = String(route.query.type || '')
    openCreate(type === 'mortgage' || type === 'vehicle_loan' ? type : undefined)
    const assetId = String(route.query.asset_id || '')
    if (assetId) form.linked_asset_id = assetId
    const name = String(route.query.name || '')
    if (name) form.name = name
    void router.replace({ path: route.path })
    return
  }
  const openId = route.query.open
  if (typeof openId === 'string' && openId) await openById(openId)
})
</script>

<template>
  <div class="liab">
    <PageHeader
      title="负债"
      subtitle="剩余本金 = 初始金额 + 已确认资金变动；还款按本金/利息/费用拆分，本金不重复计入支出。"
      :breadcrumb="['家庭台账', '负债']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiAnalysis('liabilities', 'AI 偿付压力分析')">
        <Sparkles :size="15" /> AI 偿付压力分析
      </AppButton>
      <AppButton v-if="canCreate" variant="primary" @click="openCreate()">
        <Plus :size="15" /> 新增负债
      </AppButton>
    </PageHeader>

    <section v-if="summary" class="stats">
      <StatCard
        :icon="CreditCard" tone="danger"
        :label="`负债总额（${summary.base_currency}）`"
        :value="summary.total_outstanding" :currency="summary.base_currency"
        :hint="`共 ${summary.count} 笔 · 剩余本金口径`"
      />
      <StatCard :icon="CalendarClock" tone="warning" label="月度应还" hint="按已填月供汇总">
        <template #value>
          <MoneyText
            v-if="summary.monthly_due_count" :value="summary.monthly_due"
            :currency="summary.base_currency" show-currency
          />
          <template v-else>—<small class="unit-muted">未填月供</small></template>
        </template>
      </StatCard>
      <StatCard
        :icon="AlertTriangle" tone="danger" label="逾期 / 已结清"
        :value="`${summary.overdue_count} / ${summary.settled_count}`" plain unit="笔"
        hint="逾期笔数 / 已还清笔数"
      />
      <StatCard
        :icon="FileText" tone="neutral" label="负债笔数"
        :value="summary.count" plain unit="笔"
        hint="含信用卡、贷款与个人借款"
      />
    </section>
    <p v-if="summary?.multi_currency" class="multi">
      存在多币种负债，顶部统计仅展示基础币种 {{ summary.base_currency }}；不同币种不做相加。
    </p>

    <AppCard v-if="summary?.upcoming?.length" title="到期日历（最近）">
      <ul class="upcoming">
        <li v-for="item in summary.upcoming" :key="item.liability_id">
          <div class="up-main">
            <strong>{{ item.name }}</strong>
            <small>{{ item.liability_type_label }} · 下次还款 {{ fmtDate(item.next_payment_date) }}</small>
          </div>
          <div class="up-right">
            <MoneyText
              :value="item.monthly_payment || item.outstanding"
              :currency="item.currency" show-currency class="amount"
            />
            <StatusBadge :label="item.status_label" :tone="statusTone(item.status)" />
          </div>
        </li>
      </ul>
    </AppCard>

    <AppCard>
      <template #header>
        <AppToolbar>
          <div class="search">
            <Search :size="15" />
            <input v-model="keyword" placeholder="搜索债务名称" @keyup.enter="load" />
          </div>
          <AppSelect v-model="typeFilter" :options="typeOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="categoryFilter" :options="categoryOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="statusFilter" :options="statusOptions" class="sel" @update:model-value="load" />
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
      <StateBlock
        v-else-if="!rows.length" state="empty" title="还没有负债"
        description="登记房贷、车贷、消费贷、经营贷或亲友借款后，可以跟踪剩余本金、月供与利息费用。"
      >
        <template #actions>
          <AppButton v-if="canCreate" variant="primary" @click="openCreate()">
            <Plus :size="15" /> 新增负债
          </AppButton>
        </template>
      </StateBlock>

      <div v-else class="card-grid">
        <article
          v-for="row in rows"
          :key="row.id"
          class="entity-card"
          :class="{ 'is-muted': row.status === 'settled' }"
          role="button"
          tabindex="0"
          @click="openDetail(row)"
          @keyup.enter="openDetail(row)"
        >
          <span class="entity-corner" :class="`tone-${lookOf(row.liability_type).tone}`">
            {{ row.liability_type_label }}
          </span>

          <header class="entity-head">
            <span class="entity-icon" :class="`tone-${lookOf(row.liability_type).tone}`">
              <component :is="lookOf(row.liability_type).icon" :size="18" />
            </span>
            <div class="entity-title">
              <h3>{{ row.name }}</h3>
              <p>{{ row.creditor || row.category_label || row.liability_type_label }}</p>
            </div>
          </header>

          <div class="entity-value">
            <p class="k">剩余本金</p>
            <p class="v">
              <MoneyText :value="row.outstanding" :currency="row.currency" show-currency />
            </p>
          </div>

          <div
            v-if="row.next_payment_date && row.status !== 'settled'"
            class="entity-note"
            :class="{ danger: row.status === 'overdue' }"
          >
            <CalendarClock :size="13" />
            <span>{{ row.status === 'overdue' ? '已逾期' : '下次还款' }}</span>
            <strong>{{ fmtDate(row.next_payment_date) }}</strong>
          </div>

          <div class="entity-meta">
            <span>年利率 <b>{{ row.interest_rate ? `${Number(row.interest_rate)}%` : '—' }}</b></span>
            <span v-if="row.monthly_payment">月供 <b>{{ formatAmount(row.monthly_payment, ui.amountPrecision) }}</b></span>
            <span v-if="row.repayment_method_label">{{ row.repayment_method_label }}</span>
            <span>{{ row.status_label }}</span>
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
      :open="editorOpen" :title="editorMode === 'create' ? '新增负债' : '编辑负债'" :width="600"
      @close="editorOpen = false"
    >
      <div class="form">
        <AppField label="债务类型" required :error="fieldErrors.liability_type"
                  hint="债务指房贷、车贷、消费贷与借款；信用卡欠款在「账户」里维护，不计入债务">
          <AppSelect
            :model-value="form.liability_type" :options="formTypeOptions"
            :disabled="editorMode === 'edit'" @update:model-value="onLiabilityTypeChange"
          />
        </AppField>
        <CategoryPicker
          v-model="form.category_id" domain="liability" :include-roots="categoryRoots"
          :error="fieldErrors.category_id"
          :hint="categoryRoots.length
            ? `只显示与「${zh('liability_type', form.liability_type)}」相关的用途分类；可点「＋」新增`
            : '可点「＋」新增分类；子分类直接可选'"
        />
        <AppField label="债务名称" required :error="fieldErrors.name">
          <AppInput v-model="form.name" placeholder="例如：住房贷款 / 亲友借款" />
        </AppField>

        <!-- 贷款方式与关联资产紧跟在名称之后：先说明「怎么借的、抵押了什么」 -->
        <div v-if="needsLoanMethod || needsAssetLink" class="grid-2">
          <AppField v-if="needsLoanMethod" label="贷款方式" :error="fieldErrors['extension.loan_method']"
                    hint="组合贷建议拆成两笔分别记录">
            <AppSelect v-model="form.ext_loan_method" :options="loanMethodOptions" />
          </AppField>
          <AppField v-if="needsAssetLink" label="关联资产" required :error="fieldErrors.linked_asset_id">
            <AppSelect v-model="form.linked_asset_id" :options="assetOptions" />
          </AppField>
        </div>

        <!-- 合同与利率规则 -->
        <div v-if="needsContract" class="grid-2">
          <AppField label="贷款合同编号">
            <AppInput v-model="form.ext_contract_no" placeholder="借款合同编号" />
          </AppField>
          <AppField label="利率调整周期" hint="LPR 重定价时按所选周期生效">
            <AppSelect v-model="form.ext_rate_adjustment_cycle" :options="rateCycleOptions" />
          </AppField>
        </div>

        <!-- 金额：贷款金额与当前剩余分开，存量老贷款也能录准 -->
        <div class="grid-2">
          <AppField label="贷款金额" required :error="fieldErrors.initial_amount"
                    hint="合同 / 放款金额，用于展示与测算">
            <AppInput v-model="form.initial_amount" type="number" placeholder="0.00" />
          </AppField>
          <AppField label="剩余金额" :error="fieldErrors.opening_outstanding"
                    hint="当前还欠多少；留空表示与贷款金额相同">
            <AppInput v-model="form.opening_outstanding" type="number" placeholder="留空 = 贷款金额" />
          </AppField>
        </div>

        <AppField label="债权机构" :error="fieldErrors.creditor" hint="可选择常见银行与平台，也可手动填写">
          <div class="inst-row">
            <AppSelect :model-value="creditorSelectValue" :options="creditorOptions"
                       @update:model-value="onCreditorChange" />
            <AppInput v-if="creditorCustom" v-model="form.creditor" placeholder="请输入债权机构名称" />
          </div>
        </AppField>

        <div class="grid-2">
          <AppField label="年利率（%）" :error="fieldErrors.interest_rate">
            <AppInput v-model="form.interest_rate" type="number" placeholder="例如：4.2" />
          </AppField>
          <AppField label="利率类型" :error="fieldErrors.rate_type">
            <AppSelect v-model="form.rate_type" :options="rateTypeOptions" />
          </AppField>
        </div>

        <AppField label="还款方式" :error="fieldErrors.repayment_method">
          <AppSelect v-model="form.repayment_method" :options="methodOptions" />
        </AppField>

        <div class="grid-2">
          <AppField label="贷款年限（月）" hint="合同总期限，用于算总期数">
            <AppInput v-model="form.term_months" type="number" placeholder="例如：240" />
          </AppField>
          <AppField label="剩余期数" :error="fieldErrors.remaining_periods"
                    hint="当前还剩多少期，决定还款计划与每期应还">
            <AppInput v-model="form.remaining_periods" type="number" placeholder="例如：186" />
          </AppField>
        </div>

        <div class="grid-2">
          <AppField label="还款频率 / 还款日">
            <div class="grid-2">
              <AppSelect v-model="form.payment_frequency" :options="frequencyOptions" />
              <AppInput v-model="form.payment_day" type="number" placeholder="1-31" />
            </div>
          </AppField>
          <AppField
            label="每期应还"
            hint="默认按「剩余金额 ÷ 剩余期数」测算；银行账单金额不同时可直接改成账单上的数，还款计划会按它排到末期补齐"
          >
            <div class="pay-row">
              <AppInput
                :model-value="form.monthly_payment" type="number" placeholder="自动测算"
                @update:model-value="onPaymentInput"
              />
              <button
                v-if="computedPayment && form.monthly_payment !== computedPayment"
                class="recalc" type="button" @click="useComputedPayment"
              >
                按测算 {{ computedPayment }}
              </button>
            </div>
          </AppField>
        </div>

        <AppField label="借款期间（开始 / 结束）">
          <AppDateRangePicker
            v-model:start="form.start_date" v-model:end="form.end_date"
            placeholder="选择开始与结束日期"
          />
        </AppField>
        <AppField label="首期还款日">
          <AppDatePicker v-model="form.first_payment_date" placeholder="选择首期还款日" />
        </AppField>
        <AppField label="还款账户" :error="fieldErrors.linked_account_id">
          <AppSelect
            v-model="form.linked_account_id"
            :options="[{ value: '', label: '未指定' }, ...accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
          />
        </AppField>

        <!-- 类型扩展 -->
        <template v-if="form.liability_type === 'mortgage'">
          <h4 class="sec">房贷资料</h4>
          <AppField label="提前还款规则">
            <AppInput v-model="form.ext_prepayment_rules" placeholder="例如：满一年后可提前还款" />
          </AppField>
        </template>
        <template v-else-if="form.liability_type === 'credit_card'">
          <h4 class="sec">信用卡资料</h4>
          <AppField label="额度" required :error="fieldErrors['extension.credit_limit']">
            <AppInput v-model="form.ext_credit_limit" type="number" placeholder="例如：50000" />
          </AppField>
          <AppField label="账单日 / 还款日">
            <div class="grid-2">
              <AppInput v-model="form.ext_bill_day" type="number" placeholder="账单日 1-31" />
              <AppInput v-model="form.ext_due_day" type="number" placeholder="还款日 1-31" />
            </div>
          </AppField>
          <AppField label="卡号" hint="敏感字段，默认掩码展示">
            <AppInput v-model="form.ext_card_no" />
          </AppField>
        </template>

        <AppField v-if="form.currency !== 'CNY'" label="币种" :error="fieldErrors.currency"
                  hint="默认人民币；仅外币负债需要修改">
          <AppSelect v-model="form.currency" :options="currencyOptions" />
        </AppField>
        <AppField v-else label="币种">
          <div class="ccy-row">
            <span>人民币 CNY</span>
            <button class="ccy-link" type="button" @click="form.currency = 'USD'">记外币</button>
          </div>
        </AppField>

        <AppField label="纳入净资产"
                  hint="负债是净资产的负项：关闭后该笔欠款不从家庭净值中扣除（如公司贷款）">
          <AppSwitch v-model="form.include_in_net_worth" label="从净资产中扣除" />
        </AppField>
        <AppField label="备注">
          <AppInput v-model="form.note" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">
          {{ editorMode === 'create' ? '创建负债' : '保存修改' }}
        </AppButton>
      </template>
    </AppDrawer>

    <!-- 详情 -->
    <AppDrawer :open="detailOpen" :title="detail?.name || '负债详情'" :width="720" @close="detailOpen = false">
      <StateBlock v-if="detailLoading" state="loading" />
      <template v-else-if="detail">
        <div class="head">
          <div class="row">
            <StatusBadge :label="detail.liability_type_label" tone="info" />
            <StatusBadge :label="detail.status_label" :tone="statusTone(detail.status)" />
            <StatusBadge v-if="detail.interest_rate" :label="`年利率 ${detail.interest_rate}%`" tone="neutral" />
          </div>
          <p class="value">
            剩余本金
            <MoneyText :value="detail.outstanding" :currency="detail.currency" show-currency />
          </p>
          <div class="progress"><span :style="{ width: `${progressPercent(detail)}%` }" /></div>
          <p class="meta">
            初始 {{ formatAmount(detail.initial_amount, ui.amountPrecision) }} · 已还本金
            {{ formatAmount(Number(detail.initial_amount) - Number(detail.outstanding), ui.amountPrecision) }}
            （{{ progressPercent(detail).toFixed(1) }}%）
          </p>
        </div>

        <div class="tabs">
          <button
            v-for="t in [
              { key: 'overview', label: '概览' },
              { key: 'plan', label: '还款计划' },
              { key: 'prepay', label: '提前还款' },
              { key: 'records', label: '还款记录' },
              { key: 'interest', label: '利息费用' },
              { key: 'asset', label: '关联资产' },
              { key: 'ai', label: 'AI 分析' },
            ]"
            :key="t.key" class="tab" :class="{ 'is-active': activeTab === t.key }"
            type="button" @click="activeTab = t.key"
          >
            {{ t.label }}
          </button>
        </div>

        <div v-if="activeTab === 'overview'" class="pane">
          <ul class="kv">
            <li><span class="k">债务类型</span><span class="v">{{ detail.liability_type_label }}</span></li>
            <li><span class="k">债权机构</span><span class="v">{{ detail.creditor || '—' }}</span></li>
            <li><span class="k">币种</span><span class="v">{{ detail.currency }}</span></li>
            <li><span class="k">贷款金额</span><span class="v">{{ formatAmount(detail.initial_amount, ui.amountPrecision) }}</span></li>
            <li><span class="k">剩余本金</span><span class="v strong">{{ formatAmount(detail.outstanding, ui.amountPrecision) }}</span></li>
            <li><span class="k">年利率 / 类型</span><span class="v">{{ detail.interest_rate || '—' }}% · {{ detail.rate_type_label || '未设置' }}</span></li>
            <li v-if="detail.rate_adjustment_cycle">
              <span class="k">利率调整周期</span>
              <span class="v">{{ cycleLabel(detail.rate_adjustment_cycle) }}</span>
            </li>
            <li><span class="k">还款方式</span><span class="v">{{ detail.repayment_method_label || '—' }}</span></li>
            <li><span class="k">还款频率 / 还款日</span><span class="v">{{ detail.payment_frequency_label }} · {{ detail.payment_day || '—' }}</span></li>
            <li>
              <span class="k">剩余期数</span>
              <span class="v">
                {{ detail.remaining_periods ?? '—' }} 期
                <template v-if="detail.repayment_plan_meta?.total_periods">
                  / 共 {{ detail.repayment_plan_meta.total_periods }} 期
                </template>
              </span>
            </li>
            <li><span class="k">每期应还</span><span class="v">{{ formatAmount(detail.monthly_payment, ui.amountPrecision) }}</span></li>
            <li><span class="k">首期还款日</span><span class="v">{{ fmtDate(detail.first_payment_date) }}</span></li>
            <li><span class="k">下次还款日</span><span class="v strong">{{ fmtDate(detail.next_payment_date) }}</span></li>
            <li><span class="k">起止日期</span><span class="v">{{ fmtDate(detail.start_date) }} ~ {{ fmtDate(detail.end_date) }}</span></li>
            <li><span class="k">备注</span><span class="v">{{ detail.note || '—' }}</span></li>
          </ul>
          <ul v-if="extRows.length" class="kv">
            <li v-for="row in extRows" :key="row.key">
              <span class="k">{{ row.label }}</span>
              <span class="v">{{ row.value }}</span>
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'plan'" class="pane">
          <p class="hint">
            {{ detail.repayment_plan_meta.method_label }} · {{ detail.repayment_plan_meta.frequency_label }} ·
            剩余 {{ detail.repayment_plan_meta.periods }} 期（共 {{ detail.repayment_plan_meta.total_periods }} 期，
            已还 {{ detail.repayment_plan_meta.paid_periods }} 期）
          </p>
          <p v-if="detail.remaining_periods == null && detail.term_months" class="plan-tip">
            这笔贷款还没填「剩余期数」，计划按合同总期限（{{ detail.term_months }} 个月）测算，
            可能与银行账单不一致。补上「还剩多少期」后，每期应还会按剩余本金 ÷ 剩余期数 重新测算。
          </p>
          <StateBlock v-if="!detail.repayment_plan.length" state="empty" title="暂无还款计划"
                      description="已结清或未设置还款方式时不生成计划。" />
          <div v-else class="plan-wrap">
            <div class="plan-sum">
              <div>
                <span class="k">每期应还</span>
                <strong>{{ formatAmount(detail.repayment_plan_meta.per_period || detail.monthly_payment, ui.amountPrecision) }}</strong>
              </div>
              <div>
                <span class="k">未还合计</span>
                <strong>{{ formatAmount(planUnpaidTotal, ui.amountPrecision) }}</strong>
              </div>
            </div>
            <table class="plan">
              <thead>
                <tr>
                  <th>期数</th><th>还款日</th>
                  <th class="num col-split">本金</th><th class="num col-split">利息</th>
                  <th class="num">应还</th><th class="num">剩余本金</th><th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in visiblePlan" :key="row.period">
                  <td>{{ row.period }}<small v-if="row.total_periods" class="of">/{{ row.total_periods }}</small></td>
                  <td>{{ fmtDate(row.due_date) }}</td>
                  <td class="num col-split">{{ formatAmount(row.principal, ui.amountPrecision) }}</td>
                  <td class="num col-split">{{ formatAmount(row.interest, ui.amountPrecision) }}</td>
                  <td class="num pay">{{ formatAmount(row.payment, ui.amountPrecision) }}</td>
                  <td class="num">{{ formatAmount(row.remaining, ui.amountPrecision) }}</td>
                  <td><StatusBadge label="未还" tone="neutral" /></td>
                </tr>
              </tbody>
            </table>
            <AppButton
              v-if="visiblePlan.length < detail.repayment_plan.length"
              variant="ghost" @click="planLimit += 24"
            >
              显示更多（还有 {{ detail.repayment_plan.length - visiblePlan.length }} 期）
            </AppButton>
            <p class="hint">{{ detail.repayment_plan_meta.caliber }}</p>
          </div>
        </div>

        <div v-else-if="activeTab === 'prepay'" class="pane">
          <p class="prepay-intro">
            一次性提前还一笔钱，通常有两种处理方式。<strong>缩短年限</strong>省息最多，
            <strong>减少月供</strong>月现金流更宽松——填个金额算算看。
          </p>
          <div class="prepay-form">
            <label class="prepay-field">
              <span class="k">计划提前还款金额（{{ detail.currency }}）</span>
              <input
                v-model="prepayAmount" type="number" min="0" step="1000"
                inputmode="decimal" placeholder="例如 100000"
                @keyup.enter="runPrepaySimulation"
              >
            </label>
            <AppButton variant="primary" :loading="prepayLoading" @click="runPrepaySimulation">
              测算能省多少
            </AppButton>
          </div>
          <p class="prepay-hint">
            剩余本金 {{ formatAmount(prepayResult?.outstanding || detail.outstanding, ui.amountPrecision) }}
            · 剩余 {{ detail.repayment_plan_meta?.periods ?? '—' }} 期
            · 每期应还 {{ formatAmount(detail.repayment_plan_meta?.per_period || '0', ui.amountPrecision) }}
          </p>

          <template v-if="prepayResult">
            <div class="prepay-grid">
              <!-- 缩短年限 -->
              <div class="prepay-card" :class="{ 'is-best': Number(prepayResult.shorten_term.saved_interest) >= Number(prepayResult.reduce_payment.saved_interest) }">
                <p class="pc-tag">方案一 · 缩短年限</p>
                <p class="pc-main">
                  省息 <strong>{{ formatAmount(prepayResult.shorten_term.saved_interest, ui.amountPrecision) }}</strong>
                </p>
                <ul class="pc-list">
                  <li><span>月供不变</span><b>{{ formatAmount(prepayResult.shorten_term.monthly_payment, ui.amountPrecision) }}</b></li>
                  <li><span>剩余期数</span><b>{{ prepayResult.shorten_term.periods }} 期</b></li>
                  <li><span>少还</span><b>{{ prepayResult.shorten_term.saved_periods }} 期</b></li>
                  <li><span>总利息</span><b>{{ formatAmount(prepayResult.shorten_term.total_interest, ui.amountPrecision) }}</b></li>
                </ul>
              </div>

              <!-- 减少月供 -->
              <div class="prepay-card" :class="{ 'is-best': Number(prepayResult.reduce_payment.saved_interest) > Number(prepayResult.shorten_term.saved_interest) }">
                <p class="pc-tag">方案二 · 减少月供</p>
                <p class="pc-main">
                  省息 <strong>{{ formatAmount(prepayResult.reduce_payment.saved_interest, ui.amountPrecision) }}</strong>
                </p>
                <ul class="pc-list">
                  <li><span>每期降至</span><b>{{ formatAmount(prepayResult.reduce_payment.monthly_payment, ui.amountPrecision) }}</b></li>
                  <li><span>每月少还</span><b>{{ formatAmount(prepayResult.reduce_payment.monthly_reduction || '0', ui.amountPrecision) }}</b></li>
                  <li><span>剩余期数</span><b>{{ prepayResult.reduce_payment.periods }} 期</b></li>
                  <li><span>总利息</span><b>{{ formatAmount(prepayResult.reduce_payment.total_interest, ui.amountPrecision) }}</b></li>
                </ul>
              </div>
            </div>
            <p class="prepay-caliber">
              还款后剩余本金 {{ formatAmount(prepayResult.new_outstanding, ui.amountPrecision) }}。{{ prepayResult.caliber }}。
            </p>
          </template>
        </div>

        <div v-else-if="activeTab === 'records'" class="pane">
          <div class="pane-actions">
            <AppButton v-if="canConfirm && !detail.settled" variant="secondary" @click="openRepay">
              <TrendingDown :size="15" /> 登记还款
            </AppButton>
          </div>
          <StateBlock v-if="!detail.repayments.length" state="empty" title="暂无还款记录" description="登记还款后这里会显示本金/利息/费用拆分。" />
          <ul v-else class="list">
            <li v-for="r in detail.repayments" :key="r.id">
              <div class="l-main">
                <strong>还款 {{ r.amount }} {{ detail.currency }}</strong>
                <small>{{ fmtDate(r.occurred_at) }} · 本金 {{ r.principal }} · 利息 {{ r.interest }} · 费用 {{ r.fee }}</small>
                <small v-if="r.reason" class="reason">调整原因：{{ r.reason }}</small>
              </div>
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'interest'" class="pane">
          <ul class="kv">
            <li><span class="k">累计利息与费用</span><span class="v strong">{{ detail.interest_summary.total_interest }}</span></li>
            <li><span class="k">口径</span><span class="v">{{ detail.interest_summary.caliber }}</span></li>
          </ul>
          <StateBlock v-if="!detail.interest_events.length" state="empty" title="暂无利息费用事件" description="登记还款产生利息或费用时会生成支出事件。" />
          <ul v-else class="list">
            <li v-for="e in detail.interest_events" :key="e.id">
              <div class="l-main">
                <strong>{{ e.title }}</strong>
                <small>
                  <span class="cat-tag" :class="{ none: !e.category_id }">{{ e.category_label }}</span>
                  {{ e.event_no }} · {{ fmtDate(e.occurred_at) }}
                </small>
              </div>
              <span class="l-amount">
                {{ formatAmount(e.amount, ui.amountPrecision) }} {{ e.currency }}
              </span>
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'asset'" class="pane">
          <StateBlock
            v-if="!detail.linked_asset" state="empty" title="未关联资产"
            description="房贷请关联不动产、车贷请关联车辆；资产页面只显示摘要，不重复录入贷款数据。"
          />
          <ul v-else class="kv">
            <li><span class="k">关联资产</span><span class="v strong">{{ detail.linked_asset.name }}</span></li>
            <li><span class="k">资产类型</span><span class="v">{{ detail.linked_asset.asset_type_label }}</span></li>
            <li><span class="k">资产当前价值</span><span class="v">{{ detail.linked_asset.current_value }} {{ detail.linked_asset.currency }}</span></li>
          </ul>
          <div v-if="detail.linked_asset" class="pane-actions">
            <AppButton variant="secondary" @click="router.push({ path: detail.linked_asset.asset_type === 'real_estate' ? '/ledger/assets/real-estates' : '/ledger/assets/vehicles', query: { open: detail.linked_asset.id } })">
              查看资产详情
            </AppButton>
          </div>
        </div>

        <div v-else class="pane">
          <div class="ai">
            <h4><Sparkles :size="14" /> AI 可以做什么</h4>
            <ul><li v-for="c in detail.ai.allowed" :key="c">{{ c }}</li></ul>
            <h4>AI 不能做什么</h4>
            <ul class="forbidden"><li v-for="c in detail.ai.forbidden" :key="c">{{ c }}</li></ul>
            <AppButton variant="secondary" @click="ui.openAiPanel(`负债：${detail.name}`)">
              <Sparkles :size="15" /> 就这笔向 AI 追问
            </AppButton>
          </div>
        </div>
      </template>

      <template #footer>
        <div class="footer">
          <AppButton v-if="canConfirm && detail && !detail.settled" variant="secondary" @click="openRepay">
            登记还款
          </AppButton>
          <AppButton
            v-if="canConfirm && detail && ['personal', 'other'].includes(detail.liability_type)"
            variant="secondary" @click="openDraw"
          >
            借款到账
          </AppButton>
          <AppButton v-if="auth.can('delete') && detail" variant="secondary" @click="remove(detail)">删除</AppButton>
          <AppButton variant="primary" @click="detailOpen = false">关闭</AppButton>
        </div>
      </template>
    </AppDrawer>

    <!-- 登记还款 -->
    <AppModal
      :open="repayOpen" title="登记还款" confirm-text="确认还款"
      :confirm-loading="repaySaving" @close="repayOpen = false" @confirm="submitRepay"
    >
      <div class="form">
        <p class="tip">
          系统按「剩余本金 × 周期利率」给出建议利息，其余计入本金；调整拆分必须填写原因。
          本金不重复计入支出，利息与费用计入支出。
        </p>
        <AppField label="还款总额" required :error="repayErrors.amount">
          <AppInput v-model="repayForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="还款账户" required :error="repayErrors.account_id">
          <AppSelect
            v-model="repayForm.account_id"
            :options="[{ value: '', label: '请选择账户' }, ...accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
          />
        </AppField>
        <AppField label="费用（可选）" :error="repayErrors.fee">
          <AppInput v-model="repayForm.fee" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="还款日期">
          <AppDatePicker v-model="repayForm.occurred_at" value-format="datetime" placeholder="留空为今天" />
        </AppField>
        <AppField label="手动调整拆分">
          <AppSwitch v-model="repayAdjust" label="手动指定本金与利息" />
        </AppField>
        <template v-if="repayAdjust">
          <AppField label="本金 / 利息">
            <div class="grid-2">
              <AppInput v-model="repayForm.principal" type="number" placeholder="本金" />
              <AppInput v-model="repayForm.interest" type="number" placeholder="利息" />
            </div>
          </AppField>
          <AppField label="调整原因" required :error="repayErrors.reason">
            <AppInput v-model="repayForm.reason" placeholder="例如：本期免息 / 按银行对账单" />
          </AppField>
        </template>
        <AppField label="备注">
          <AppInput v-model="repayForm.note" />
        </AppField>
      </div>
    </AppModal>

    <!-- 借款到账 -->
    <AppModal
      :open="drawOpen" title="借款到账" confirm-text="确认到账"
      :confirm-loading="drawSaving" @close="drawOpen = false" @confirm="submitDraw"
    >
      <div class="form">
        <p class="tip">借款到账会增加账户余额与负债本金，但**不计入收入**（01 §4）。</p>
        <AppField label="到账金额" required :error="drawErrors.amount">
          <AppInput v-model="drawForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="入账账户" required :error="drawErrors.account_id">
          <AppSelect
            v-model="drawForm.account_id"
            :options="[{ value: '', label: '请选择账户' }, ...accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
          />
        </AppField>
        <AppField label="到账日期">
          <AppDatePicker v-model="drawForm.occurred_at" value-format="datetime" placeholder="留空为今天" />
        </AppField>
        <AppField label="备注">
          <AppInput v-model="drawForm.note" />
        </AppField>
      </div>
    </AppModal>
  </div>
</template>

<style scoped>
.liab { display: flex; flex-direction: column; gap: var(--space-4); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(206px, 1fr)); gap: var(--space-3); }
.stats :deep(.card-body) { padding: 13px 15px 14px; }
/* 金额是这张卡的主角，之前只有 17px，看着没重点 */
.stat :deep(.money) { font-size: 25px; font-weight: 700; letter-spacing: -0.01em; }
.stat:first-child { background: var(--glass-2); }
.stat:first-child :deep(.money) { color: var(--danger); }
.stat .k { font-size: 12.5px; color: var(--ink-muted); }
.stat .n { font-size: 25px; font-weight: 700; color: var(--ink-strong); line-height: 1.15; }
.multi { font-size: 12.5px; color: var(--ink-muted); }
.unit-muted { margin-left: 4px; font-size: 13px; font-weight: 500; opacity: 0.6; }
.upcoming { display: flex; flex-direction: column; gap: 8px; }
.upcoming li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.up-main { display: flex; flex-direction: column; }
.up-main strong { font-size: 13.5px; color: var(--ink-strong); }
.up-main small { font-size: 11.5px; color: var(--ink-muted); }
.up-right { display: flex; align-items: center; gap: 10px; }
.up-right .amount { font-variant-numeric: tabular-nums; font-size: 13px; }


@media (max-width: 640px) {
  .upcoming li { flex-wrap: wrap; gap: 6px 10px; }
  .up-right { width: 100%; justify-content: space-between; }
}
.toolbar { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; width: 100%; }
.search {
  display: flex; align-items: center; gap: 6px; flex: 1; min-width: 180px; height: 34px;
  padding: 0 10px; border: 1px solid var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1); color: var(--ink-muted);
}
.search input { flex: 1; border: none; background: transparent; color: var(--ink-strong); font-size: 13.5px; outline: none; }
.sel { width: 150px; }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act {
  display: inline-flex; align-items: center; gap: 4px; height: 28px; padding: 0 10px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1);
  color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.inst-row { display: flex; flex-direction: column; gap: var(--space-2); }
.pay-row { display: flex; align-items: center; gap: 8px; }
.pay-row :deep(.wrap) { flex: 1; }
.recalc {
  flex: 0 0 auto; height: 32px; padding: 0 10px; border-radius: 8px;
  border: 1px dashed var(--border); background: var(--glass-1);
  color: var(--primary); font-size: 12px; cursor: pointer; white-space: nowrap;
}
.recalc:hover { border-color: var(--primary); }
.ccy-row { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--ink-strong); }
.ccy-link {
  border: none; background: transparent; padding: 0; cursor: pointer;
  font-size: 12.5px; color: var(--primary); text-decoration: underline;
}
.sec { font-size: 13.5px; color: var(--ink-strong); border-top: 1px solid var(--border); padding-top: var(--space-3); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.head { margin-bottom: var(--space-3); }
.head .row { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.value { display: flex; align-items: baseline; gap: 8px; font-size: 13px; color: var(--ink-muted); }
.value :deep(.money) { font-size: 22px; font-weight: 600; }
.progress { height: 6px; border-radius: 999px; background: var(--glass-2); overflow: hidden; margin: 8px 0 6px; }
.progress span { display: block; height: 100%; background: var(--primary); }
.meta { font-size: 12px; color: var(--ink-muted); }
.tabs { display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid var(--border); margin-bottom: var(--space-3); }
.tab { flex: none; padding: 8px 12px; border: none; background: transparent; cursor: pointer; font-size: 13px; color: var(--ink-muted); border-bottom: 2px solid transparent; }
.tab.is-active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 500; }
.pane { display: flex; flex-direction: column; gap: var(--space-3); }

/* ---------- 提前还款测算 ---------- */
.prepay-intro { margin: 0; font-size: 12.5px; line-height: 1.7; color: var(--ink-muted); }
.prepay-intro strong { color: var(--ink-strong); font-weight: 600; }
.prepay-form { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.prepay-field { display: flex; flex-direction: column; gap: 5px; flex: 1; min-width: 200px; }
.prepay-field .k { font-size: 11.5px; color: var(--ink-muted); }
.prepay-field input {
  padding: 9px 11px; border-radius: 9px; border: 1px solid var(--border);
  background: var(--glass-1); color: var(--ink-strong); font-size: 13.5px;
  font-variant-numeric: tabular-nums; outline: none;
}
.prepay-field input:focus { border-color: var(--primary); }
.prepay-hint { margin: 0; font-size: 11.5px; color: var(--ink-muted); font-variant-numeric: tabular-nums; }

.prepay-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); margin-top: 4px; }
.prepay-card {
  padding: 13px 14px; border-radius: var(--glass-radius-card, 12px);
  border: 1px solid var(--border); background: var(--glass-1);
  display: flex; flex-direction: column; gap: 8px;
}
.prepay-card.is-best {
  border-color: rgba(25, 183, 122, 0.42);
  background: linear-gradient(150deg, rgba(25, 183, 122, 0.08), transparent 62%), var(--glass-1);
}
.pc-tag { margin: 0; font-size: 11px; color: var(--ink-muted); }
.pc-main { margin: 0; font-size: 12.5px; color: var(--ink-muted); }
.pc-main strong {
  font-size: 19px; font-weight: 700; color: var(--ink-strong);
  font-variant-numeric: tabular-nums; margin-left: 4px;
}
.prepay-card.is-best .pc-main strong { color: var(--success); }
.pc-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.pc-list li {
  display: flex; justify-content: space-between; gap: 10px;
  font-size: 11.5px; color: var(--ink-muted); font-variant-numeric: tabular-nums;
}
.pc-list b { color: var(--ink-strong); font-weight: 550; }
.prepay-caliber { margin: 0; font-size: 11px; color: var(--ink-muted); line-height: 1.6; }

@media (max-width: 640px) {
  .prepay-grid { grid-template-columns: 1fr; }
}
.pane-actions { display: flex; justify-content: flex-end; }
.kv { display: flex; flex-direction: column; }
.kv li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 8px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.kv .k { color: var(--ink-muted); }
.kv .v { color: var(--ink-strong); text-align: right; word-break: break-all; }
.kv .v.strong { font-weight: 600; }
.list { display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.l-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.l-main strong { font-size: 13.5px; color: var(--ink-strong); }
.l-main small { font-size: 11.5px; color: var(--ink-muted); }
.l-main small.reason { color: var(--primary); }
.l-amount { font-variant-numeric: tabular-nums; font-size: 13.5px; white-space: nowrap; }
.plan-wrap { overflow: auto; }
.plan { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.plan th, .plan td { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; }
.plan td.num { text-align: right; font-variant-numeric: tabular-nums; }
/* 表头与数字列同对齐，避免标题和下面的数字错位 */
.plan th.num { text-align: right; }

/* 手机上列太多会横向溢出：本金/利息收起（应还与剩余本金已能说明问题） */
@media (max-width: 640px) {
  .plan { font-size: 11.5px; }
  .plan th, .plan td { padding: 5px 6px; }
  .plan .col-split { display: none; }
}
.plan td.pay, .plan th.num.pay { font-weight: 600; color: var(--ink-strong); }
.plan .of { color: var(--ink-muted); font-size: 11px; }
.plan-sum {
  display: flex; gap: var(--space-5); flex-wrap: wrap;
  margin-bottom: var(--space-3); padding: 10px 12px;
  border: 1px solid var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1);
}
.cat-tag {
  display: inline-block; margin-right: 6px; padding: 1px 6px; border-radius: 6px;
  background: var(--glass-2); border: 1px solid var(--border);
  font-size: 11px; color: var(--ink-strong);
}
.cat-tag.none { color: var(--ink-muted); border-style: dashed; }
.plan-tip {
  margin: 0 0 var(--space-3); padding: 9px 11px; border-radius: var(--glass-radius-control);
  border: 1px solid rgba(240, 173, 78, 0.35); background: rgba(240, 173, 78, 0.1);
  font-size: 12px; line-height: 1.6; color: var(--ink);
}
.plan-sum .k { display: block; font-size: 11.5px; color: var(--ink-muted); }
.plan-sum strong { font-size: 16px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.hint { font-size: 12px; color: var(--ink-muted); line-height: 1.6; }
.tip { font-size: 12.5px; color: var(--ink-muted); line-height: 1.6; }
.ai { display: flex; flex-direction: column; gap: 10px; }
.ai h4 { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul.forbidden li { color: var(--ink-muted); }
.footer { display: flex; gap: var(--space-2); justify-content: flex-end; flex-wrap: wrap; }
@media (max-width: 700px) { .sel { width: 100%; } .grid-2 { grid-template-columns: 1fr; } }
.credit-warn {
  margin: -4px 0 4px; padding: 10px 12px; border-radius: 10px;
  font-size: 12.5px; line-height: 1.6; color: var(--warning-ink, #8a5a00);
  background: rgba(230, 160, 20, 0.12); border: 1px solid rgba(230, 160, 20, 0.28);
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
