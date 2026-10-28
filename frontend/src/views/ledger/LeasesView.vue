<script setup lang="ts">
/**
 * 房产租赁 /ledger/leases（06 §9、15 §3.3）。
 * 视图：房产列表 / 租约列表；详情页签：概览、应收、实收与事件、押金、AI。
 * 应收不改变账户余额；实收计入收入；押金不计收支（06 §9）。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { CalendarClock, FileText, Plus, Sparkles, Users } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { formatAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppToolbar from '@/components/ui/AppToolbar.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppDateRangePicker from '@/components/ui/AppDateRangePicker.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppModal from '@/components/ui/AppModal.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import CategoryPicker from '@/components/CategoryPicker.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { accountLabel } from '@/utils/account'

interface Lease { id: string; lease_no: string; category_id: string | null; category_label: string; real_estate_id: string; real_estate_name: string | null; tenant_name: string; monthly_rent: string; payment_frequency: string; payment_frequency_label: string; currency: string; start_date: string | null; end_date: string | null; deposit_amount: string; status: string; status_label: string; version: number; receiving_account_id: string | null; property_repair_bearer: string | null; reminder_days: number | null; lease_purpose: string | null; tenant_contact: string | null; note: string | null; deposit_received: boolean; deposit_include_in_net_worth: boolean; next_receipt_date: string | null; deposit_summary: { count: number; source: string; held: string; returned: string; received: boolean; received_at: string | null } }
interface Receivable { id: string; due_date: string | null; amount: string; currency: string; status: string; status_label: string; overdue: boolean; period_start: string | null; period_end: string | null }
interface Deposit { id: string; amount: string; currency: string; status: string; status_label: string; received_at: string | null }
interface LeaseDetail extends Lease { receivables: Receivable[]; deposits: Deposit[]; events: unknown[]; utilities: { id: string; utility_type_label: string; account_no: string }[]; receivable_summary: { pending: number; paid: number; total_pending: string } }
interface AccountLite { id: string; name: string; currency: string; status: string }

const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const rows = ref<Lease[]>([])
const keyword = ref('')
const statusFilter = ref('')
const categoryFilter = ref('')
const categories = ref<{ id: string; name: string }[]>([])
const accounts = ref<AccountLite[]>([])
const realEstates = ref<{ id: string; name: string }[]>([])

const canCreate = computed(() => auth.can('create_draft'))
const canEdit = computed(() => auth.can('edit_draft'))
const canConfirm = computed(() => auth.can('confirm'))
const canArchive = computed(() => auth.can('archive'))

function listParams(): Record<string, string> {
  const params: Record<string, string> = {}
  if (keyword.value) params.keyword = keyword.value
  if (statusFilter.value) params.status = statusFilter.value
  if (categoryFilter.value) params.category_id = categoryFilter.value
  return params
}

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const res = await api.get<{ items: unknown[] }>('/ledger/leases', listParams())
    if (seq !== loadSeq) return
    rows.value = res.items as Lease[]
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

/** 在详情里登记完（实收 / 押金）后同步卡片数据，不把列表换成骨架屏 */
async function refreshQuietly() {
  try {
    const res = await api.get<{ items: unknown[] }>('/ledger/leases', listParams())
    rows.value = res.items as Lease[]
  } catch {
    /* 静默失败：保留旧列表，不打扰正在看详情的人 */
  }
}

async function loadRefs() {
  const [acc, ast, cats] = await Promise.all([
    api.get<{ items: AccountLite[] }>('/ledger/accounts'),
    api.get<{ items: { id: string; name: string; asset_type: string }[] }>(
      '/ledger/assets', { asset_type: 'real_estate', page_size: 200 }),
    api.get<{ items: { id: string; name: string; status: string }[] }>('/settings/categories', { domain: 'lease' }),
  ])
  accounts.value = acc.items.filter((a) => a.status === 'active')
  realEstates.value = ast.items.map((a) => ({ id: a.id, name: a.name }))
  categories.value = (cats.items || []).filter((c) => c.status === 'active')
}

onMounted(() => { load(); loadRefs() })

const accountOptions = computed(() => [
  { value: '', label: '请选择收款账户' },
  ...accounts.value.map((a) => ({ value: a.id, label: accountLabel(a, ui.amountPrecision) })),
])
const realEstateOptions = computed(() => [
  { value: '', label: '请选择房产' },
  ...realEstates.value.map((r) => ({ value: r.id, label: r.name })),
])
const categoryOptions = computed(() => [
  { value: '', label: '全部分类' },
  ...categories.value.map((c) => ({ value: c.id, label: c.name })),
])
// ---- 新建 / 编辑 ----
const editorOpen = ref(false)
const editingId = ref('')
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})
const form = reactive({
  real_estate_id: '', tenant_name: '', tenant_contact: '', lease_purpose: '', category_id: '',
  monthly_rent: '', payment_frequency: 'monthly', currency: 'CNY',
  start_date: '', end_date: '', deposit_amount: '', receiving_account_id: '',
  property_repair_bearer: '', reminder_days: '', note: '',
  deposit_received: false, deposit_include_in_net_worth: false,
})

