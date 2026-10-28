<script setup lang="ts">
/**
 * 流水页面 /ledger/transactions（06 §4、18 §4 M1 核心账务）。
 * 覆盖：筛选、列表、新增收入/支出、内部转账、详情、确认、冲销。
 * 已确认事件不能直接删除，只能冲销（06 §4、11 §2）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Plus, Sparkles, ArrowLeftRight, RefreshCw, CheckCircle2, Undo2, RotateCcw } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import { toLocalDateTimeValue } from '@/utils/calendar'
import { formatAmount } from '@/utils/format'
import { accountLabel } from '@/utils/account'
import AppButton from '@/components/ui/AppButton.vue'
import AppToolbar from '@/components/ui/AppToolbar.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppDateRangePicker from '@/components/ui/AppDateRangePicker.vue'
import AppDataTable, { type Column } from '@/components/ui/AppDataTable.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import CategoryPicker from '@/components/CategoryPicker.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface EventRow {
  id: string
  event_no: string
  event_type: string
  event_type_label: string
  /** 统一显示口径：收入 / 支出 / 内部转账 / 不计收支 */
  flow_direction: string
  flow_direction_label: string
  /** 二级分类名（如「房贷还款」「工资收入」），无分类时为 null */
  category_name: string | null
  status: string
  status_label: string
  title: string
  amount: string
  currency: string
  counterparty: string | null
  account_id: string | null
  category_id: string | null
  asset_id: string | null
  source: string
  source_label: string
  counts_in_stats: boolean
  version: number
  occurred_at: string | null
  note: string | null
  realized_gain: string | null
  movements: { direction_label: string; amount: string }[]
  /** 还款相关事件：同一笔还款的本金 / 利息两条事件共用一个还款单 */
  repayment?: RepaymentInfo | null
}

interface RepaymentInfo {
  id: string
  amount: string
  principal: string
  interest: string
  fee: string
  liability_id: string
  liability_name: string
  leg: 'principal' | 'interest'
  occurred_at: string | null
}

/** 列表展示行：同一笔还款合并为一行，明细放在 legs 里 */
interface DisplayRow extends EventRow {
  merged?: boolean
  legs?: EventRow[]
}

interface Account {
  id: string
  name: string
  currency: string
  status: string
  /** 卡号后四位（后端只存后四位，用于区分同名的多张卡） */
  card_last4?: string | null
  /** 后端拼好的「尾号 0616」，为空表示非银行卡 */
  card_masked?: string | null
  /** 账户当前余额：信用卡为负表示欠款 */
  balance?: string | null
  /** 授信额度（信用卡 / 花呗等信用类账户才有值） */
  credit_limit?: string | null
  /** 已用额度 = 欠款，后端按余额派生 */
  credit_used?: string | null
  available_credit?: string | null
  is_credit?: boolean
  category_label?: string | null
}

/** 负债（房贷 / 车贷 / 信用卡负债等），用于「还款」类支出选对应债务 */
interface LiabilityLite {
  id: string
  name: string
  liability_type: string
  liability_type_label: string
  outstanding: string
  currency: string
  status: string
}

interface Transfer {
  id: string
  group_no: string
  status: string
  status_label: string
  from_account_name: string | null
  to_account_name: string | null
  amount: string
  fee: string
  currency: string
  occurred_at: string | null
  note: string | null
}

const route = useRoute()
const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const tab = ref<'events' | 'transfers'>('events')
/** 转账记录的筛选：转账不在业务流水里，需要独立的一套条件 */
const transferFilters = reactive({
  date_from: '', date_to: '', account_id: '', keyword: '',
})
const transferActiveCount = computed(() => [
  transferFilters.date_from, transferFilters.date_to,
  transferFilters.account_id, transferFilters.keyword,
].filter(Boolean).length)
const loading = ref(false)
const error = ref('')
const rows = ref<EventRow[]>([])
const transfers = ref<Transfer[]>([])
const transferPage = ref(1)
const transferTotal = ref(0)
const total = ref(0)
const page = ref(1)
const pageSize = 20
const accounts = ref<Account[]>([])
const categories = ref<{ id: string; name: string; domain: string; parent_id?: string | null; level?: number }[]>([])
/** 负债：选「信用卡还款 / 房贷还款」等还款类分类时，用来挑对应的债务 */
const liabilities = ref<LiabilityLite[]>([])
/** 关联资产（23 §4：业务事件必须支持资产关联；15 §5.4 车辆费用必须保留车辆关联） */
const assets = ref<{ id: string; name: string; asset_type_label: string; status: string }[]>([])
/** 家庭成员（固定收入的归属成员） */
const members = ref<{ id: string; display_name: string; status: string }[]>([])
const memberOptions = computed(() => [
  { value: '', label: '不指定成员' },
  ...members.value.filter((m) => m.status === 'active')
    .map((m) => ({ value: m.id, label: m.display_name })),
])

const filters = reactive({
  direction: '',
  account_id: (route.query.account_id as string) || '',
  category_id: '',
  status: '',
  source: '',
  keyword: '',
  date_from: '',
  date_to: '',
  amount_min: '',
  amount_max: '',
})

const eventDrawer = ref(false)
/** 退款上下文：不为空时表示当前抽屉是在为这笔支出登记退款 */
const refundOf = ref<EventRow | null>(null)
const transferDrawer = ref(false)
const detailDrawer = ref(false)
const detail = ref<EventRow | null>(null)
/** 合并行的明细（本金 / 利息两条事件），非合并行为空 */
const detailLegs = ref<EventRow[]>([])

/**
 * 还款会拆成「本金」「利息与费用」两条事件：流水列表只显示一笔还款，
 * 点开详情再看拆分，否则同一笔钱在流水里出现两行，看着像重复记账。
 */
const displayRows = computed<DisplayRow[]>(() => {
  // 先按还款单归组，同时记住每组第一次出现的位置，保证列表顺序不变
  const legsById = new Map<string, EventRow[]>()
  const slot: (EventRow | string)[] = []
  for (const row of rows.value) {
    const info = row.repayment
    if (!info) {
      slot.push(row)
      continue
    }
    if (!legsById.has(info.id)) {
      legsById.set(info.id, [])
      slot.push(info.id)
    }
    legsById.get(info.id)!.push(row)
  }

  return slot.map((item) => {
    if (typeof item !== 'string') return item
    const legs = legsById.get(item) || []
    const info = legs[0].repayment!
    // 以「本金」那条事件为主：它代表这笔还款本身，利息只是其中的支出部分
    const base = legs.find((leg) => leg.repayment?.leg === 'principal') || legs[0]
    const ordered = [...legs].sort(
      (a, b) => (a.repayment?.leg === 'principal' ? 0 : 1) - (b.repayment?.leg === 'principal' ? 0 : 1),
    )
    return {
      ...base,
      title: `还款 · ${info.liability_name || '负债'}`,
      amount: info.amount,
      // 合并行代表一整笔还款：类型按统一口径显示为「支出」，分类保留「房贷还款」这类二级分类。
      // event_type 保持 liability_repay 是为了屏蔽「退款」入口（退款只对单笔支出有意义）。
      event_type: 'liability_repay',
      flow_direction: 'expense',
      flow_direction_label: '支出',
      merged: true,
      legs: ordered,
      repayment: info,
    }
  })
})
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const eventForm = reactive({
  event_type: 'expense',
  title: '',
  amount: '',
  occurred_at: '',
  account_id: '',
  category_id: '',
  asset_id: '',
  counterparty: '',
  note: '',
  /** 还款类支出专用：`account:<id>` 表示信用卡账户，`liability:<id>` 表示负债 */
  repay_target: '',
})
/** 支出时可选「每月重复」：保存草稿的同时登记一笔固定支出 */
const recurring = reactive({
  enabled: false,
  frequency: 'monthly',
  day_of_month: 1,
})
/** 收入时可选「每月重复」：保存草稿的同时登记一笔固定收入（工资等） */
const recurringIncome = reactive({
  enabled: false,
  frequency: 'monthly',
  day_of_month: 1,
  member_id: '',
})
const transferForm = reactive({
  from_account_id: '',
  to_account_id: '',
  amount: '',
  fee: '',
  occurred_at: '',
  to_amount: '',
  exchange_rate: '',
  note: '',
})

/** 详情里的类型文案：统一成「方向（+ 具体类型）」。
 * 普通收入/支出只显示「收入 / 支出」；负债还款、资产购置这类语义不同时补上具体类型，
 * 避免列表与详情口径不一致（曾出现列表写「还款」、别处写「负债还款」）。 */
const detailTypeText = computed(() => {
  const d = detail.value
  if (!d) return '—'
  const direction = d.flow_direction_label || '不计收支'
  const precise = d.event_type_label || ''
  const sameWord = precise === direction
    || (direction === '收入' && precise === '收入')
    || (direction === '支出' && precise === '支出')
  return sameWord || !precise ? direction : `${direction} · ${precise}`
})

const columns: Column[] = [
  { key: 'occurred_at', label: '日期' },
  { key: 'title', label: '标题', primary: true },
  { key: 'flow_direction_label', label: '类型' },
  { key: 'category_name', label: '分类' },
  { key: 'amount', label: '金额', align: 'right' },
  { key: 'status_label', label: '状态' },
  { key: 'actions', label: '操作', align: 'right' },
]

const transferColumns: Column[] = [
  { key: 'occurred_at', label: '日期' },
  { key: 'group_no', label: '转账组编号', primary: true },
  { key: 'from_account_name', label: '转出账户' },
  { key: 'to_account_name', label: '转入账户' },
  { key: 'amount', label: '金额', align: 'right' },
  { key: 'status_label', label: '状态' },
  { key: 'actions', label: '操作', align: 'right' },
]

const canCreate = computed(() => auth.can('create_draft'))
const canConfirm = computed(() => auth.can('confirm'))
const canReverse = computed(() => auth.can('reverse'))
const canEditDraft = computed(() => auth.can('edit_draft'))

const editingEventId = ref('')
const editingEventVersion = ref(1)

/** 账户一行文案：名称 + 尾号（银行卡）/币种（外币）+ 余额或欠款。
 *  同名卡很多，只显示名称分不清是哪一张；金额让用户不必先退出页面去查余额。 */
const accountText = (a: Account, withAmount = true) => accountLabel(a, ui.amountPrecision, withAmount)

const accountOptions = computed(() => [
  { value: '', label: '全部账户' },
  ...accounts.value.map((a) => ({ value: a.id, label: accountText(a, false) })),
])
const accountOptionsRequired = computed(() => [
  { value: '', label: '请选择账户' },
  ...accounts.value.filter((a) => a.status === 'active')
    .map((a) => ({ value: a.id, label: accountText(a, true) })),
])
/** 还款的「扣款账户」：只列可用余额类账户，信用卡不能给自己还款 */
const payFromOptions = computed(() => [
  { value: '', label: '请选择扣款账户' },
  ...accounts.value
    .filter((a) => a.status === 'active' && !a.is_credit)
    .map((a) => ({ value: a.id, label: accountText(a, true) })),
])

/** 选中账户的补充说明：余额 / 欠款 / 可用额度，选完不用再去账户页确认 */
const selectedAccountInfo = computed(() => {
  const a = accounts.value.find((x) => x.id === eventForm.account_id)
  if (!a) return ''
  const parts: string[] = []
  if (a.is_credit) {
    parts.push(`欠款 ${formatAmount(a.credit_used, ui.amountPrecision)}`)
    if (a.credit_limit) parts.push(`额度 ${formatAmount(a.credit_limit, ui.amountPrecision)}`)
    if (a.available_credit) parts.push(`可用 ${formatAmount(a.available_credit, ui.amountPrecision)}`)
  } else {
    parts.push(`当前余额 ${formatAmount(a.balance, ui.amountPrecision)}`)
  }
  if (a.category_label) parts.unshift(a.category_label)
  return parts.join(' · ')
})
const categoryOptions = computed(() => [
  { value: '', label: '全部分类' },
  ...categories.value
    .filter((c) => !filters.direction || (filters.direction === 'income' ? c.domain === 'income' : c.domain === 'expense'))
    .map((c) => ({ value: c.id, label: c.name })),
])

/** 还款类分类 → 可选的债务类型。信用卡在你家是「账户」，所以还会列出信用类账户。 */
const REPAY_LIABILITY_TYPES: Record<string, string[]> = {
  信用卡还款: ['credit_card'],
  房贷还款: ['mortgage'],
  车贷还款: ['vehicle_loan'],
  其他贷款还款: ['consumer_loan', 'business_loan', 'personal', 'other'],
}

const selectedCategory = computed(
  () => categories.value.find((c) => c.id === eventForm.category_id) || null,
)

/** 是否属于「还款」这一支：先按名称精确命中，再沿父级回溯（父级叫「还款」即算） */
const repayCategoryName = computed(() => {
  const c = selectedCategory.value
  if (!c) return ''
  if (REPAY_LIABILITY_TYPES[c.name]) return c.name
  let cur: typeof c | undefined = c
  for (let depth = 0; depth < 4 && cur?.parent_id; depth += 1) {
    const parent = categories.value.find((p) => p.id === cur!.parent_id)
    if (!parent) break
    if (parent.name === '还款') return c.name
    cur = parent
  }
  return ''
})

/** 还款模式：只有「新建支出 + 还款类分类」走还款单，编辑草稿/退款仍按普通流水处理 */
const repayMode = computed(() => (
  eventForm.event_type === 'expense'
  && !refundOf.value
  && !editingEventId.value
  && Boolean(repayCategoryName.value)
))