function openCreate() {
  editingId.value = ''
  fieldErrors.value = {}
  Object.assign(form, {
    real_estate_id: '', tenant_name: '', tenant_contact: '', lease_purpose: '', category_id: '',
    monthly_rent: '', payment_frequency: 'monthly', currency: 'CNY',
    start_date: '', end_date: '', deposit_amount: '', receiving_account_id: '',
    property_repair_bearer: '', reminder_days: '', note: '',
    deposit_received: false, deposit_include_in_net_worth: false,
  })
  editorOpen.value = true
}
function openEdit(row: Lease) {
  editingId.value = row.id
  // 乐观锁：必须回填当前版本，否则后端 update_lease 的版本校验永远 409
  editingVersion.value = row.version ?? 1
  fieldErrors.value = {}
  Object.assign(form, {
    real_estate_id: row.real_estate_id, tenant_name: row.tenant_name,
    tenant_contact: row.tenant_contact || '', lease_purpose: row.lease_purpose || '',
    category_id: row.category_id || '',
    monthly_rent: row.monthly_rent, payment_frequency: row.payment_frequency, currency: row.currency,
    start_date: row.start_date?.slice(0, 10) || '', end_date: row.end_date?.slice(0, 10) || '',
    deposit_amount: row.deposit_amount, receiving_account_id: row.receiving_account_id || '',
    property_repair_bearer: row.property_repair_bearer || '',
    reminder_days: row.reminder_days != null ? String(row.reminder_days) : '',
    note: row.note || '',
    deposit_received: row.deposit_received === true,
    deposit_include_in_net_worth: row.deposit_include_in_net_worth === true,
  })
  editorOpen.value = true
}
async function save() {
  saving.value = true
  fieldErrors.value = {}
  const body = {
    real_estate_id: form.real_estate_id || null,
    tenant_name: form.tenant_name,
    tenant_contact: form.tenant_contact || null,
    lease_purpose: form.lease_purpose || null,
    category_id: form.category_id || null,
    monthly_rent: form.monthly_rent || '0',
    payment_frequency: form.payment_frequency,
    currency: form.currency,
    start_date: form.start_date || null,
    end_date: form.end_date || null,
    deposit_amount: form.deposit_amount || '0',
    receiving_account_id: form.receiving_account_id || null,
    property_repair_bearer: form.property_repair_bearer || null,
    reminder_days: form.reminder_days === '' ? null : form.reminder_days,
    note: form.note || null,
    deposit_received: form.deposit_received,
    deposit_include_in_net_worth: form.deposit_received && form.deposit_include_in_net_worth,
  }
  try {
    if (editingId.value) {
      await api.put(`/ledger/leases/${editingId.value}`, { ...body, version: editingVersion.value })
      toast.success('租约已更新')
    } else {
      await api.post('/ledger/leases', body)
      toast.success('租约已创建')
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
const editingVersion = ref(1)

// ---- 详情 ----
const detailOpen = ref(false)
const detail = ref<LeaseDetail | null>(null)
const activeTab = ref('overview')
const tabs = [
  { key: 'overview', label: '概览' },
  { key: 'receivables', label: '应收' },
  { key: 'events', label: '实收' },
  { key: 'deposits', label: '押金' },
  { key: 'ai', label: 'AI 分析' },
]
async function openDetail(row: Lease) {
  activeTab.value = 'overview'
  contactRevealed.value = false
  detailOpen.value = true
  await loadDetail(row.id)
}
async function loadDetail(id: string) {
  try {
    detail.value = await api.get<LeaseDetail>(`/ledger/leases/${id}`)
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

// ---- 应收 ----
const receivableOpen = ref(false)
const receivableSaving = ref(false)
const receivableErrors = ref<Record<string, string>>({})
const receivableForm = reactive({ amount: '', due_date: '', period_start: '', period_end: '', note: '' })
function openReceivable() {
  receivableErrors.value = {}
  Object.assign(receivableForm, { amount: '', due_date: '', period_start: '', period_end: '', note: '' })
  receivableOpen.value = true
}
async function saveReceivable() {
  receivableSaving.value = true
  receivableErrors.value = {}
  try {
    await api.post(`/ledger/leases/${detail.value!.id}/receivables`, {
      amount: receivableForm.amount,
      due_date: receivableForm.due_date || null,
      period_start: receivableForm.period_start || null,
      period_end: receivableForm.period_end || null,
      note: receivableForm.note || null,
    })
    toast.success('租金应收已登记（不改变账户余额）')
    receivableOpen.value = false
    await loadDetail(detail.value!.id)
  } catch (err) {
    receivableErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    receivableSaving.value = false
  }
}

// ---- 实收 ----
interface ReceiptPreview {
  amount: string; currency: string
  payment_frequency: string; payment_frequency_label: string
  occurred_at: string | null
  receivable_id: string | null; receivable_amount: string | null; due_date: string | null
  next_due_date: string | null; period_start: string | null; period_end: string | null
  lease_end_date: string | null
}
const receiptOpen = ref(false)
const receiptSaving = ref(false)
const receiptErrors = ref<Record<string, string>>({})
const receiptForm = reactive({ amount: '', account_id: '', receivable_id: '', occurred_at: '', note: '' })
const receiptPreview = ref<ReceiptPreview | null>(null)
const receiptPreviewLoading = ref(false)

function todayLocal() {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 金额、匹配应收、下次收租日都由后端按「付款周期 × 月租」算，前端只负责展示 */
async function loadReceiptPreview() {
  if (!detail.value) return
  receiptPreviewLoading.value = true
  try {
    receiptPreview.value = await api.get<ReceiptPreview>(
      `/ledger/leases/${detail.value.id}/receipt-preview`,
      { occurred_at: receiptForm.occurred_at || undefined },
    )
  } catch {
    receiptPreview.value = null
  } finally {
    receiptPreviewLoading.value = false
  }
}

async function openReceipt() {
  receiptErrors.value = {}
  Object.assign(receiptForm, {
    amount: '', account_id: detail.value?.receiving_account_id || '',
    receivable_id: '', occurred_at: todayLocal(), note: '',
  })
  receiptOpen.value = true
  await loadReceiptPreview()
  if (receiptPreview.value) {
    // 默认按周期折算（月租 × 季 = 3 个月），金额仍可手改
    receiptForm.amount = receiptPreview.value.amount
    receiptForm.receivable_id = receiptPreview.value.receivable_id || ''
  }
}

// 换到账日期 → 重算下次收租日
watch(() => receiptForm.occurred_at, () => {
  if (receiptOpen.value) void loadReceiptPreview()
})
async function saveReceipt() {
  receiptSaving.value = true
  receiptErrors.value = {}
  try {
    await api.post(`/ledger/leases/${detail.value!.id}/receipts`, {
      amount: receiptForm.amount,
      account_id: receiptForm.account_id || null,
      receivable_id: receiptForm.receivable_id || null,
      occurred_at: receiptForm.occurred_at || null,
      note: receiptForm.note || null,
    })
    toast.success('租金实收已确认并计入收入')
    receiptOpen.value = false
    await loadDetail(detail.value!.id)
    await refreshQuietly()
  } catch (err) {
    receiptErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    receiptSaving.value = false
  }
}

// ---- 押金 ----
const depositOpen = ref(false)
const depositSaving = ref(false)
const depositErrors = ref<Record<string, string>>({})
const depositForm = reactive({ amount: '', account_id: '', occurred_at: '', note: '' })
function openDepositReceive() {
  depositErrors.value = {}
  Object.assign(depositForm, {
    amount: detail.value?.deposit_amount || '', account_id: detail.value?.receiving_account_id || '',
    occurred_at: '', note: '',
  })
  depositOpen.value = true
}
async function saveDeposit() {
  depositSaving.value = true
  depositErrors.value = {}
  try {
    await api.post(`/ledger/leases/${detail.value!.id}/deposits/receive`, {
      amount: depositForm.amount, account_id: depositForm.account_id || null,
      occurred_at: depositForm.occurred_at || null, note: depositForm.note || null,
    })
    toast.success('押金已收取（不计入收入）')
    depositOpen.value = false
    await loadDetail(detail.value!.id)
    await refreshQuietly()
  } catch (err) {
    depositErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    depositSaving.value = false
  }
}
async function returnDeposit(deposit: Deposit) {
  if (!detail.value) return
  const { ok } = await ui.askConfirm({
    title: '退还押金',
    message: `将退还押金 ${deposit.amount} ${deposit.currency}，该操作不计入支出。`,
    confirmText: '确认退还',
  })
  if (!ok) return
  try {
    await api.post(`/ledger/leases/${detail.value.id}/deposits/${deposit.id}/return`, {
      account_id: detail.value.receiving_account_id || accounts.value[0]?.id || null,
    })
    toast.success('押金已退还（不计入支出）')
    await loadDetail(detail.value.id)
    await refreshQuietly()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

/** 下载文件：带鉴权头取 blob，文件名优先用服务端 Content-Disposition */
async function downloadFile(url: string, fallback: string) {
  try {
    const token = localStorage.getItem('fam.access_token') || ''
    const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
    if (!resp.ok) {
      toast.error('下载失败，请稍后重试')
      return
    }
    const disposition = resp.headers.get('Content-Disposition') || ''
    const matched = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
    const filename = matched ? decodeURIComponent(matched[1]) : fallback
    const href = URL.createObjectURL(await resp.blob())
    const a = document.createElement('a')
    a.href = href
    a.download = filename
    a.click()
    window.setTimeout(() => URL.revokeObjectURL(href), 1000)
  } catch {
    toast.error('下载失败')
  }
}

/** 空白合同模板：没有租约或想手写时用 */
// ---------------- 合同：先预览，再下载 ----------------
const contractOpen = ref(false)
const contractLoading = ref(false)
const contractTitle = ref('')
const contractParagraphs = ref<{ text: string; heading: boolean }[]>([])
const contractFile = ref('')
const contractPath = ref('')

async function openContractPreview(path: string, fallbackName: string) {
  contractOpen.value = true
  contractLoading.value = true
  contractParagraphs.value = []
  contractPath.value = path
  contractFile.value = fallbackName
  try {
    const res = await api.get<{ title: string; paragraphs: { text: string; heading: boolean }[]; filename: string }>(
      `${path}/preview`,
    )
    contractTitle.value = res.title
    contractParagraphs.value = res.paragraphs || []
    contractFile.value = res.filename || fallbackName
  } catch (err) {
    toast.error(errorMessage(err))
    contractOpen.value = false
  } finally {
    contractLoading.value = false
  }
}

function previewContractTemplate() {
  void openContractPreview('/ledger/leases/contract-template', '房屋租赁合同-空白模板.docx')
}

/** 按当前租约生成合同：已知信息自动填好，未知项留空由双方补 */
function previewContract(row: Lease) {
  void openContractPreview(`/ledger/leases/${row.id}/contract`, `房屋租赁合同-${row.tenant_name || ''}.docx`)
}

/** 预览确认后再真正下载（与预览同一份内容） */
function downloadPreviewedContract() {
  if (!contractPath.value) return
  void downloadFile(contractPath.value, contractFile.value)
}

// ---------------- AI 租约体检 ----------------
function runLeaseCheck() {
  if (!detail.value) return
  ui.openAiAnalysis('lease', `AI 租约体检：${detail.value.tenant_name}`, 'current_month', detail.value.id)
}

// ---------------- 续租 ----------------
const renewOpen = ref(false)
const renewSaving = ref(false)
const renewForm = reactive({ start_date: '', end_date: '', monthly_rent: '', deposit_amount: '' })

function openRenew() {
  if (!detail.value) return
  const base = detail.value.end_date ? new Date(detail.value.end_date) : new Date()
  const start = new Date(base)
  start.setDate(start.getDate() + 1)
  const end = new Date(start)
  end.setFullYear(end.getFullYear() + 1)
  end.setDate(end.getDate() - 1)
  Object.assign(renewForm, {
    start_date: start.toISOString().slice(0, 10),
    end_date: end.toISOString().slice(0, 10),
    monthly_rent: detail.value.monthly_rent,
    deposit_amount: detail.value.deposit_amount,
  })
  renewOpen.value = true
}

async function submitRenew() {
  if (!detail.value) return
  renewSaving.value = true
  try {
    const created = await api.post<Record<string, any>>(`/ledger/leases/${detail.value.id}/renew`, {
      start_date: renewForm.start_date || null,
      end_date: renewForm.end_date || null,
      monthly_rent: renewForm.monthly_rent || null,
      deposit_amount: renewForm.deposit_amount || null,
    })
    renewOpen.value = false
    toast.success('已生成续租合同')
    await load()
    if (created?.id) await openDetail(created as Lease)
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    renewSaving.value = false
  }
}

// ---------------- 退租结算 ----------------
const settleOpen = ref(false)
const settleSaving = ref(false)
const settleForm = reactive({
  settle_date: '',
  refund_account_id: '',
  note: '',
  utilities: [] as { label: string; amount: string }[],
  damages: [] as { label: string; amount: string; note: string }[],
})
const settlementUtilityOptions = ['水费', '电费', '燃气费', '物业费', '其他']

function openSettle() {
  if (!detail.value) return
  settleForm.settle_date = new Date().toISOString().slice(0, 10)
  settleForm.refund_account_id = ''
  settleForm.note = ''
  settleForm.utilities = [{ label: '水费', amount: '' }]
  settleForm.damages = []
  settleOpen.value = true
}

const settleDeduction = computed(() => {
  const sum = (rows: { amount: string }[]) =>
    rows.reduce((total, row) => total + (Number(row.amount) || 0), 0)
  return sum(settleForm.utilities) + sum(settleForm.damages)
})
const settleDeposit = computed(() => Number(detail.value?.deposit_summary?.held || 0))
const settleRefund = computed(() => Math.max(0, settleDeposit.value - settleDeduction.value))
// 押金登记过到账（钱在账户里）才需要选退款账户
const needsRefundAccount = computed(
  () => detail.value?.deposit_summary?.source === 'record' && settleRefund.value > 0,
)

async function submitSettle() {
  if (!detail.value) return
  settleSaving.value = true
  try {
    const items = [
      ...settleForm.utilities
        .filter((row) => (Number(row.amount) || 0) > 0)
        .map((row) => ({ item_type: 'utility', label: row.label, amount: row.amount })),
      ...settleForm.damages
        .filter((row) => row.label.trim() && (Number(row.amount) || 0) > 0)
        .map((row) => ({ item_type: 'damage', label: row.label.trim(), amount: row.amount, note: row.note })),
    ]
    await api.post(`/ledger/leases/${detail.value.id}/settle`, {
      settle_date: settleForm.settle_date || null,
      refund_account_id: settleForm.refund_account_id || null,
      note: settleForm.note || null,
      items,
    })
    settleOpen.value = false
    toast.success('退租结算完成，租约已终止')
    await load()
    await loadDetail(detail.value.id)
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    settleSaving.value = false
  }
}

// ---------------- 租客联系方式 ----------------
const contactRevealed = ref(false)
async function revealContact() {
  if (!detail.value) return
  try {
    const res = await api.get<Record<string, any>>(`/ledger/leases/${detail.value.id}?reveal=true`)
    detail.value = { ...detail.value, tenant_contact: res.tenant_contact }
    contactRevealed.value = true
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function terminate(row: Lease) {
  const { ok, reason } = await ui.askConfirm({
    title: '终止租约',
    message: `确定终止「${row.lease_no}」吗？房产将转为空置状态，历史应收与押金保留。`,
    confirmText: '确认终止',
    danger: true,
    requireReason: true,
    reasonLabel: '终止原因',
  })
  if (!ok || !reason) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'lease.terminate', object_type: 'lease', object_id: row.id,
    })
    await api.post(`/ledger/leases/${row.id}/terminate`, { reason, confirm_token: tokenRes.token })
    toast.success('租约已终止')
    await load()
    if (detail.value?.id === row.id) await loadDetail(row.id)
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

/** 租约状态 → 角标配色 */
function leaseTone(status: string) {
  return ({ active: 'success', draft: 'warning', expired: 'neutral', terminated: 'neutral' } as Record<string, string>)[status] || 'neutral'
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  return String(s).slice(0, 10)
}
</script>

<template>
  <div class="leases">
    <PageHeader title="租赁" subtitle="租金、押金与逾期统一在此管理；应收不改变账户余额，实收计入收入，押金不计收支。">
      <AppButton variant="secondary" @click="previewContractTemplate">
        <FileText :size="15" /> 合同模板
      </AppButton>
      <AppButton variant="primary" v-if="canCreate" @click="openCreate">
        <Plus :size="15" /> 新建租约
      </AppButton>
    </PageHeader>

    <AppToolbar>
      <input v-model="keyword" class="search-input" placeholder="搜索租客 / 房产 / 编号" @keyup.enter="load" />
      <AppSelect v-model="statusFilter" :options="[
        { value: '', label: '全部状态' },
        { value: 'active', label: '生效中' }, { value: 'draft', label: '草稿' },
        { value: 'expired', label: '已到期' }, { value: 'terminated', label: '已终止' },
      ]" class="sel" @update:model-value="load" />
      <AppSelect v-model="categoryFilter" :options="categoryOptions" class="sel" @update:model-value="load" />
      <AppButton variant="secondary" @click="load">刷新</AppButton>
    </AppToolbar>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else>
      <AppCard>
        <div v-if="rows.length" class="card-grid">
          <article
            v-for="row in rows"
            :key="row.id"
            class="entity-card"
            :class="{ 'is-muted': row.status !== 'active' }"
            role="button"
            tabindex="0"
            @click="openDetail(row)"
            @keyup.enter="openDetail(row)"
          >
            <span class="entity-corner" :class="`tone-${leaseTone(row.status)}`">{{ row.status_label }}</span>

            <header class="entity-head">
              <span class="entity-icon tone-primary"><Users :size="18" /></span>
              <div class="entity-title">
                <h3>{{ row.tenant_name }}</h3>
                <p>{{ row.real_estate_name || '未关联房产' }} · {{ row.lease_no }}</p>
              </div>
            </header>

            <div class="entity-value">
              <p class="k">月租（{{ row.payment_frequency_label }}）</p>
              <p class="v">
                <MoneyText :value="row.monthly_rent" :currency="row.currency" show-currency />
              </p>
            </div>

            <div v-if="row.end_date" class="entity-note" :class="{ warn: row.status === 'expired' }">
              <CalendarClock :size="13" />
              <span>{{ row.status === 'expired' ? '已到期' : '合同到期' }}</span>
              <strong>{{ fmtDate(row.end_date) }}</strong>
            </div>

            <div class="entity-note" :class="{ warn: !row.next_receipt_date }">
              <CalendarClock :size="13" />
              <span>下一期收租</span>
              <strong>{{ row.next_receipt_date ? fmtDate(row.next_receipt_date) : '未生成收租计划' }}</strong>
            </div>

            <div class="entity-meta">
              <span v-if="row.category_label">{{ row.category_label }}</span>
              <span v-if="row.deposit_amount">
                押金 <b>{{ formatAmount(row.deposit_amount, ui.amountPrecision) }}</b>
                <i class="dep-flag" :class="{ ok: row.deposit_summary?.received }">
                  {{ row.deposit_summary?.received ? '已收' : '未收' }}
                </i>
              </span>
              <span v-if="row.tenant_contact">{{ row.tenant_contact }}</span>
            </div>

            <footer class="entity-actions" @click.stop>
              <button class="act" type="button" @click="openDetail(row)">详情</button>
              <button class="act" type="button" @click="previewContract(row)">合同</button>
              <button v-if="canEdit" class="act" type="button" @click="openEdit(row)">编辑</button>
              <button v-if="canArchive && row.status === 'active'" class="act danger" type="button" @click="terminate(row)">终止</button>
            </footer>
          </article>
        </div>
        <StateBlock v-else state="empty" title="暂无租约" description="新建租约后，可登记租金应收、实收与押金。">
          <template #actions>
            <AppButton v-if="canCreate" variant="primary" @click="openCreate">新建租约</AppButton>
          </template>
        </StateBlock>
      </AppCard>
    </template>

    <!-- 新建/编辑租约 -->
    <AppDrawer :open="editorOpen" :title="editingId ? '编辑租约' : '新建租约'" :width="560" @close="editorOpen = false">
      <div class="form">
        <CategoryPicker v-model="form.category_id" domain="lease" :error="fieldErrors.category_id"
                        hint="先选分类（可点「＋」新增，子分类直接可选），再填写租约详情" />
        <AppField label="关联房产" required :error="fieldErrors.real_estate_id">
          <AppSelect v-model="form.real_estate_id" :options="realEstateOptions" :disabled="!!editingId" />
        </AppField>
        <AppField label="月租" required :error="fieldErrors.monthly_rent"
                  hint="租约的核心经济信息，先填这里">
          <AppInput v-model="form.monthly_rent" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="租客名称" required :error="fieldErrors.tenant_name">
          <AppInput v-model="form.tenant_name" placeholder="例如：张三" />
        </AppField>
        <AppField label="租客联系方式">
          <AppInput v-model="form.tenant_contact" placeholder="手机号（敏感，默认掩码展示）" />
        </AppField>
        <AppField label="租赁用途">
          <AppInput v-model="form.lease_purpose" placeholder="例如：居住 / 办公" />
        </AppField>
        <AppField label="付款周期" :error="fieldErrors.payment_frequency">
          <AppSelect v-model="form.payment_frequency" :options="['monthly', 'quarterly', 'half_yearly', 'yearly'].map((v) => ({ value: v, label: zh('payment_frequency', v) }))" />
        </AppField>
        <AppField v-if="form.currency !== 'CNY'" label="币种" hint="默认人民币；仅外币租约需要修改">
          <AppSelect v-model="form.currency" :options="[{ value: 'CNY', label: 'CNY' }, { value: 'USD', label: 'USD' }, { value: 'EUR', label: 'EUR' }, { value: 'JPY', label: 'JPY' }]" />
        </AppField>
        <AppField v-else label="币种">
          <div class="ccy-row">
            <span>人民币 CNY</span>
            <button class="ccy-link" type="button" @click="form.currency = 'USD'">记外币</button>
          </div>
        </AppField>
        <AppField label="租赁期间（起租日 / 合同到期日）">
          <AppDateRangePicker
            v-model:start="form.start_date" v-model:end="form.end_date"
            placeholder="选择起租日与合同到期日"
          />
        </AppField>
        <AppField label="押金金额">
          <AppInput v-model="form.deposit_amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="收款账户">
          <AppSelect v-model="form.receiving_account_id" :options="accountOptions" />
        </AppField>
        <AppField label="物业/维修承担方">
          <AppSelect v-model="form.property_repair_bearer" :options="[{ value: '', label: '未设置' }, ...['landlord', 'tenant', 'both'].map((v) => ({ value: v, label: zh('repair_bearer', v) }))]" />
        </AppField>
        <AppField label="到期提醒（提前 N 天）">
          <AppInput v-model="form.reminder_days" type="number" placeholder="例如 30" />
        </AppField>
        <AppField label="押金是否已收">
          <AppSwitch v-model="form.deposit_received" label="押金已收到" />
        </AppField>
        <AppField
          label="押金是否计入净资产"
          hint="仅在已收押金时可选；已通过「登记押金」入账的不会再重复计入"
        >
          <AppSwitch
            v-model="form.deposit_include_in_net_worth"
            :disabled="!form.deposit_received"
            label="押金计入净资产"
          />
        </AppField>
        <AppField label="备注">
          <AppInput v-model="form.note" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">保存租约</AppButton>
      </template>
    </AppDrawer>

    <!-- 租约详情 -->
    <AppDrawer :open="detailOpen" title="租约详情" :width="600" @close="detailOpen = false">
      <StateBlock v-if="!detail" state="loading" />
      <template v-else>
        <div class="seg2">
          <button v-for="t in tabs" :key="t.key" :class="{ on: activeTab === t.key }" type="button" @click="activeTab = t.key">{{ t.label }}</button>
        </div>

        <div v-if="activeTab === 'overview'" class="pane">
          <ul class="kv">
            <li><span class="k">租约编号</span><span class="v">{{ detail.lease_no }}</span></li>
            <li><span class="k">房产</span><span class="v">{{ detail.real_estate_name || '—' }}</span></li>
            <li><span class="k">租客</span><span class="v strong">{{ detail.tenant_name }}</span></li>
            <li><span class="k">月租 / 周期</span><span class="v">{{ detail.monthly_rent }} {{ detail.currency }} · {{ detail.payment_frequency_label }}</span></li>
            <li><span class="k">租期</span><span class="v">{{ fmtDate(detail.start_date) }} ~ {{ fmtDate(detail.end_date) }}</span></li>
            <li><span class="k">状态</span><span class="v"><StatusBadge :label="detail.status_label" :tone="detail.status === 'active' ? 'success' : 'neutral'" /></span></li>
            <li>
              <span class="k">联系方式</span>
              <span class="v">
                {{ detail.tenant_contact || '—' }}
                <button
                  v-if="detail.tenant_contact && !contactRevealed"
                  class="link-btn" type="button" @click="revealContact"
                >查看</button>
              </span>
            </li>
            <li><span class="k">未收应收</span><span class="v">{{ detail.receivable_summary.total_pending }} {{ detail.currency }}</span></li>
            <li>
              <span class="k">押金</span>
              <span class="v">
                {{ detail.deposit_amount }} {{ detail.currency }} ·
                <template v-if="detail.deposit_received">
                  <template v-if="detail.deposit_summary.source === 'record'">已收（已入账）</template>
                  <template v-else>已收（未登记到账）</template>
                </template>
                <template v-else>未收</template>
              </span>
            </li>
            <li><span class="k">待退还押金</span><span class="v">{{ detail.deposit_summary.held }} {{ detail.currency }}</span></li>
          </ul>
          <p class="hint">应收不改变账户余额；实收确认后计入收入；押金不计收支。</p>

          <div class="util-block">
            <p class="util-title">水电煤户号</p>
            <div v-if="detail.utilities && detail.utilities.length" class="util-chips">
              <span v-for="u in detail.utilities" :key="u.id" class="util-chip">
                {{ u.utility_type_label }} {{ u.account_no }}
              </span>
            </div>
            <p v-else class="hint">
              该房产还没有登记水电煤户号，可在不动产详情的「水电煤户号」里添加。
            </p>
          </div>
        </div>

        <div v-else-if="activeTab === 'receivables'" class="pane">
          <div class="pane-head">
            <span>应收 {{ detail.receivable_summary.pending }} 笔待收</span>
            <AppButton v-if="canCreate" variant="secondary" @click="openReceivable">登记应收</AppButton>
          </div>
          <StateBlock v-if="!detail.receivables.length" state="empty" title="暂无应收" description="登记租金应收后按应付日跟踪。" />
          <ul v-else class="list">
            <li v-for="r in detail.receivables" :key="r.id">
              <div class="l-main">
                <strong>{{ r.amount }} {{ r.currency }}</strong>
                <small>应付 {{ fmtDate(r.due_date) }}</small>
              </div>
              <StatusBadge :label="r.overdue ? '逾期' : r.status_label" :tone="r.overdue ? 'danger' : (r.status === 'paid' ? 'success' : 'neutral')" />
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'events'" class="pane">
          <div class="pane-head">
            <span>实收（租金到账）</span>
            <AppButton v-if="canConfirm" variant="secondary" @click="openReceipt">登记实收</AppButton>
          </div>
          <StateBlock
            v-if="!detail.events.length"
            state="empty" title="暂无实收" description="登记实收后计入收入并显示在这里；押金收取与退还在「押金」页签。"
          />
          <ul v-else class="list">
            <li v-for="e in (detail.events as any[])" :key="e.id">
              <div class="l-main">
                <strong>{{ e.title }}</strong>
                <small>{{ e.event_type_label }} · {{ fmtDate(e.occurred_at) }} · {{ e.status_label }}</small>
              </div>
              <span class="amt">{{ e.amount }} {{ e.currency }}</span>
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'deposits'" class="pane">
          <div class="pane-head">
            <span>押金（待退还 {{ detail.deposit_summary.held }}）</span>
            <AppButton
              v-if="canConfirm && !detail.deposits.length"
              variant="secondary" @click="openDepositReceive"
            >收取押金</AppButton>
          </div>
          <StateBlock
            v-if="!detail.deposits.length && !detail.deposit_received"
            state="empty" title="暂无押金记录"
            description="押金单独显示为待退还资金，不计入收入或支出。"
          />
          <div v-else-if="!detail.deposits.length" class="deposit-flag">
            <div class="l-main">
              <strong>{{ detail.deposit_amount }} {{ detail.currency }} · 已标记收讫</strong>
              <small>只记录了「已收」，还没有登记到账账户；补登记后押金会进入账户余额。</small>
            </div>
            <AppButton
              v-if="canConfirm && !detail.deposits.length"
              variant="secondary" @click="openDepositReceive"
            >登记到账</AppButton>
          </div>
          <ul v-else class="list">
            <li v-for="d in detail.deposits" :key="d.id">
              <div class="l-main">
                <strong>{{ d.amount }} {{ d.currency }}</strong>
                <small>收取于 {{ fmtDate(d.received_at) }}</small>
              </div>
              <div class="acts">
                <StatusBadge :label="d.status_label" :tone="d.status === 'held' ? 'warning' : 'success'" />
                <button v-if="canConfirm && d.status === 'held'" class="act" type="button" @click="returnDeposit(d)">退还</button>
              </div>
            </li>
          </ul>
        </div>

        <div v-else class="pane ai">
          <h4><Sparkles :size="14" /> 租约体检</h4>
          <ul>
            <li>检查关键信息是否缺失：租客联系方式、租期、押金、收款账户</li>
            <li>检查条款是否矛盾：租金为 0、租期顺序、押金低于月租、到期未处理</li>
            <li>检查落地情况：收租计划、押金是否入账、水电煤户号</li>
          </ul>
          <AppButton variant="primary" @click="runLeaseCheck">开始体检</AppButton>
          <h4>AI 不能做什么</h4>
          <ul class="forbidden"><li>不自动催收</li><li>不混淆押金收入</li><li>不自动确认应收</li></ul>
          <AppButton variant="secondary" @click="ui.openAiPanel(`租赁：${detail.tenant_name}`)">
            <Sparkles :size="15" /> 打开 AI 分析
          </AppButton>
        </div>

        <div class="doc-actions">
          <AppButton variant="secondary" @click="previewContract(detail)">
            <FileText :size="15" /> 查看 / 下载租赁合同
          </AppButton>
          <AppButton v-if="detail.status === 'active'" variant="secondary" @click="openRenew">
            续租
          </AppButton>
          <AppButton v-if="detail.status === 'active'" variant="secondary" @click="openSettle">
            退租结算
          </AppButton>
          <span class="doc-hint">已按本租约的租客、租金、租期预填；押金、账户等空白处可签署时手写。</span>
        </div>
      </template>
    </AppDrawer>

    <!-- 登记应收 -->
    <AppModal :open="receivableOpen" title="登记租金应收" confirm-text="登记应收" :confirm-loading="receivableSaving" @close="receivableOpen = false" @confirm="saveReceivable">
      <div class="form">
        <AppField label="应收金额" required :error="receivableErrors.amount">
          <AppInput v-model="receivableForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="应付日" :error="receivableErrors.due_date" hint="早于今日将标记为逾期">
          <AppDatePicker v-model="receivableForm.due_date" placeholder="选择应付日" />
        </AppField>
        <AppField label="账期">
          <AppDateRangePicker
            v-model:start="receivableForm.period_start" v-model:end="receivableForm.period_end"
            placeholder="选择账期"
          />
        </AppField>
        <AppField label="备注"><AppInput v-model="receivableForm.note" /></AppField>
        <p class="hint">应收登记不会改变账户余额；实收确认后才计入收入。</p>
      </div>
    </AppModal>

    <!-- 登记实收 -->
    <AppModal :open="receiptOpen" title="登记租金实收" confirm-text="确认实收" :confirm-loading="receiptSaving" @close="receiptOpen = false" @confirm="saveReceipt">
      <div class="form">
        <AppField label="到账日期">
          <AppDatePicker v-model="receiptForm.occurred_at" placeholder="选择到账日期" />
        </AppField>

        <div v-if="receiptPreview" class="calc">
          <div class="calc-line">
            <span>本期金额</span>
            <strong>{{ receiptPreview.amount }} {{ receiptPreview.currency }}</strong>
            <em>{{ receiptPreview.payment_frequency_label }} × 月租{{ detail ? ` ${detail.monthly_rent}` : '' }}</em>
          </div>
          <div class="calc-line">
            <span>下次收租日</span>
            <strong v-if="receiptPreview.next_due_date">{{ fmtDate(receiptPreview.next_due_date) }}</strong>
            <em v-else class="muted">一次性租约，没有下一期</em>
          </div>
          <div v-if="receiptPreview.receivable_amount" class="calc-line">
            <span>匹配应收</span>
            <strong>{{ receiptPreview.receivable_amount }} {{ receiptPreview.currency }}</strong>
            <em>应付 {{ fmtDate(receiptPreview.due_date) }}</em>
          </div>
        </div>
        <p v-else-if="receiptPreviewLoading" class="calc-loading">正在按付款周期计算…</p>

        <AppField label="实收金额" required :error="receiptErrors.amount" hint="已按付款周期自动带出，可按实际到账修改">
          <AppInput v-model="receiptForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="到账账户" required :error="receiptErrors.account_id">
          <AppSelect v-model="receiptForm.account_id" :options="accountOptions" />
        </AppField>
        <AppField label="核销应收（可选）" hint="留空会自动核销最早一笔待收应收">
          <AppSelect v-model="receiptForm.receivable_id" :options="[{ value: '', label: '不指定（自动核销最早一笔）' }, ...(detail?.receivables || []).filter((r) => r.status === 'pending').map((r) => ({ value: r.id, label: `${r.amount} ${r.currency}（${fmtDate(r.due_date)}）` }))]" />
        </AppField>
        <AppField label="备注"><AppInput v-model="receiptForm.note" /></AppField>
        <p class="hint">实收确认后计入收入并生成账户资金变动；本期结清后按周期自动生成下一期应收。</p>
      </div>
    </AppModal>

    <!-- 收取押金 -->
    <AppModal :open="depositOpen" title="收取押金" confirm-text="确认收取" :confirm-loading="depositSaving" @close="depositOpen = false" @confirm="saveDeposit">
      <div class="form">
        <AppField label="押金金额" required :error="depositErrors.amount">
          <AppInput v-model="depositForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="到账账户" required :error="depositErrors.account_id">
          <AppSelect v-model="depositForm.account_id" :options="accountOptions" />
        </AppField>
        <AppField label="日期"><AppDatePicker v-model="depositForm.occurred_at" placeholder="选择收取日期" /></AppField>
        <AppField label="备注"><AppInput v-model="depositForm.note" /></AppField>
        <p class="hint">每份租约的押金只能收取一次；押金单独显示为待退还资金，不计入收入。</p>
      </div>
    </AppModal>
  </div>
  <AppModal
    :open="contractOpen"
    :title="contractTitle || '合同预览'"
    size="lg"
    @close="contractOpen = false"
  >
    <p v-if="contractLoading" class="contract-loading">正在生成合同…</p>
    <div v-else class="contract-preview">
      <p
        v-for="(p, i) in contractParagraphs"
        :key="i"
        :class="['cp', { 'cp-h': p.heading, 'cp-blank': !p.text }]"
      >{{ p.text || ' ' }}</p>
    </div>
    <template #footer>
      <AppButton variant="secondary" @click="contractOpen = false">关闭</AppButton>
      <AppButton variant="primary" @click="downloadPreviewedContract">下载 Word</AppButton>
    </template>
  </AppModal>

  <!-- 续租 -->
  <AppModal
    :open="renewOpen" title="续租" confirm-text="生成续租合同"
    :confirm-loading="renewSaving"
    @close="renewOpen = false" @confirm="submitRenew"
  >
    <div class="form">
      <AppField label="新起租日" required>
        <AppDatePicker v-model="renewForm.start_date" placeholder="选择起租日" />
      </AppField>
      <AppField label="新到期日">
        <AppDatePicker v-model="renewForm.end_date" placeholder="选择到期日" />
      </AppField>
      <AppField label="月租">
        <AppInput v-model="renewForm.monthly_rent" type="number" placeholder="0.00" />
      </AppField>
      <AppField label="押金">
        <AppInput v-model="renewForm.deposit_amount" type="number" placeholder="0.00" />
      </AppField>
    </div>
    <p class="hint">原租约转为「已到期」，租客、联系方式与押金状态沿用；水电煤户号仍跟着房产。</p>
  </AppModal>

  <!-- 退租结算 -->
  <AppModal
    :open="settleOpen" title="退租结算" confirm-text="确认结算并退租"
    :confirm-loading="settleSaving"
    @close="settleOpen = false" @confirm="submitSettle"
  >
    <div class="settle-sum">
      <span>押金 <strong>{{ settleDeposit.toFixed(2) }}</strong></span>
      <span>扣除 <strong>{{ settleDeduction.toFixed(2) }}</strong></span>
      <span class="ok">应退 <strong>{{ settleRefund.toFixed(2) }}</strong></span>
    </div>

    <h4 class="sec">水电煤结算</h4>
    <div v-for="(row, i) in settleForm.utilities" :key="`u${i}`" class="settle-row">
      <AppSelect
        v-model="row.label"
        :options="settlementUtilityOptions.map((v) => ({ value: v, label: v }))"
      />
      <AppInput v-model="row.amount" type="number" placeholder="金额" />
      <button class="act" type="button" @click="settleForm.utilities.splice(i, 1)">删除</button>
    </div>
    <AppButton variant="secondary" @click="settleForm.utilities.push({ label: '电费', amount: '' })">
      + 水电煤一项
    </AppButton>

    <h4 class="sec">家电家具破损赔偿</h4>
    <div v-for="(row, i) in settleForm.damages" :key="`d${i}`" class="settle-row damage">
      <AppInput v-model="row.label" placeholder="项目（如沙发破损）" />
      <AppInput v-model="row.amount" type="number" placeholder="金额" />
      <AppInput v-model="row.note" placeholder="说明" />
      <button class="act" type="button" @click="settleForm.damages.splice(i, 1)">删除</button>
    </div>
    <AppButton variant="secondary" @click="settleForm.damages.push({ label: '', amount: '', note: '' })">
      + 赔偿一项
    </AppButton>

    <div class="form">
      <AppField v-if="needsRefundAccount" label="退款账户" required>
        <AppSelect v-model="settleForm.refund_account_id" :options="accountOptions" />
      </AppField>
      <AppField label="结算日期">
        <AppDatePicker v-model="settleForm.settle_date" placeholder="选择结算日期" />
      </AppField>
      <AppField label="备注">
        <AppInput v-model="settleForm.note" placeholder="如：墙面污损已确认" />
      </AppField>
    </div>
    <p class="hint">
      确认后租约终止、房产转为空置；押金已入账的按应退金额记一笔退款流水。
      水电煤与赔偿只从押金里扣，不额外记支出（真要对外缴款时另记一笔缴费）。
    </p>
  </AppModal>
</template>

<style scoped>
.link-btn {
  margin-left: 6px; padding: 0; border: none; background: none;
  color: var(--primary); font-size: 12px; cursor: pointer;
}
.calc {
  display: flex; flex-direction: column; gap: 6px;
  padding: 10px 12px; border-radius: 10px;
  border: 1px dashed var(--border); background: var(--glass-1);
}
.calc-line { display: flex; align-items: baseline; gap: 8px; font-size: 13px; }
.calc-line span { color: var(--ink-muted); font-size: 12px; min-width: 62px; }
.calc-line strong { color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.calc-line em { font-style: normal; font-size: 12px; color: var(--ink-muted); }
.calc-line em.muted { color: var(--ink-muted); }
.calc-loading { margin: 0; font-size: 12px; color: var(--ink-muted); }
.deposit-flag {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 12px 14px; border-radius: 12px;
  border: 1px dashed var(--border); background: var(--glass-1);
}
.settle-sum {
  display: flex; gap: 18px; flex-wrap: wrap;
  padding: 10px 14px; border-radius: 12px;
  background: var(--glass-1); border: 1px solid var(--border);
  font-size: 13px; color: var(--ink-muted);
}
.settle-sum strong { color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.settle-sum .ok strong { color: var(--success, #19875a); }
.settle-row {
  display: grid; grid-template-columns: 1fr 1fr auto; gap: 8px; align-items: center;
  margin-bottom: 8px;
}
.settle-row.damage { grid-template-columns: 1.4fr 1fr 1.4fr auto; }
.sec { margin: 14px 0 8px; font-size: 13px; color: var(--ink-strong); }
.util-block { margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--border); }
.util-title { margin: 0 0 8px; font-size: 12.5px; color: var(--ink-muted); }
.util-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.util-chip {
  padding: 3px 9px; border-radius: 999px; font-size: 12px;
  border: 1px solid var(--border); background: var(--glass-2); color: var(--ink);
}
.contract-loading { padding: 40px; text-align: center; color: var(--ink-muted); }
.contract-preview { max-height: 58vh; overflow: auto; padding: 4px 6px 4px 2px; }
.contract-preview .cp { margin: 0 0 5px; font-size: 13px; line-height: 1.85; color: var(--ink); }
.contract-preview .cp-h { font-weight: 600; color: var(--ink-strong); margin-top: 12px; font-size: 13.5px; }
.contract-preview .cp-blank { height: 6px; margin: 0; }
.dep-flag {
  margin-left: 4px; font-style: normal; font-size: 11.5px; color: var(--ink-muted);
}
.dep-flag.ok { color: var(--success, #19875a); }

.doc-actions {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-top: var(--space-4); padding-top: var(--space-3);
  border-top: 1px dashed var(--border);
}
.doc-hint { font-size: 11.5px; line-height: 1.5; color: var(--ink-muted); flex: 1 1 220px; }
.ccy-row { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--ink-strong); }
.ccy-link {
  border: none; background: transparent; padding: 0; cursor: pointer;
  font-size: 12.5px; color: var(--primary); text-decoration: underline;
}
.leases { display: flex; flex-direction: column; gap: var(--space-4); }
.search-input {
  height: 34px; padding: 0 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1); color: var(--ink-strong); font-size: 13.5px; min-width: 200px; flex: 1;
}
.search-input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(53, 109, 255, 0.16); }
.seg, .seg2 { display: inline-flex; flex-wrap: wrap; max-width: 100%; gap: 2px; padding: 2px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.seg button, .seg2 button { height: 30px; padding: 0 14px; border: none; border-radius: 9px; background: transparent; color: var(--ink-muted); font-size: 13px; cursor: pointer; }
.seg button.on, .seg2 button.on { color: var(--ink-strong); background: var(--glass-3); font-weight: 600; }
.sel { width: 140px; }
.acts { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act { display: inline-flex; align-items: center; height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer; }
.act:hover { border-color: var(--primary); color: var(--primary); }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.pane { display: flex; flex-direction: column; gap: var(--space-3); }
.pane-head { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--ink-muted); }
.kv { display: flex; flex-direction: column; }
.kv li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 9px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.kv .k { color: var(--ink-muted); }
.kv .v { color: var(--ink-strong); }
.list { display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.l-main { display: flex; flex-direction: column; gap: 2px; }
.l-main strong { font-size: 13.5px; color: var(--ink-strong); }
.l-main small { font-size: 12px; color: var(--ink-muted); }
.amt { font-size: 13px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.hint { font-size: 12px; color: var(--ink-muted); }
.ai h4 { display: flex; align-items: center; gap: 6px; font-size: 13.5px; color: var(--ink-strong); }
.ai ul { padding-left: 18px; font-size: 12.5px; color: var(--ink-muted); }
.ai ul.forbidden { color: var(--warning); }
</style>