/** 对应的信用卡 / 负债。信用卡既可能是账户（有授信额度），也可能是负债，两种都列出来。 */
const repayTargetOptions = computed(() => {
  const name = repayCategoryName.value
  if (!name) return [] as { value: string; label: string }[]
  const wanted = REPAY_LIABILITY_TYPES[name] || []
  const options: { value: string; label: string }[] = []
  if (name === '信用卡还款') {
    for (const a of accounts.value) {
      if (a.status !== 'active' || !a.is_credit) continue
      options.push({ value: `account:${a.id}`, label: accountText(a, true) })
    }
  }
  for (const l of liabilities.value) {
    if (wanted.length && !wanted.includes(l.liability_type)) continue
    options.push({
      value: `liability:${l.id}`,
      label: `${l.name}（${l.liability_type_label}）· 剩余 ${formatAmount(l.outstanding, ui.amountPrecision)}`,
    })
  }
  return options
})

/** 还款提示：不同目标走不同单据，口径必须说清楚，否则用户会以为没记上 */
const repayHint = computed(() => {
  const target = eventForm.repay_target
  if (!repayMode.value) return ''
  if (target.startsWith('account:')) {
    return '信用卡在本平台按账户管理：这笔会记为「内部转账」（扣款账户 → 信用卡），信用卡欠款随之减少，不重复计入支出。可在「内部转账」页签查看。'
  }
  if (target.startsWith('liability:')) {
    return '计入该笔债务的还款单：本金减少剩余本金、不计支出，利息与手续费才计入支出。'
  }
  return '选择对应的信用卡或债务后，系统会按还款口径记账（本金不重复计入支出）。'
})

const assetOptions = computed(() => [
  { value: '', label: '不关联资产' },
  ...assets.value
    .filter((a) => a.status === 'active')
    .map((a) => ({ value: a.id, label: `${a.name}（${a.asset_type_label}）` })),
])
function assetName(id: string | null | undefined): string {
  if (!id) return '—'
  return assets.value.find((a) => a.id === id)?.name || id
}
const directionOptions = [
  { value: '', label: '全部方向' },
  { value: 'income', label: '收入' },
  { value: 'expense', label: '支出' },
  { value: 'transfer', label: '内部转账' },
  { value: 'inflow', label: '资金转入（不计收入）' },
  { value: 'outflow', label: '资金转出（不计支出）' },
]
const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'draft', label: '草稿' },
  { value: 'confirmed', label: '已确认' },
  { value: 'reversed', label: '已冲销' },
]
const sourceOptions = [
  { value: '', label: '全部来源' },
  { value: 'manual', label: '手工录入' },
  { value: 'ocr', label: 'OCR 识别' },
  { value: 'ai', label: 'AI 建议' },
]
const eventTypeOptions = [
  { value: 'expense', label: zh('event_type', 'expense') },
  { value: 'income', label: zh('event_type', 'income') },
]

async function loadRefs() {
  // 参考数据失败不能挡住流水列表：否则页面只显示「没有符合条件的流水」，用户会误判为没数据
  try {
    const [acc, cat, ast, hh, lia] = await Promise.all([
      api.get<{ items: Account[] }>('/ledger/accounts'),
      api.get<{ items: { id: string; name: string; domain: string; parent_id?: string | null; level?: number }[] }>('/ledger/categories'),
      api.get<{ items: { id: string; name: string; asset_type_label: string; status: string }[] }>(
        '/ledger/assets', { page_size: 200 }),
      api.get<{ members: { id: string; display_name: string; status: string }[] }>('/settings/household'),
      api.get<{ items: LiabilityLite[] }>('/ledger/liabilities', { status: 'active' }),
    ])
    accounts.value = acc.items
    categories.value = cat.items
    assets.value = ast.items
    members.value = hh.members || []
    liabilities.value = lia.items || []
  } catch (err) {
    toast.error(`账户/分类加载失败：${errorMessage(err)}`)
  }
}

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    if (tab.value === 'events') {
      const data = await api.get<{ items: EventRow[]; total: number }>('/ledger/events', {
        page: page.value,
        page_size: pageSize,
        direction: filters.direction || undefined,
        account_id: filters.account_id || undefined,
        category_id: filters.category_id || undefined,
        status: filters.status || undefined,
        source: filters.source || undefined,
        keyword: filters.keyword || undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
        amount_min: filters.amount_min || undefined,
        amount_max: filters.amount_max || undefined,
      })
      if (seq !== loadSeq) return
      rows.value = data.items
      total.value = data.total
    } else {
      const data = await api.get<{ items: Transfer[]; total: number }>('/ledger/transfers', {
        page: transferPage.value,
        page_size: pageSize,
        date_from: transferFilters.date_from || undefined,
        date_to: transferFilters.date_to || undefined,
        account_id: transferFilters.account_id || undefined,
        keyword: transferFilters.keyword || undefined,
      })
      if (seq !== loadSeq) return
      transfers.value = data.items
      transferTotal.value = data.total ?? data.items.length
    }
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

/** 移动端筛选弹层的角标：收进去的条件里有几个生效 */
const advancedFilterCount = computed(() => [
  filters.direction, filters.category_id, filters.status, filters.source,
  filters.amount_min, filters.amount_max,
].filter(Boolean).length)

function resetFilters() {
  Object.assign(filters, {
    direction: '', account_id: '', category_id: '', status: '', source: '',
    keyword: '', date_from: '', date_to: '', amount_min: '', amount_max: '',
  })
  page.value = 1
  load()
}

function openEventDrawer() {
  fieldErrors.value = {}
  editingEventId.value = ''
  refundOf.value = null
  Object.assign(eventForm, {
    event_type: 'expense', title: '', amount: '', occurred_at: '',
    account_id: filters.account_id || '', category_id: '', asset_id: '', counterparty: '', note: '',
    repay_target: '',
  })
  Object.assign(recurring, { enabled: false, frequency: 'monthly', day_of_month: 1 })
  eventDrawer.value = true
}

function openRefund(row: EventRow) {
  fieldErrors.value = {}
  editingEventId.value = ''
  refundOf.value = row
  Object.assign(eventForm, {
    event_type: 'refund',
    title: `退款 · ${row.title}`,
    amount: row.amount,
    occurred_at: '',
    account_id: row.account_id || '',
    category_id: row.category_id || '',
    asset_id: '',
    counterparty: row.counterparty || '',
    note: '',
    repay_target: '',
  })
  eventDrawer.value = true
}

function closeEventDrawer() {
  eventDrawer.value = false
  refundOf.value = null
}

function openEditDraft(row: EventRow) {
  fieldErrors.value = {}
  editingEventId.value = row.id
  editingEventVersion.value = row.version
  Object.assign(eventForm, {
    event_type: row.event_type,
    title: row.title || '',
    amount: row.amount,
    occurred_at: toLocalDateTimeValue(row.occurred_at),
    account_id: row.account_id || '',
    category_id: row.category_id || '',
    asset_id: row.asset_id || '',
    counterparty: row.counterparty || '',
    note: row.note || '',
    repay_target: '',
  })
  eventDrawer.value = true
}

function openTransferDrawer() {
  fieldErrors.value = {}
  Object.assign(transferForm, {
    from_account_id: '', to_account_id: '', amount: '', fee: '',
    occurred_at: '', to_amount: '', exchange_rate: '', note: '',
  })
  transferDrawer.value = true
}

const transferNeedsRate = computed(() => {
  const from = accounts.value.find((a) => a.id === transferForm.from_account_id)
  const to = accounts.value.find((a) => a.id === transferForm.to_account_id)
  return Boolean(from && to && from.currency !== to.currency)
})

/** 还款类支出：不走普通草稿，直接落到还款单 / 内部转账，保证本金不重复计入支出。
 *  - 目标是信用卡账户（账户口径）→ 内部转账：扣款账户减少、信用卡欠款减少；
 *  - 目标是负债（房贷 / 车贷 / 信用卡负债）→ 还款单：本金减少剩余本金，利息与费用才计支出。 */
async function saveRepayment() {
  fieldErrors.value = {}
  const errors: Record<string, string> = {}
  const [kind, targetId] = (eventForm.repay_target || '').split(':')
  const amount = Number(eventForm.amount || 0)
  if (!eventForm.account_id) errors.account_id = '请选择扣款账户'
  if (!kind || !targetId) errors.repay_target = '请选择对应的信用卡 / 债务'
  if (!eventForm.amount || Number.isNaN(amount) || amount <= 0) errors.amount = '还款金额必须大于 0'
  if (kind === 'account' && eventForm.account_id && eventForm.account_id === targetId) {
    errors.repay_target = '扣款账户与要还的信用卡不能是同一个账户'
  }
  if (Object.keys(errors).length) {
    fieldErrors.value = errors
    return
  }
  saving.value = true
  try {
    if (kind === 'account') {
      await api.post('/ledger/transfers', {
        from_account_id: eventForm.account_id,
        to_account_id: targetId,
        amount: eventForm.amount,
        occurred_at: eventForm.occurred_at || null,
        note: eventForm.note || null,
      })
      toast.success('信用卡还款已登记：记为内部转账，信用卡欠款同步减少，不重复计入支出')
    } else {
      await api.post(`/ledger/liabilities/${targetId}/repayments`, {
        amount: eventForm.amount,
        account_id: eventForm.account_id,
        occurred_at: eventForm.occurred_at || null,
        note: eventForm.note || null,
      })
      toast.success('还款已登记：本金不重复计入支出，利息与费用计入支出')
    }
    eventDrawer.value = false
    // 还款会改变账户余额与剩余本金，参考数据一起刷新，下次选账户时金额才是最新的
    await Promise.all([loadRefs(), load()])
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function saveEvent(keepOpen = false) {
  if (repayMode.value) {
    await saveRepayment()
    return
  }
  saving.value = true
  fieldErrors.value = {}
  try {
    const body: Record<string, unknown> = {
      event_type: eventForm.event_type,
      title: eventForm.title,
      amount: eventForm.amount || '0',
      occurred_at: eventForm.occurred_at || null,
      account_id: eventForm.account_id || null,
      category_id: eventForm.category_id || null,
      asset_id: eventForm.asset_id || null,
      counterparty: eventForm.counterparty || null,
      note: eventForm.note || null,
      source: 'manual',
      refund_of: refundOf.value?.id || null,
    }
    if (editingEventId.value) {
      body.version = editingEventVersion.value
      await api.put(`/ledger/events/${editingEventId.value}`, body)
      toast.success('草稿已更新')
    } else {
      await api.post('/ledger/events', body)
      toast.success('草稿已保存。草稿不进入任何统计，确认后才形成正式业务事实。')
      // 支出 + 每月重复 → 同时登记一笔固定支出，到期自动记账
      if (eventForm.event_type === 'expense' && recurring.enabled) {
        await api.post('/ledger/recurring-expenses', {
          title: eventForm.title,
          amount: eventForm.amount || '0',
          frequency: recurring.frequency,
          day_of_month: recurring.day_of_month,
          account_id: eventForm.account_id || null,
          category_id: eventForm.category_id || null,
        })
      }
      // 收入 + 每月重复 → 同时登记一笔固定收入（工资等），到期自动记账
      if (eventForm.event_type === 'income' && recurringIncome.enabled) {
        await api.post('/ledger/recurring-incomes', {
          title: eventForm.title,
          amount: eventForm.amount || '0',
          frequency: recurringIncome.frequency,
          day_of_month: recurringIncome.day_of_month,
          account_id: eventForm.account_id || null,
          category_id: eventForm.category_id || null,
          member_id: recurringIncome.member_id || null,
        })
      }
    }
    if (keepOpen) {
      eventForm.title = ''
      eventForm.amount = ''
      eventForm.counterparty = ''
    } else {
      eventDrawer.value = false
    }
    await load()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function cancelEvent(row: EventRow) {
  const res = await ui.askConfirm({
    title: '取消草稿',
    message: `取消后该草稿转为「已取消」，不再计入任何统计，且不能恢复。目标：「${row.title}」。`,
    detail: ['已取消是终态，保留审计历史。', '如需要保留，可改用「编辑」修改内容。'],
    confirmText: '确认取消',
    danger: true,
  })
  if (!res.ok) return
  try {
    await api.post(`/ledger/events/${row.id}/cancel`, { version: row.version })
    toast.success('草稿已取消')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function saveTransfer() {
  saving.value = true
  fieldErrors.value = {}
  try {
    const body: Record<string, unknown> = {
      from_account_id: transferForm.from_account_id,
      to_account_id: transferForm.to_account_id,
      amount: transferForm.amount || '0',
      fee: transferForm.fee || '0',
      occurred_at: transferForm.occurred_at || null,
      note: transferForm.note || null,
    }
    if (transferNeedsRate.value) {
      body.to_amount = transferForm.to_amount
      body.exchange_rate = transferForm.exchange_rate
    }
    await api.post('/ledger/transfers', body)
    toast.success('转账已确认，已生成转账组，只改变账户余额')
    transferDrawer.value = false
    tab.value = 'transfers'
    await load()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function confirmEvent(row: EventRow) {
  const res = await ui.askConfirm({
    title: '确认业务事件',
    message: `确认后将生成资金变动并写入审计，草稿转为正式业务事实：「${row.title}」。`,
    detail: [`金额：${row.amount} ${row.currency}`, '确认后不能直接编辑关键金额，只能冲销或更正。'],
    confirmText: '确认事件',
  })
  if (!res.ok) return
  try {
    await api.post(`/ledger/events/${row.id}/confirm`, { version: row.version })
    toast.success('事件已确认，已生成资金变动')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function reverseEvent(row: EventRow) {
  const legs = (row as DisplayRow).legs?.length ? (row as DisplayRow).legs! : [row]
  const res = await ui.askConfirm({
    title: legs.length > 1 ? '冲销这笔还款' : '冲销业务事件',
    message: legs.length > 1
      ? `这笔还款由「本金」与「利息费用」两条记录组成，冲销会同时失效，历史记录保留。目标：「${row.title}」。`
      : `冲销后该事件不再计入统计与余额，历史记录会保留。目标：「${row.title}」。`,
    detail: ['已确认的业务事实不物理删除，只能冲销或更正。'],
    confirmText: '确认冲销',
    danger: true,
    requireReason: true,
    reasonLabel: '冲销原因（必填）',
  })
  if (!res.ok) return
  try {
    for (const leg of legs) {
      if (leg.status !== 'confirmed') continue
      await api.post(`/ledger/events/${leg.id}/reverse`, {
        reason: res.reason, version: leg.version,
      })
    }
    toast.success('事件已冲销，历史记录已保留')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function reverseTransfer(row: Transfer) {
  const res = await ui.askConfirm({
    title: '冲销内部转账',
    message: `冲销后转账组内的事件全部失效，账户余额回退。转账组：${row.group_no}。`,
    detail: ['历史记录保留，可在审计中追溯。'],
    confirmText: '确认冲销',
    danger: true,
    requireReason: true,
    reasonLabel: '冲销原因（必填）',
  })
  if (!res.ok) return
  try {
    await api.post(`/ledger/transfers/${row.id}/reverse`, { reason: res.reason })
    toast.success('转账已冲销')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

function openDetail(row: EventRow) {
  detail.value = row
  detailLegs.value = (row as DisplayRow).legs || []
  detailDrawer.value = true
}

/** 表格插槽拿到的是 Record<string, unknown>，这里统一收口成还款单信息 */
function repaymentOf(row: unknown): RepaymentInfo | null {
  return (row as DisplayRow | null)?.repayment || null
}

/** 合并行的 本金 / 利息 / 费用 拆分（取第一条明细里的还款单信息） */
const detailRepayment = computed(() => detailLegs.value[0]?.repayment || detail.value?.repayment || null)

function accountName(id: string | null) {
  return accounts.value.find((a) => a.id === id)?.name || '—'
}

function categoryName(id: string | null) {
  return categories.value.find((c) => c.id === id)?.name || '—'
}

function fmtDate(v: string | null) {
  return v ? new Date(v).toLocaleDateString('zh-CN') : '—'
}

onMounted(async () => {
  await Promise.allSettled([loadRefs(), load()])
  if (route.query.transfer) {
    openTransferDrawer()
  } else if (route.query.new) {
    // 支持 /ledger/transactions?new=1 直接进入记账（脉枢台等入口一键到底）
    openEventDrawer()
  }
})
</script>

<template>
  <div class="tx">
    <PageHeader
      title="流水"
      subtitle="草稿不计入统计；确认后生成资金变动与审计；已确认事件只能冲销，不能直接删除。"
      :breadcrumb="['家庭台账', '流水']"
    >
      <AppButton variant="secondary" @click="ui.openAiPanel('流水')">
        <Sparkles :size="15" /> AI 分析
      </AppButton>
      <AppButton v-if="canCreate" variant="secondary" @click="openEventDrawer">
        <Plus :size="15" /> 新增记录
      </AppButton>
      <AppButton v-if="canConfirm" variant="primary" @click="openTransferDrawer">
        <ArrowLeftRight :size="15" /> 内部转账
      </AppButton>
    </PageHeader>

    <div class="tabs">
      <button :class="{ on: tab === 'events' }" type="button" @click="tab = 'events'; page = 1; load()">
        流水记录
      </button>
      <button :class="{ on: tab === 'transfers' }" type="button" @click="tab = 'transfers'; transferPage = 1; load()">
        转账记录
      </button>
    </div>

    <AppCard v-if="tab === 'events'">
      <template #header>
        <AppToolbar
          :active-count="advancedFilterCount" :reset-text="'重置'"
          sheet-title="流水筛选" @reset="resetFilters" @apply="page = 1; load()"
        >
          <AppDateRangePicker
            v-model:start="filters.date_from" v-model:end="filters.date_to"
            class="period" placeholder="全部日期"
            @change="page = 1; load()"
          />
          <AppInput v-model="filters.keyword" placeholder="搜索标题或对方" class="sel2" @enter="page = 1; load()" />
          <AppSelect v-model="filters.account_id" :options="accountOptions" class="sel" @update:model-value="page = 1; load()" />
          <AppButton size="sm" variant="secondary" class="tb-query" @click="page = 1; load()">
            <RefreshCw :size="14" /> 查询
          </AppButton>
          <template #advanced>
            <AppSelect v-model="filters.direction" :options="directionOptions" class="sel" @update:model-value="page = 1; load()" />
            <AppSelect v-model="filters.category_id" :options="categoryOptions" class="sel" @update:model-value="page = 1; load()" />
            <AppSelect v-model="filters.status" :options="statusOptions" class="sel" @update:model-value="page = 1; load()" />
            <AppSelect v-model="filters.source" :options="sourceOptions" class="sel" @update:model-value="page = 1; load()" />
            <AppInput v-model="filters.amount_min" type="number" placeholder="最小金额" class="sel" />
            <AppInput v-model="filters.amount_max" type="number" placeholder="最大金额" class="sel" />
            <AppButton size="sm" variant="ghost" @click="resetFilters">重置</AppButton>
          </template>
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
      <StateBlock
        v-else-if="!rows.length"
        state="empty"
        title="没有符合条件的流水"
        description="可以调整筛选条件，或新增一笔收入/支出。"
      >
        <template #actions>
          <AppButton v-if="canCreate" variant="primary" @click="openEventDrawer">
            <Plus :size="15" /> 新增记录
          </AppButton>
        </template>
      </StateBlock>

      <template v-else>
        <AppDataTable :columns="columns" :rows="displayRows as never" mobile-title-key="title">
          <template #occurred_at="{ row }">{{ fmtDate((row as unknown as EventRow).occurred_at) }}</template>
          <template #title="{ row }">
            <div class="t-cell">
              <span class="t-title">{{ (row as unknown as EventRow).title }}</span>
              <span v-if="repaymentOf(row)" class="t-sub">
                本金 {{ formatAmount(repaymentOf(row)!.principal, ui.amountPrecision) }}
                · 利息 {{ formatAmount(repaymentOf(row)!.interest, ui.amountPrecision) }}
                <template v-if="Number(repaymentOf(row)!.fee) > 0">
                  · 费用 {{ formatAmount(repaymentOf(row)!.fee, ui.amountPrecision) }}
                </template>
              </span>
            </div>
          </template>
          <template #amount="{ row }">
            <MoneyText
              :value="(row as unknown as EventRow).amount"
              :currency="(row as unknown as EventRow).currency"
              show-currency
            />
          </template>
          <template #status_label="{ row }">
            <StatusBadge
              :label="(row as unknown as EventRow).status_label"
              :tone="(row as unknown as EventRow).status === 'confirmed' ? 'success'
                : (row as unknown as EventRow).status === 'draft' ? 'warning' : 'neutral'"
            />
          </template>
          <template #actions="{ row }">
            <div class="row-actions">
              <button class="act" type="button" @click="openDetail(row as unknown as EventRow)">详情</button>
              <button
                v-if="canEditDraft && (row as unknown as EventRow).status === 'draft'"
                class="act"
                type="button"
                @click="openEditDraft(row as unknown as EventRow)"
              >
                编辑
              </button>
              <button
                v-if="canConfirm && (row as unknown as EventRow).status === 'draft'"
                class="act"
                type="button"
                @click="confirmEvent(row as unknown as EventRow)"
              >
                <CheckCircle2 :size="13" /> 确认
              </button>
              <button
                v-if="canEditDraft && (row as unknown as EventRow).status === 'draft'"
                class="act danger"
                type="button"
                @click="cancelEvent(row as unknown as EventRow)"
              >
                取消
              </button>
              <button
                v-if="canCreate && (row as unknown as EventRow).status === 'confirmed'
                  && (row as unknown as EventRow).event_type === 'expense'"
                class="act"
                type="button"
                @click="openRefund(row as unknown as EventRow)"
              >
                <RotateCcw :size="13" /> 退款
              </button>
              <button
                v-if="canReverse && (row as unknown as EventRow).status === 'confirmed'"
                class="act danger"
                type="button"
                @click="reverseEvent(row as unknown as EventRow)"
              >
                <Undo2 :size="13" /> 冲销
              </button>
            </div>
          </template>
        </AppDataTable>
        <div class="pager">
          <span>共 {{ total }} 条</span>
          <button class="act" type="button" :disabled="page <= 1" @click="page -= 1; load()">上一页</button>
          <span>第 {{ page }} 页</span>
          <button class="act" type="button" :disabled="page * pageSize >= total" @click="page += 1; load()">下一页</button>
        </div>
      </template>
    </AppCard>

    <AppCard v-else>
      <template #header>
        <AppToolbar :active-count="transferActiveCount" sheet-title="转账筛选"
                    @reset="transferFilters.date_from = ''; transferFilters.date_to = '';
                            transferFilters.account_id = ''; transferFilters.keyword = '';
                            transferPage = 1; load()">
          <AppDateRangePicker
            v-model:start="transferFilters.date_from" v-model:end="transferFilters.date_to"
            class="period" placeholder="全部日期"
            @change="transferPage = 1; load()"
          />
          <AppSelect v-model="transferFilters.account_id" :options="accountOptions" class="sel"
                     @update:model-value="transferPage = 1; load()" />
          <AppInput v-model="transferFilters.keyword" placeholder="搜索单号或备注" class="sel2"
                    @enter="transferPage = 1; load()" />
          <AppButton size="sm" variant="secondary" class="tb-query"
                     @click="transferPage = 1; load()">
            <RefreshCw :size="14" /> 查询
          </AppButton>
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="!transfers.length" state="empty" title="还没有内部转账"
                  description="内部转账只改变账户余额，不改变收入与支出。" />
      <AppDataTable v-else :columns="transferColumns" :rows="transfers as never" mobile-title-key="group_no">
        <template #occurred_at="{ row }">{{ fmtDate((row as unknown as Transfer).occurred_at) }}</template>
        <template #amount="{ row }">
          <MoneyText :value="(row as unknown as Transfer).amount" :currency="(row as unknown as Transfer).currency" show-currency />
        </template>
        <template #status_label="{ row }">
          <StatusBadge
            :label="(row as unknown as Transfer).status_label"
            :tone="(row as unknown as Transfer).status === 'confirmed' ? 'success' : 'neutral'"
          />
        </template>
        <template #actions="{ row }">
          <button
            v-if="canReverse && (row as unknown as Transfer).status === 'confirmed'"
            class="act danger"
            type="button"
            @click="reverseTransfer(row as unknown as Transfer)"
          >
            <Undo2 :size="13" /> 冲销
          </button>
          <span v-else class="muted">—</span>
        </template>
      </AppDataTable>
      <div v-if="transferTotal > pageSize" class="pager">
        <span>共 {{ transferTotal }} 条</span>
        <button class="act" type="button" :disabled="transferPage <= 1"
                @click="transferPage -= 1; load()">上一页</button>
        <span>第 {{ transferPage }} 页</span>
        <button class="act" type="button" :disabled="transferPage * pageSize >= transferTotal"
                @click="transferPage += 1; load()">下一页</button>
      </div>
    </AppCard>

    <!-- 新增收入/支出 -->
    <AppDrawer :open="eventDrawer"
               :title="refundOf ? '登记退款'
                 : (editingEventId ? '编辑草稿'
                   : (repayMode ? `${repayCategoryName}（登记还款）` : '新增收入 / 支出（保存为草稿）'))"
               @close="closeEventDrawer">
      <div class="form">
        <AppField v-if="refundOf" label="退款对象">
          <p class="muted">
            原支出「{{ refundOf.title }}」{{ refundOf.amount }} {{ refundOf.currency }}；
            退款会退回原账户并冲减支出统计（不计入收入），金额可改为部分退款。
          </p>
        </AppField>
        <AppField v-else label="事件类型" required :error="fieldErrors.event_type"
                  :hint="repayMode ? '还款属于资金流出，方向固定为支出' : '草稿类型不可修改'">
          <AppSelect v-model="eventForm.event_type" :options="eventTypeOptions" :disabled="!!editingEventId" />
        </AppField>
        <CategoryPicker v-model="eventForm.category_id"
                        :domain="eventForm.event_type === 'income' ? 'income' : 'expense'"
                        :error="fieldErrors.category_id"
                        :hint="repayMode
                          ? '选到「还款」下的分类后，表单会自动切成为还款单（信用卡还账户、贷款还负债）'
                          : '先选分类（可点「＋」新增，子分类直接可选），再填标题与金额'" />

        <!-- 还款类（信用卡还款 / 房贷 / 车贷 / 其他贷款）：必须有「扣款账户 + 对应债务」两个去向 -->
        <template v-if="repayMode">
          <AppField label="对应的信用卡 / 债务" required :error="fieldErrors.repay_target" :hint="repayHint">
            <AppSelect v-model="eventForm.repay_target" :options="repayTargetOptions"
                       placeholder="请选择要还的信用卡或债务" />
          </AppField>
          <p v-if="!repayTargetOptions.length" class="acct-warn">
            还没有可选的信用卡或债务：信用卡请先在「账户」里建档（填授信额度即为信用卡），房贷车贷请先在「负债」里建档。
          </p>
          <AppField label="还款金额" required :error="fieldErrors.amount">
            <AppInput v-model="eventForm.amount" type="number" placeholder="0.00" />
          </AppField>
          <AppField label="扣款账户" required :error="fieldErrors.account_id" hint="钱从哪个账户扣出去（信用卡不能给自己还款）">
            <AppSelect v-model="eventForm.account_id" :options="payFromOptions" />
            <p v-if="selectedAccountInfo" class="acct-info">{{ selectedAccountInfo }}</p>
          </AppField>
          <AppField label="还款时间" :error="fieldErrors.occurred_at" hint="留空表示当前时间">
            <AppDatePicker v-model="eventForm.occurred_at" value-format="datetime" with-time
                          placeholder="留空表示当前时间" />
          </AppField>
          <AppField label="备注" :error="fieldErrors.note">
            <AppInput v-model="eventForm.note" placeholder="例如：本期账单还款" />
          </AppField>
        </template>

        <template v-else>
          <AppField label="标题" required :error="fieldErrors.title">
            <AppInput v-model="eventForm.title" placeholder="例如：超市采购" />
          </AppField>
          <AppField label="金额" required :error="fieldErrors.amount">
            <AppInput v-model="eventForm.amount" type="number" placeholder="0.00" />
          </AppField>
          <AppField label="账户" :error="fieldErrors.account_id" hint="选择账户后币种自动跟随账户">
            <AppSelect v-model="eventForm.account_id" :options="accountOptionsRequired" />
            <p v-if="selectedAccountInfo" class="acct-info">{{ selectedAccountInfo }}</p>
          </AppField>
          <AppField
            label="关联资产（可选）" :error="fieldErrors.asset_id"
            hint="关联后该笔会进入对应资产的「费用流水」，用于车辆燃油/保养、房产水电维修等费用归集"
          >
            <AppSelect v-model="eventForm.asset_id" :options="assetOptions" />
          </AppField>
          <AppField label="发生时间" :error="fieldErrors.occurred_at" hint="留空表示当前时间">
            <AppDatePicker v-model="eventForm.occurred_at" value-format="datetime" with-time
                          placeholder="留空表示当前时间" />
          </AppField>
          <AppField label="商户 / 对方" :error="fieldErrors.counterparty">
            <AppInput v-model="eventForm.counterparty" />
          </AppField>
          <AppField label="备注" :error="fieldErrors.note">
            <AppInput v-model="eventForm.note" />
          </AppField>
        </template>
        <template v-if="eventForm.event_type === 'expense' && !editingEventId && !refundOf && !repayMode">
          <AppField label="每月重复">
            <AppSwitch v-model="recurring.enabled" label="设为每月固定支出" />
            <p class="muted">话费月租、订阅等金额固定的支出，勾选后每月到期自动记账，无需重复录入。</p>
          </AppField>
          <template v-if="recurring.enabled">
            <div class="grid-2">
              <AppField label="扣款日">
                <AppInput v-model="recurring.day_of_month" type="number" min="1" max="28" />
              </AppField>
              <AppField label="周期">
                <AppSelect v-model="recurring.frequency" :options="[
                  { value: 'monthly', label: '每月' },
                  { value: 'weekly', label: '每周' },
                  { value: 'yearly', label: '每年' },
                ]" />
              </AppField>
            </div>
          </template>
        </template>
        <template v-if="eventForm.event_type === 'income' && !editingEventId && !refundOf">
          <AppField label="每月重复">
            <AppSwitch v-model="recurringIncome.enabled" label="设为每月固定收入" />
            <p class="muted">工资、养老金等金额固定的收入，勾选后每月到期自动记账，无需重复录入。</p>
          </AppField>
          <template v-if="recurringIncome.enabled">
            <div class="grid-2">
              <AppField label="发薪日">
                <AppInput v-model="recurringIncome.day_of_month" type="number" min="1" max="28" />
              </AppField>
              <AppField label="周期">
                <AppSelect v-model="recurringIncome.frequency" :options="[
                  { value: 'monthly', label: '每月' },
                  { value: 'weekly', label: '每周' },
                  { value: 'yearly', label: '每年' },
                ]" />
              </AppField>
            </div>
            <AppField label="归属成员（可选）">
              <AppSelect v-model="recurringIncome.member_id" :options="memberOptions" />
            </AppField>
          </template>
        </template>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="closeEventDrawer()">取消</AppButton>
        <template v-if="editingEventId">
          <AppButton variant="primary" :loading="saving" @click="saveEvent(false)">保存修改</AppButton>
        </template>
        <template v-else-if="repayMode">
          <AppButton variant="primary" :loading="saving" @click="saveEvent(false)">登记还款</AppButton>
        </template>
        <template v-else>
          <AppButton variant="secondary" :loading="saving" @click="saveEvent(true)">保存并继续</AppButton>
          <AppButton variant="primary" :loading="saving" @click="saveEvent(false)">保存草稿</AppButton>
        </template>
      </template>
    </AppDrawer>

    <!-- 内部转账 -->
    <AppDrawer :open="transferDrawer" title="内部转账" @close="transferDrawer = false">
      <div class="form">
        <AppField label="转出账户" required :error="fieldErrors.from_account_id">
          <AppSelect v-model="transferForm.from_account_id" :options="accountOptionsRequired" />
        </AppField>
        <AppField label="转入账户" required :error="fieldErrors.to_account_id"
                  hint="转出与转入不能是同一个账户">
          <AppSelect v-model="transferForm.to_account_id" :options="accountOptionsRequired" />
        </AppField>
        <AppField label="转账金额" required :error="fieldErrors.amount">
          <AppInput v-model="transferForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="手续费" :error="fieldErrors.fee" hint="手续费单独记为支出，不计入转账本金">
          <AppInput v-model="transferForm.fee" type="number" placeholder="0.00" />
        </AppField>
        <template v-if="transferNeedsRate">
          <div class="rate-note">
            两个账户币种不同。系统不会默认按 1 换算，请填写本次实际使用的汇率与到账金额。
          </div>
          <AppField label="对方账户到账金额" required :error="fieldErrors.to_amount">
            <AppInput v-model="transferForm.to_amount" type="number" />
          </AppField>
          <AppField label="本次使用汇率" required :error="fieldErrors.exchange_rate">
            <AppInput v-model="transferForm.exchange_rate" type="number" />
          </AppField>
        </template>
        <AppField label="日期" :error="fieldErrors.occurred_at" hint="留空表示当前时间">
          <AppDatePicker v-model="transferForm.occurred_at" value-format="datetime" with-time
                        placeholder="留空表示当前时间" />
        </AppField>
        <AppField label="备注" :error="fieldErrors.note">
          <AppInput v-model="transferForm.note" />
        </AppField>
        <p class="hint">
          确认后生成转账组并立即生效；内部转账只改变账户余额，不改变收入与支出。
        </p>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="transferDrawer = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="saveTransfer">确认转账</AppButton>
      </template>
    </AppDrawer>

    <!-- 详情 -->
    <AppDrawer :open="detailDrawer" title="流水详情" :width="560" @close="detailDrawer = false">
      <ul v-if="detail" class="kv">
        <li><span class="k">事件编号</span><span class="v">{{ detail.event_no }}</span></li>
        <li><span class="k">标题</span><span class="v">{{ detail.title }}</span></li>
        <li>
          <span class="k">类型 / 状态</span>
          <span class="v">{{ detailTypeText }} · {{ detail.status_label }}</span>
        </li>
        <li><span class="k">金额</span><span class="v strong">{{ detail.amount }} {{ detail.currency }}</span></li>
        <li v-if="detailRepayment">
          <span class="k">还款拆分</span>
          <span class="v">
            本金 {{ formatAmount(detailRepayment.principal, ui.amountPrecision) }}
            · 利息 {{ formatAmount(detailRepayment.interest, ui.amountPrecision) }}
            <template v-if="Number(detailRepayment.fee) > 0">
              · 费用 {{ formatAmount(detailRepayment.fee, ui.amountPrecision) }}
            </template>
          </span>
        </li>
        <li v-if="detailRepayment">
          <span class="k">关联负债</span>
          <span class="v">{{ detailRepayment.liability_name || '—' }}</span>
        </li>
        <li v-if="detail.realized_gain !== null && detail.realized_gain !== undefined">
          <span class="k">已实现损益</span>
          <span class="v strong">{{ Number(detail.realized_gain) >= 0 ? '+' : '' }}{{ detail.realized_gain }} {{ detail.currency }}</span>
        </li>
        <li><span class="k">发生时间</span><span class="v">{{ fmtDate(detail.occurred_at) }}</span></li>
        <li><span class="k">账户</span><span class="v">{{ accountName(detail.account_id) }}</span></li>
        <li><span class="k">分类</span><span class="v">{{ categoryName(detail.category_id) }}</span></li>
        <li><span class="k">商户 / 对方</span><span class="v">{{ detail.counterparty || '—' }}</span></li>
        <li><span class="k">来源</span><span class="v">{{ detail.source_label }}</span></li>
        <li><span class="k">是否计入统计</span><span class="v">{{ detail.counts_in_stats ? '计入' : '不计入' }}</span></li>
      </ul>
      <h4 v-if="detail" class="sec">关联对象</h4>
      <ul v-if="detail" class="kv">
        <li><span class="k">账户</span><span class="v">{{ accountName(detail.account_id) }}</span></li>
        <li><span class="k">分类</span><span class="v">{{ categoryName(detail.category_id) }}</span></li>
        <li><span class="k">资产</span><span class="v">{{ assetName(detail.asset_id) }}</span></li>
      </ul>
      <h4 v-if="detail?.movements?.length" class="sec">资金变动</h4>
      <ul v-if="detail?.movements?.length" class="flow">
        <li v-for="(m, i) in detail.movements" :key="i">
          <span>{{ m.direction_label }}</span>
          <span class="f-amount">{{ m.amount }}</span>
        </li>
      </ul>
      <template v-if="detailLegs.length">
        <h4 class="sec">记账明细（同一笔还款拆成两条）</h4>
        <ul class="legs">
          <li v-for="leg in detailLegs" :key="leg.id">
            <div class="leg-main">
              <strong>{{ leg.event_type_label }} · {{ leg.title }}</strong>
              <small>{{ leg.event_no }}</small>
            </div>
            <div class="leg-right">
              <span class="leg-amount">{{ formatAmount(leg.amount, ui.amountPrecision) }} {{ leg.currency }}</span>
              <StatusBadge
                :label="leg.status_label"
                :tone="leg.status === 'confirmed' ? 'success' : leg.status === 'draft' ? 'warning' : 'neutral'"
              />
            </div>
          </li>
        </ul>
        <p class="hint">本金不重复计入支出，利息与费用计入支出；两行合计等于本次还款额。</p>
      </template>
      <p class="hint">字段级变更前后差异与操作记录可在「设置 / 系统状态与审计」中查看。</p>
    </AppDrawer>
  </div>
</template>

<style scoped>
.tx { display: flex; flex-direction: column; gap: var(--space-4); }
.tabs { display: inline-flex; flex-wrap: wrap; max-width: 100%; gap: 2px; padding: 2px; align-self: flex-start; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.tabs button { height: 30px; padding: 0 14px; border: none; border-radius: 9px; background: transparent; color: var(--ink-muted); font-size: 13px; cursor: pointer; }
.tabs button.on { color: var(--ink-strong); background: var(--glass-3); font-weight: 600; }
.filters { display: flex; flex-direction: column; gap: var(--space-2); width: 100%; }
.t-cell { display: flex; flex-direction: column; gap: 2px; }
.t-title { color: var(--ink-strong); }
.t-sub { font-size: 11.5px; color: var(--ink-muted); font-variant-numeric: tabular-nums; }
.legs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.legs li {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 9px 11px; border: 1px solid var(--border);
  border-radius: var(--glass-radius-control); background: var(--glass-1);
}
.leg-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.leg-main strong { font-size: 13px; color: var(--ink-strong); }
.leg-main small { font-size: 11px; color: var(--ink-muted); }
.leg-right { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
.leg-amount { font-size: 13px; font-variant-numeric: tabular-nums; color: var(--ink-strong); }
.f-row { display: flex; gap: var(--space-2); flex-wrap: wrap; }
.sel { width: 148px; flex: none; }
.period { width: 286px; flex: none; }
.sel2 { flex: 1 1 200px; min-width: 180px; max-width: 320px; }
/* 查询按钮固定靠右，避免每行长度不一时控件漂移 */
.tb-query { margin-left: auto; flex: none; }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act {
  display: inline-flex; align-items: center; gap: 4px;
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }
.act:disabled { opacity: 0.5; cursor: not-allowed; }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.pager { display: flex; align-items: center; justify-content: flex-end; gap: var(--space-3); margin-top: var(--space-3); font-size: 12.5px; color: var(--ink-muted); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
/* 选中账户后的补充信息：余额 / 欠款 / 可用额度，选完不必再跳去账户页确认 */
.acct-info {
  margin-top: 6px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: var(--glass-1);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.acct-warn {
  margin-top: -4px;
  font-size: 12px;
  color: var(--warning);
  line-height: 1.6;
}
.rate-note { padding: 10px 12px; border-radius: var(--radius-sm); background: rgba(244, 166, 42, 0.1); border: 1px solid rgba(244, 166, 42, 0.36); color: var(--warning); font-size: 12.5px; }
.hint { font-size: 12px; color: var(--ink-muted); margin-top: var(--space-2); }
.kv { display: flex; flex-direction: column; }
.kv li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 9px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.kv .k { color: var(--ink-muted); }
.kv .v { color: var(--ink-strong); }
.kv .v.strong { font-size: 16px; font-weight: 600; }
.sec { margin: var(--space-5) 0 var(--space-2); font-size: 13.5px; color: var(--ink-strong); }
.flow { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.flow li { display: flex; justify-content: space-between; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.f-amount { font-variant-numeric: tabular-nums; color: var(--ink-strong); }
.muted { color: var(--ink-muted); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
@media (max-width: 700px) {
  .sel, .period { width: 100%; }
  .grid-2 { grid-template-columns: 1fr; }
}
</style>
