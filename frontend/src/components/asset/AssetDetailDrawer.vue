<script setup lang="ts">
/**
 * 资产详情抽屉（06 §2、15 §3.5/§4.3/§5.5/§6.3）。
 * - 页签由资产类型决定；类型专属页签由各页面通过插槽渲染，其余页签本组件内置
 * - 估值、处置、归档、保险、水电煤户号操作在本组件内完成，操作成功后通知父级刷新列表
 * - 金额与统计口径全部来自后端；前端不自行计算
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Archive, Building2, Coins, Plus, Sparkles, Trash2, TrendingUp } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { formatAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppDateRangePicker from '@/components/ui/AppDateRangePicker.vue'
import AssetAttachments from '@/components/asset/AssetAttachments.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppModal from '@/components/ui/AppModal.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import {
  fmtDate, fmtDateTime, statusTone, type AccountLite, type AssetDetail,
} from '@/views/ledger/asset-shared'

const props = defineProps<{
  open: boolean
  assetId: string
  assetType: string
  tabs: { key: string; label: string }[]
  accounts: AccountLite[]
}>()
const emit = defineEmits<{ close: []; changed: [] }>()

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()
const router = useRouter()

const detail = ref<AssetDetail | null>(null)
const loading = ref(false)
const error = ref('')
const activeTab = ref('overview')

/** 不动产（含车位）：不展示价值来源/折旧方式/维护日期 */
const isProperty = computed(() =>
  ['real_estate', 'parking'].includes(String(detail.value?.asset_type || '')),
)
/** 借出款（应收）：没有估值/折旧/维护的概念，处置也不适用 */
const isLend = computed(() => Boolean(detail.value?.related?.receivable))
const canEdit = computed(() => auth.can('edit_draft'))
const canDispose = computed(() => auth.can('confirm'))
const canArchive = computed(() => auth.can('archive'))
const canCreateDraft = computed(() => auth.can('create_draft'))
const canConfirm = computed(() => auth.can('confirm'))

let loadSeq = 0
async function load() {
  if (!props.assetId) return
  // 快速切换资产时旧请求可能后到：用代次丢弃过期响应，避免展示 A 却按 B 提交
  const seq = ++loadSeq
  const assetId = props.assetId
  loading.value = true
  error.value = ''
  detail.value = null
  try {
    const data = await api.get<AssetDetail>(`/ledger/assets/${assetId}`)
    if (seq !== loadSeq) return
    detail.value = data
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

watch(
  () => [props.open, props.assetId],
  ([open]) => {
    if (open) {
      activeTab.value = props.tabs[0]?.key || 'overview'
      load()
    }
  },
)
onMounted(() => {
  if (props.open) load()
})

const ext = computed<Record<string, any>>(() => (detail.value?.extension || {}) as Record<string, any>)

interface ReceivableRepaymentRow {
  id: string
  amount: string
  /** 一次回款可含利息：本金冲减应收，利息计入收入 */
  principal_part: string
  interest_part: string
  /** 可选的期次标注（历史数据可能手填过） */
  period_no: number | null
  /** 自动算出的覆盖期次，如 "18" / "18-19" */
  period_range: string | null
  /** false = 补录的历史回款，不影响当前账户 */
  affects_account: boolean
  account_name: string
  received_at: string | null
  note: string | null
}

interface ReceivableDetail {
  id: string
  borrower_name: string
  borrower_id_no: string | null
  lend_type_label: string
  /** 从账户出借 / 补录既有借出 */
  lent_mode: string
  lent_mode_label: string
  principal: string
  repaid_total: string
  outstanding: string
  lent_at: string | null
  due_date: string | null
  repayment_method_label: string
  interest_enabled: boolean
  interest_rate: string | null
  interest_received: string
  fee_bearer_label: string
  fee_amount: string | null
  note: string | null
  settled: boolean
  reconcile: ReceivableReconcile
  /** 分期计划（金额每期可不同）；为空表示只按总额对账 */
  periods: ReceivablePeriodRow[]
  repayments: ReceivableRepaymentRow[]
}

interface ReceivablePeriodRow {
  id: string
  period_no: number
  due_date: string | null
  amount: string
  paid_amount: string
  unpaid: string
  status: 'paid' | 'partial' | 'pending' | 'overdue'
  status_label: string
  note: string | null
}

/** 对账块：金额不固定的分期只比「总额 + 期限」 */
interface ReceivableReconcile {
  principal: string
  paid: string
  remaining: string
  paid_percent: number
  time_percent: number | null
  days_total: number | null
  days_elapsed?: number
  days_left: number | null
  overdue_days: number
  gap_to_catch_up: string
  daily_needed: string | null
  projected_settle_date: string | null
  /** 按当前节奏会拖过约定还清日 */
  will_be_late?: boolean
  due_date?: string
  repayment_count: number
  has_due_date: boolean
  /** 是否登记了「何时借出 + 何时该还完」，没有就不比时间进度 */
  has_schedule_window: boolean
  status: 'settled' | 'no_window' | 'on_track' | 'at_risk' | 'overdue'
  status_label: string
  /** 有分期计划时才有 */
  has_schedule?: boolean
  period_count?: number
  paid_periods?: number
  unpaid_periods?: number
  overdue_periods?: number
}

const rec = computed<ReceivableDetail | null>(() =>
  (detail.value?.related?.receivable as ReceivableDetail | undefined) || null,
)

/** 非借出款的其他资产不显示「借出与回款」页签 */
const visibleTabs = computed(() => props.tabs.filter((t) => {
  if (t.key === 'receivable') return Boolean(rec.value)
  // 借出款是应收，没有「估值」概念，估值时间线页签藏掉
  if (t.key === 'valuations' && rec.value) return false
  return true
}))

/** 底部「登记回款」会先把页签切到借出与回款，否则弹窗关了看不到刚登记的那笔 */
function switchToLendTab() {
  activeTab.value = 'receivable'
}

// ---------------- 分期计划 ----------------
const scheduleOpen = ref(false)
const scheduleSaving = ref(false)
const scheduleErrors = ref<Record<string, string>>({})
/**
 * 分段的 key 必须稳定：用数组下标当 key 时，删掉中间一段后 Vue 会复用后面那行的
 * DOM 与 v-model 绑定，输入框里的金额会串到别的段上（明明删错了行）。
 */
interface ScheduleSegment { id: number; count: number | string; amount: string }
let segmentSeq = 0
function newSegment(): ScheduleSegment {
  segmentSeq += 1
  return { id: segmentSeq, count: 1, amount: '' }
}

const scheduleForm = reactive({
  start_month: '',
  segments: [newSegment()] as ScheduleSegment[],
})

function openSchedule() {
  scheduleErrors.value = {}
  const first = rec.value?.periods?.[0]
  scheduleForm.start_month = (first?.due_date || '').slice(0, 7)
    || (rec.value?.lent_at || '').slice(0, 7)
  scheduleForm.segments = [newSegment()]
  scheduleOpen.value = true
}

function addSegment() {
  scheduleForm.segments.push(newSegment())
}

function removeSegment(index: number) {
  if (scheduleForm.segments.length <= 1) return
  scheduleForm.segments.splice(index, 1)
}

/** 实时预览：总期数与合计，避免提交后才发现合计对不上本金 */
const schedulePreview = computed(() => {
  let count = 0
  let total = 0
  for (const seg of scheduleForm.segments) {
    const n = Number(seg.count) || 0
    const a = Number(seg.amount) || 0
    if (n > 0) { count += n; total += n * a }
  }
  return { count, total }
})

async function saveSchedule() {
  scheduleSaving.value = true
  scheduleErrors.value = {}
  try {
    await api.put(`/ledger/assets/receivables/${props.assetId}/periods`, {
      start_month: scheduleForm.start_month,
      segments: scheduleForm.segments.map((s2) => ({ count: Number(s2.count), amount: String(s2.amount) })),
    })
    toast.success('分期计划已保存，回款会按顺序自动分摊到各期')
    scheduleOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    scheduleErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    scheduleSaving.value = false
  }
}

/** 逐期「剩余欠款」：该期之后的累计未还 */
function periodBalanceAfter(index: number): string {
  const list = rec.value?.periods || []
  let rest = 0
  for (let i = index; i < list.length; i += 1) rest += Number(list[i].unpaid || 0)
  return rest.toFixed(2)
}

const repayOpen = ref(false)
const repaySaving = ref(false)
const repayErrors = ref<Record<string, string>>({})
const repayForm = reactive({ amount: '', interest_part: '', period_no: '', account_id: '', occurred_at: '', note: '', affects_account: true })

function openRepay() {
  repayErrors.value = {}
  Object.assign(repayForm, {
    amount: rec.value?.outstanding || '',
    interest_part: '',
    period_no: '',
    account_id: '',
    occurred_at: '',
    note: '',
    affects_account: true,
  })
  repayOpen.value = true
}

async function submitRepay() {
  repaySaving.value = true
  repayErrors.value = {}
  try {
    const body: Record<string, unknown> = {
      amount: repayForm.amount,
      // 历史回款：钱当时收到并花掉了，只冲减应收，不动当前账户
      affects_account: repayForm.affects_account,
      account_id: repayForm.account_id || null,
      occurred_at: repayForm.occurred_at || null,
      note: repayForm.note || null,
    }
    if (rec.value?.interest_enabled && Number(repayForm.interest_part || 0) > 0) {
      body.interest_part = repayForm.interest_part
    }
    await api.post(`/ledger/assets/receivables/${props.assetId}/repayments`, body)
    toast.success(!repayForm.affects_account
      ? '历史回款已补录：只冲减应收，不影响当前账户余额与收支'
      : Number(repayForm.interest_part || 0) > 0
        ? '回款已登记：本金冲减应收（不计收入），利息计入「投资收益 · 利息收入」'
        : '回款已登记：账户余额增加、应收本金减少，不计收入')
    repayOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    repayErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    repaySaving.value = false
  }
}

const EXT_LABELS: Record<string, Record<string, string>> = {
  real_estate: {
    property_type: '房产类型', title_no: '房产证编号', ownership_no: '不动产权证号',
    address: '坐落地址', building: '楼栋', unit: '单元', floor: '楼层', room_no: '房号',
    building_area: '建筑面积（㎡）', inner_area: '套内面积（㎡）', land_area: '土地面积（㎡）',
    orientation: '朝向', layout: '户型', built_year: '建成年份', delivery_date: '交付日期',
    renovation_cost: '装修金额', tax_cost: '税费', agency_fee: '中介费',
    management_fee_price: '物业费单价（元/㎡·月）', unit_price: '参考单价（元/㎡）',
    ownership_ratio: '共有比例', has_mortgage: '是否有房贷', note: '备注',
  },
  parking: {
    parking_type: '车位类型', parking_no: '车位编号', certificate_no: '产权证/使用权编号',
    area_name: '区域/楼栋', area: '面积（㎡）', management_fee: '管理费',
    management_fee_cycle: '管理费周期', linked_real_estate_name: '所属不动产', note: '备注',
  },
  vehicle: {
    brand: '品牌', series: '车系', model: '车型', vin: 'VIN', plate_no: '车牌号',
    engine_no: '发动机号', vehicle_type: '车辆类型', energy_type: '能源类型',
    register_date: '上牌日期', mileage: '里程（公里）', color: '颜色',
    usage_status: '使用状态', parking_space_name: '关联车位', has_loan: '是否有贷款', note: '备注',
  },
  precious_metal: {
    brand: '品牌', purchase_channel: '购买渠道',
    pricing_mode_label: '计价方式', flat_price: '一口价',
    purity: '纯度', weight: '克重', unit: '单位',
    unit_price: '克价', processing_fee: '加工费',
    certificate_no: '证书编号', storage_location: '保管位置', note: '备注',
  },
}

/** 枚举型扩展字段 → 中文字典；漏配会显示成 residence 这类英文标识 */
const EXT_ENUMS: Record<string, string> = {
  property_type: 'property_type',
  parking_type: 'parking_type',
  management_fee_cycle: 'billing_cycle',
  energy_type: 'vehicle_energy_type',
  usage_status: 'vehicle_usage_status',
  category: 'metal_category',
  form: 'metal_form',
  unit: 'metal_unit',
  valuation_source: 'valuation_source',
}

const profileRows = computed(() => {
  const labels = EXT_LABELS[props.assetType] || {}
  return Object.entries(labels)
    .flatMap(([key, label]) => {
      const raw = ext.value[key]
      // 后端会随扩展字段一起下发 xxx_label；本地字典只作兜底
      const backendLabel = ext.value[`${key}_label`]
      const ns = EXT_ENUMS[key]
      const text = typeof backendLabel === 'string' && backendLabel
        ? backendLabel
        : (ns && typeof raw === 'string' && raw ? (zh(ns, raw) || raw) : formatValue(raw))
      // 物业费单价只说明单价，真正要看的是每月多少钱，顺带算出来
      if (key === 'management_fee_price') {
        const price = Number(raw)
        const area = Number(ext.value.building_area)
        const monthly = price > 0 && area > 0 ? price * area : 0
        return [
          { key, label, value: text },
          ...(monthly > 0
            ? [{ key: 'management_fee_monthly', label: '每月物业费（估算）',
                 value: `${monthly.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元` }]
            : []),
        ]
      }
      return [{ key, label, value: text }]
    })
    .filter((row) => row.value !== '—')
})

function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v)) return fmtDate(v)
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isNaN(n) && typeof v !== 'boolean' && String(v).trim() !== '' && /^-?\d+(\.\d+)?$/.test(String(v).trim())) {
    return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
  }
  return String(v)
}

// ---------------- 新增估值 ----------------
const valuationOpen = ref(false)
const valuationSaving = ref(false)
const valuationErrors = ref<Record<string, string>>({})
const valuationForm = reactive({
  value: '', valued_at: '', source: 'manual', source_note: '', reason: '', note: '',
})
/** 「单价 × 面积」自动算总价：房产用建筑面积，车位用面积 */
const valuationUnitPrice = ref('')
const valuationValueTouched = ref(false)
/** 档案里没有面积时，允许临时填一个用于估算（不写回档案） */
const valuationAreaInput = ref('')
const supportsAreaCalc = computed(
  () => props.assetType === 'real_estate' || props.assetType === 'parking',
)
const profileArea = computed(() => {
  const raw = props.assetType === 'real_estate'
    ? ext.value.building_area
    : (props.assetType === 'parking' ? ext.value.area : null)
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? n : 0
})
const valuationArea = computed(() => {
  if (profileArea.value > 0) return profileArea.value
  const typed = Number(valuationAreaInput.value)
  return Number.isFinite(typed) && typed > 0 ? typed : 0
})

function applyUnitPrice() {
  const price = Number(valuationUnitPrice.value)
  if (!(price > 0) || !valuationArea.value) return
  if (valuationValueTouched.value) return
  valuationForm.value = (price * valuationArea.value).toFixed(ui.amountPrecision)
}

watch([valuationUnitPrice, valuationArea], applyUnitPrice)
const sourceOptions = ['manual', 'appraisal', 'market', 'contract', 'other'].map((v) => ({
  value: v, label: zh('valuation_source', v),
}))

function openValuation() {
  valuationErrors.value = {}
  Object.assign(valuationForm, {
    value: detail.value?.current_value || '', valued_at: '', source: 'manual',
    source_note: '', reason: '', note: '',
  })
  valuationValueTouched.value = false
  valuationAreaInput.value = ''
  // 用当前价值反推单价，方便直接改单价看总价
  const currentValue = Number(detail.value?.current_value || 0)
  valuationUnitPrice.value = valuationArea.value && currentValue > 0
    ? (currentValue / valuationArea.value).toFixed(2)
    : ''
  valuationOpen.value = true
}

async function saveValuation() {
  valuationSaving.value = true
  valuationErrors.value = {}
  try {
    await api.post(`/ledger/assets/${props.assetId}/valuations`, {
      value: valuationForm.value,
      valued_at: valuationForm.valued_at || null,
      source: valuationForm.source,
      source_note: valuationForm.source_note || null,
      reason: valuationForm.reason || null,
      note: valuationForm.note || null,
    })
    toast.success('估值已登记，当前价值已更新')
    valuationOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    valuationErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    valuationSaving.value = false
  }
}

// ---------------- 贝壳估价 ----------------
const beikeOpen = ref(false)
const beikeBusy = ref(false)
const beikeApplying = ref(false)
const beikeErrors = ref<Record<string, string>>({})
const beikeResult = ref<Record<string, unknown> | null>(null)
const beikeQuota = ref('')
const beikeCachedAt = ref(0)
const beikeForm = reactive({ city: '', city_code: '', address: '', area: '' })

/**
 * 估价结果本地缓存：贝壳按次扣额度，同一个地址 + 面积 7 天内直接复用，
 * 避免反复点「查询估价」把额度烧光。改了地址或面积自动失效。
 */
const BEIKE_CACHE_KEY = 'fam.beike.cache'
const BEIKE_TTL = 7 * 24 * 3600 * 1000

function beikeCacheKey() {
  return [props.assetId, beikeForm.address.trim(), beikeForm.area.trim()].join('|')
}

function readBeikeCache(maxAge = BEIKE_TTL): { at: number; result: Record<string, unknown> } | null {
  try {
    const all = JSON.parse(localStorage.getItem(BEIKE_CACHE_KEY) || '{}') as Record<string, { at: number; result: Record<string, unknown> }>
    const hit = all[beikeCacheKey()]
    if (hit && Date.now() - hit.at < maxAge) return hit
  } catch {
    /* 本地存储不可用时忽略 */
  }
  return null
}

function writeBeikeCache(result: Record<string, unknown>) {
  try {
    const all = JSON.parse(localStorage.getItem(BEIKE_CACHE_KEY) || '{}') as Record<string, { at: number; result: Record<string, unknown> }>
    all[beikeCacheKey()] = { at: Date.now(), result }
    // 只留最近 20 条，别把本地存储撑爆
    const entries = Object.entries(all).sort((a, b) => b[1].at - a[1].at).slice(0, 20)
    localStorage.setItem(BEIKE_CACHE_KEY, JSON.stringify(Object.fromEntries(entries)))
  } catch {
    /* 忽略 */
  }
}

function cacheAgeLabel(at: number) {
  const days = Math.floor((Date.now() - at) / 86_400_000)
  if (days <= 0) return '今天'
  return `${days} 天前`
}

/** 额度用完时：直接转到手工估值，不让用户卡在这一步 */
function switchToManualValuation() {
  beikeOpen.value = false
  openValuation()
}

function dimLabel(dim: unknown) {
  return ({ resblock: '小区均价', bizcircle: '商圈均价', district: '城区均价', city: '城市均价' } as Record<string, string>)[String(dim)] || String(dim || '—')
}

function openBeike() {
  beikeErrors.value = {}
  beikeResult.value = null
  beikeQuota.value = ''
  beikeCachedAt.value = 0
  Object.assign(beikeForm, {
    city: '', city_code: '',
    address: String(ext.value.address || detail.value?.name || ''),
    area: String(ext.value.building_area || ''),
  })
  // 有 7 天内的同参数结果就直接展示，不消耗额度
  const cached = readBeikeCache()
  if (cached) {
    beikeResult.value = cached.result
    beikeCachedAt.value = cached.at
  }
  beikeOpen.value = true
}

async function runBeike() {
  beikeBusy.value = true
  beikeErrors.value = {}
  beikeQuota.value = ''
  try {
    const result = await api.post<Record<string, unknown>>(
      `/ledger/assets/${props.assetId}/beike-valuate`, { ...beikeForm },
    )
    beikeResult.value = result
    beikeCachedAt.value = 0
    writeBeikeCache(result)
    toast.success('贝壳估价完成，请核对后决定是否采用')
  } catch (err) {
    beikeErrors.value = errorFields(err)
    // 额度用完是「暂时没有次数」，不是配置坏了：给可操作的出口，不弹无意义的红条
    if ((err as { code?: string })?.code === 'QUOTA_EXCEEDED') {
      beikeQuota.value = errorMessage(err)
      // 额度用完时，把更早的估价结果也拿出来给用户参考（不再要求 7 天内）
      const stale = readBeikeCache(Number.POSITIVE_INFINITY)
      if (stale) {
        beikeResult.value = stale.result
        beikeCachedAt.value = stale.at
      }
    } else {
      toast.error(errorMessage(err))
    }
  } finally {
    beikeBusy.value = false
  }
}

async function applyBeike() {
  const total = beikeResult.value?.total_price
  if (!total) return
  beikeApplying.value = true
  try {
    await api.post(`/ledger/assets/${props.assetId}/valuations`, {
      value: String(total),
      source: 'beike',
      source_note: `贝壳开放平台估价 ${new Date().toISOString().slice(0, 10)}`,
      reason: '采用贝壳开放平台估价结果',
    })
    toast.success('已采用贝壳估价，资产当前价值已更新')
    beikeOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    beikeApplying.value = false
  }
}

// ---------------- 处置 ----------------
const disposeOpen = ref(false)
const disposeSaving = ref(false)
const disposeErrors = ref<Record<string, string>>({})
const disposeForm = reactive({
  dispose_type: 'sold', occurred_at: '', amount: '', account_id: '',
  counterparty: '', reason: '', note: '',
})

function openDispose() {
  disposeErrors.value = {}
  Object.assign(disposeForm, {
    dispose_type: 'sold', occurred_at: '', amount: detail.value?.current_value || '',
    account_id: '', counterparty: '', reason: '', note: '',
  })
  disposeOpen.value = true
}

async function saveDispose() {
  disposeSaving.value = true
  disposeErrors.value = {}
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'asset.dispose', object_type: 'asset', object_id: props.assetId,
    })
    await api.post(`/ledger/assets/${props.assetId}/dispose`, {
      dispose_type: disposeForm.dispose_type,
      occurred_at: disposeForm.occurred_at || null,
      amount: disposeForm.amount || '0',
      account_id: disposeForm.account_id || null,
      counterparty: disposeForm.counterparty || null,
      reason: disposeForm.reason,
      note: disposeForm.note || null,
      confirm_token: tokenRes.token,
    })
    toast.success('资产已处置，已生成资产处置事件')
    disposeOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    disposeErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    disposeSaving.value = false
  }
}

// ---------------- 归档 ----------------
const archiveOpen = ref(false)
const archiveSaving = ref(false)
const archiveReason = ref('')

function openArchive() {
  archiveReason.value = ''
  archiveOpen.value = true
}

async function saveArchive() {
  archiveSaving.value = true
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'asset.archive', object_type: 'asset', object_id: props.assetId,
    })
    await api.post(`/ledger/assets/${props.assetId}/archive`, {
      reason: archiveReason.value, confirm_token: tokenRes.token,
    })
    toast.success('资产已归档，历史记录保留')
    archiveOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    archiveSaving.value = false
  }
}

// ---------------- 记一笔费用（15 §5.4、23 §4：费用关联资产，进入统一流水） ----------------
const expenseOpen = ref(false)
const expenseSaving = ref(false)
const expenseErrors = ref<Record<string, string>>({})
const expenseCategories = ref<{ id: string; name: string }[]>([])
const expenseForm = reactive({
  title: '', amount: '', occurred_at: '', account_id: '', category_id: '', note: '',
})

function openExpense() {
  expenseErrors.value = {}
  Object.assign(expenseForm, {
    title: `${detail.value?.name || '资产'} 费用`, amount: '', occurred_at: '',
    account_id: '', category_id: '', note: '',
  })
  expenseOpen.value = true
  if (!expenseCategories.value.length) loadExpenseCategories()
}

async function loadExpenseCategories() {
  try {
    const res = await api.get<{ items: { id: string; name: string }[] }>(
      '/ledger/categories', { domain: 'expense' })
    expenseCategories.value = res.items
  } catch { /* 分类加载失败不阻塞费用录入 */ }
}

async function saveExpense(confirm = false) {
  expenseSaving.value = true
  expenseErrors.value = {}
  try {
    const created = await api.post<{ id: string }>('/ledger/events', {
      event_type: 'expense',
      title: expenseForm.title,
      amount: expenseForm.amount || '0',
      occurred_at: expenseForm.occurred_at || null,
      account_id: expenseForm.account_id || null,
      category_id: expenseForm.category_id || null,
      asset_id: props.assetId,
      note: expenseForm.note || null,
      source: 'manual',
    })
    if (confirm) {
      await api.post(`/ledger/events/${created.id}/confirm`, {})
      toast.success('费用已确认并计入统计，已关联到本资产')
    } else {
      toast.success('费用草稿已保存，确认后计入统计')
    }
    expenseOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    expenseErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    expenseSaving.value = false
  }
}

// ---------------- 车辆保险 ----------------
const insuranceOpen = ref(false)
const insuranceSaving = ref(false)
const insuranceErrors = ref<Record<string, string>>({})
const editingInsuranceId = ref('')
const insuranceForm = reactive({
  insurance_type: 'compulsory', insurer: '', policy_no: '', effective_date: '',
  expiry_date: '', premium: '', coverage: '', deductible: '',
  insured_person: '', beneficiary: '', renewal_status: 'pending', remind_date: '', note: '',
})
const insuranceTypeOptions = ['compulsory', 'commercial', 'damage', 'third_party', 'occupant', 'other'].map(
  (v) => ({ value: v, label: zh('insurance_type', v) }),
)
const renewalOptions = ['pending', 'renewed', 'expired', 'cancelled'].map((v) => ({
  value: v, label: zh('insurance_renewal_status', v),
}))

function openInsurance(row?: Record<string, any>) {
  insuranceErrors.value = {}
  editingInsuranceId.value = row ? String(row.id) : ''
  Object.assign(insuranceForm, {
    insurance_type: row?.insurance_type || 'compulsory',
    insurer: row?.insurer || '',
    policy_no: row?.policy_no || '',
    effective_date: row?.effective_date ? String(row.effective_date).slice(0, 10) : '',
    expiry_date: row?.expiry_date ? String(row.expiry_date).slice(0, 10) : '',
    premium: row?.premium || '',
    coverage: row?.coverage || '',
    deductible: row?.deductible || '',
    insured_person: row?.insured_person || '',
    beneficiary: row?.beneficiary || '',
    renewal_status: row?.renewal_status || 'pending',
    remind_date: row?.remind_date ? String(row.remind_date).slice(0, 10) : '',
    note: row?.note || '',
  })
  insuranceOpen.value = true
}

async function saveInsurance() {
  insuranceSaving.value = true
  insuranceErrors.value = {}
  const body: Record<string, unknown> = {
    insurance_type: insuranceForm.insurance_type,
    insurer: insuranceForm.insurer || null,
    policy_no: insuranceForm.policy_no || null,
    effective_date: insuranceForm.effective_date || null,
    expiry_date: insuranceForm.expiry_date || null,
    premium: insuranceForm.premium || null,
    coverage: insuranceForm.coverage || null,
    deductible: insuranceForm.deductible || null,
    insured_person: insuranceForm.insured_person || null,
    beneficiary: insuranceForm.beneficiary || null,
    renewal_status: insuranceForm.renewal_status,
    remind_date: insuranceForm.remind_date || null,
    note: insuranceForm.note || null,
  }
  try {
    if (editingInsuranceId.value) {
      await api.put(`/ledger/assets/vehicle-insurances/${editingInsuranceId.value}`, body)
      toast.success('保险记录已更新')
    } else {
      await api.post(`/ledger/assets/vehicles/${props.assetId}/insurances`, body)
      toast.success('保险记录已保存')
    }
    insuranceOpen.value = false
    await load()
  } catch (err) {
    insuranceErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    insuranceSaving.value = false
  }
}

async function removeInsurance(row: Record<string, any>) {
  const res = await ui.askConfirm({
    title: '删除保险记录',
    message: `将删除「${zh('insurance_type', String(row.insurance_type))}」记录，删除为软删除并保留审计。`,
    confirmText: '确认删除', danger: true, requireReason: true, reasonLabel: '删除原因（必填）',
  })
  if (!res.ok) return
  try {
    await api.delete(`/ledger/assets/vehicle-insurances/${row.id}`, { reason: res.reason })
    toast.success('保险记录已删除')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

// ---------------- 水电煤户号 ----------------
const utilityOpen = ref(false)
const utilitySaving = ref(false)
const utilityErrors = ref<Record<string, string>>({})
const editingUtilityId = ref('')
const utilityForm = reactive({
  utility_type: 'water', provider: '', account_no: '', account_name: '',
  billing_address: '', billing_cycle: 'monthly', billing_day: '',
  default_account_id: '', note: '',
})
const utilityTypeOptions = ['water', 'electricity', 'gas', 'heating', 'property', 'network', 'other'].map(
  (v) => ({ value: v, label: zh('utility_type', v) }),
)

function openUtility(row?: Record<string, any>) {
  utilityErrors.value = {}
  editingUtilityId.value = row ? String(row.id) : ''
  Object.assign(utilityForm, {
    utility_type: row?.utility_type || 'water',
    provider: row?.provider || '',
    account_no: row?.account_no || '',
    account_name: row?.account_name || '',
    billing_address: row?.billing_address || '',
    billing_cycle: row?.billing_cycle || 'monthly',
    billing_day: row?.billing_day ?? '',
    default_account_id: row?.default_account_id || '',
    note: row?.note || '',
  })
  utilityOpen.value = true
}

async function saveUtility() {
  utilitySaving.value = true
  utilityErrors.value = {}
  // 只提交这三项；编辑时其余历史字段不传，后端按部分更新保留
  const body: Record<string, unknown> = {
    utility_type: utilityForm.utility_type,
    account_no: utilityForm.account_no || null,
    account_name: utilityForm.account_name || null,
  }
  try {
    if (editingUtilityId.value) {
      await api.put(`/ledger/assets/utility-accounts/${editingUtilityId.value}`, body)
      toast.success('户号已更新')
    } else {
      await api.post(`/ledger/assets/real-estates/${props.assetId}/utility-accounts`, body)
      toast.success('户号已保存')
    }
    utilityOpen.value = false
    await load()
  } catch (err) {
    utilityErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    utilitySaving.value = false
  }
}

async function toggleUtility(row: Record<string, any>) {
  try {
    await api.put(`/ledger/assets/utility-accounts/${row.id}`, { enabled: !row.enabled })
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function removeUtility(row: Record<string, any>) {
  const res = await ui.askConfirm({
    title: '删除水电煤户号',
    message: `将删除「${zh('utility_type', String(row.utility_type))}」户号，删除为软删除并保留审计。`,
    confirmText: '确认删除', danger: true, requireReason: true, reasonLabel: '删除原因（必填）',
  })
  if (!res.ok) return
  try {
    await api.delete(`/ledger/assets/utility-accounts/${row.id}`, { reason: res.reason })
    toast.success('户号已删除')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

const utilityRows = computed(() => (detail.value?.related?.utility_accounts as Record<string, any>[]) || [])
const insuranceRows = computed(() => (detail.value?.related?.insurances as Record<string, any>[]) || [])
const liabilityInfo = computed(() => (detail.value?.related?.liabilities as Record<string, any>) || {})
const leaseInfo = computed(() => (detail.value?.related?.leases as Record<string, any>) || {})
const leaseItems = computed(() => (leaseInfo.value.items as Record<string, any>[]) || [])
const fmtDay = (v?: string | null) => (v ? String(v).slice(0, 10) : '—')
function leaseStatusTone(status?: string) {
  if (status === 'active') return 'success'
  if (status === 'terminated' || status === 'expired') return 'neutral'
  return 'info'
}
const liabilityRows = computed(() => (liabilityInfo.value.items as Record<string, any>[]) || [])

/**
 * 「是否有房贷/车贷」以负债模块的实际数据为准：
 * 资产上的标记可能没同步（早期建档时只写了负债侧），不能反过来否定已经存在的贷款。
 */
const hasLoanFlag = computed(
  () => liabilityRows.value.length > 0 || Boolean(ext.value.has_mortgage || ext.value.has_loan),
)

/** 打开负债详情（房贷/车贷在负债模块统一维护，资产侧只显示摘要） */
function openLiability(item: Record<string, any>) {
  if (item?.id) {
    router.push({ path: '/ledger/liabilities', query: { open: String(item.id) } })
  }
}

const maintenanceEvents = computed(() =>
  (detail.value?.events || []).filter(
    (e) => e.event_type === 'expense' && /保养|维修|年检|洗车|违章|过路|停车|燃油|充电/.test(e.title),
  ),
)
</script>

<template>
  <AppDrawer :open="open" :title="detail?.name || '资产详情'" :width="720" @close="emit('close')">
    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
    <template v-else-if="detail">
      <div class="head">
        <div class="head-main">
          <div class="row">
            <StatusBadge :label="detail.asset_type_label" tone="info" />
            <StatusBadge :label="detail.status_label" :tone="statusTone(detail.status)" />
            <StatusBadge :label="detail.liquidity_label" tone="neutral" />
            <StatusBadge v-if="detail.counts_in_net_worth" label="计入净资产" tone="success" />
          </div>
          <p class="value">
            {{ isLend ? '剩余借款' : '当前价值' }}
            <MoneyText :value="isLend ? (rec?.outstanding || '0') : detail.current_value"
                       :currency="detail.currency" show-currency />
          </p>
          <p class="meta">
            <template v-if="isLend">
              借款本金 {{ formatAmount(detail.acquire_cost, ui.amountPrecision) }} {{ detail.currency }}
              <template v-if="rec?.due_date"> · 约定还清 {{ fmtDate(rec.due_date) }}</template>
            </template>
            <template v-else>
              购置成本 {{ formatAmount(detail.acquire_cost, ui.amountPrecision) }} {{ detail.currency }} · 最近估值
              {{ fmtDate(detail.last_valuation_date) }}<template v-if="!isProperty"> · {{ detail.value_source_label || '未标注来源' }}</template>
            </template>
          </p>
        </div>
      </div>

      <div class="tabs">
        <button
          v-for="t in visibleTabs"
          :key="t.key"
          class="tab"
          :class="{ 'is-active': activeTab === t.key }"
          type="button"
          @click="activeTab = t.key"
        >
          {{ t.label }}
        </button>
      </div>

      <!-- 通用概览 -->
      <div v-if="activeTab === 'overview'" class="pane">
        <ul class="kv">
          <li><span class="k">资产类型</span><span class="v">{{ detail.asset_type_label }}<template v-if="detail.sub_type"> · {{ detail.sub_type }}</template></span></li>
          <li><span class="k">币种</span><span class="v">{{ detail.currency }}</span></li>
          <!-- 借出款没有「购置」概念：日期=借出日、成本=借出本金、当前价值=剩余借款 -->
          <li><span class="k">{{ isLend ? '借出日期' : '购置日期' }}</span><span class="v">{{ fmtDate(isLend ? rec?.lent_at : detail.acquire_date) }}</span></li>
          <li>
            <span class="k">{{ isLend ? '借出本金' : '购置成本' }}</span>
            <span class="v">{{ formatAmount(isLend ? rec?.principal : detail.acquire_cost, ui.amountPrecision) }} {{ detail.currency }}</span>
          </li>
          <li>
            <span class="k">{{ isLend ? '剩余借款' : '当前价值' }}</span>
            <span class="v strong">{{ formatAmount(isLend ? rec?.outstanding : detail.current_value, ui.amountPrecision) }} {{ detail.currency }}</span>
          </li>
          <!-- 不动产不显示价值来源/折旧方式/维护日期：价值只来自估值时间线，房产也不折旧；
               借出款是应收，连估值概念都没有，这几项全部不显示 -->
          <li v-if="!isProperty && !isLend"><span class="k">价值来源</span><span class="v">{{ detail.value_source_label || '—' }}</span></li>
          <li v-if="!isLend"><span class="k">最近估值日期</span><span class="v">{{ fmtDate(detail.last_valuation_date) }}</span></li>
          <li v-if="!isProperty && !isLend"><span class="k">折旧方式</span><span class="v">{{ detail.depreciation_method_label || '—' }}</span></li>
          <li v-if="!isProperty && !isLend"><span class="k">维护日期</span><span class="v">{{ fmtDate(detail.maintenance_date) }}</span></li>
          <li><span class="k">是否计入净资产</span><span class="v">{{ detail.include_in_net_worth ? '是' : '否' }}</span></li>
          <li><span class="k">备注</span><span class="v">{{ detail.note || '—' }}</span></li>
        </ul>
        <div v-if="detail.custom_attributes" class="custom">
          <h4>自定义属性</h4>
          <pre>{{ JSON.stringify(detail.custom_attributes, null, 2) }}</pre>
        </div>
        <slot name="tab-overview" :detail="detail" />
      </div>

      <!-- 类型资料 -->
      <div v-else-if="activeTab === 'profile'" class="pane">
        <StateBlock
          v-if="!profileRows.length"
          state="empty" title="暂无专属资料" description="该资产还没有补充类型专属字段。"
        />
        <ul v-else class="kv">
          <li v-for="row in profileRows" :key="row.key">
            <span class="k">{{ row.label }}</span><span class="v">{{ row.value }}</span>
          </li>
        </ul>
        <slot name="tab-profile" :detail="detail" />
      </div>

      <!-- 房贷 / 贷款（负债模块统一管理） -->
      <div v-else-if="activeTab === 'mortgage' || activeTab === 'loan'" class="pane">
        <ul class="kv">
          <li>
            <span class="k">{{ activeTab === 'mortgage' ? '是否有房贷' : '是否有贷款' }}</span>
            <span class="v">{{ hasLoanFlag ? '是' : '否' }}</span>
          </li>
          <li>
            <span class="k">关联负债</span>
            <span class="v">
              <template v-if="liabilityRows.length">
                {{ liabilityRows.map((r) => r.name).join('、') }}
                <small class="sub">（{{ liabilityRows.length }} 笔）</small>
              </template>
              <template v-else>未关联</template>
            </span>
          </li>
        </ul>

        <!-- 负债模块提供的贷款摘要（M3：余额、利率、还款与还款计划入口） -->
        <template v-if="liabilityRows.length">
          <ul v-for="item in liabilityRows" :key="String(item.id)" class="kv loan">
            <li><span class="k">贷款名称</span><span class="v strong">{{ item.name }}</span></li>
            <li><span class="k">债权机构</span><span class="v">{{ item.creditor || '—' }}</span></li>
            <li><span class="k">初始金额</span><span class="v">{{ item.initial_amount }}</span></li>
            <li><span class="k">剩余本金</span><span class="v strong">{{ item.outstanding }} {{ item.currency }}</span></li>
            <li><span class="k">年利率 / 类型</span><span class="v">{{ item.interest_rate || '—' }}% · {{ item.rate_type_label || '未设置' }}</span></li>
            <li><span class="k">还款方式 / 频率</span><span class="v">{{ item.repayment_method_label || '—' }} · {{ item.payment_frequency_label }}</span></li>
            <li><span class="k">每期应还</span><span class="v">{{ item.monthly_payment || '—' }}</span></li>
            <li><span class="k">下次还款日</span><span class="v strong">{{ fmtDate(item.next_payment_date) }}</span></li>
            <li><span class="k">状态</span><span class="v">{{ item.status_label }}</span></li>
          </ul>
          <div class="pane-actions">
            <AppButton variant="secondary" @click="openLiability(liabilityRows[0])">
              查看负债详情与还款计划
            </AppButton>
          </div>
        </template>

        <StateBlock
          v-else
          state="pending"
          :title="liabilityInfo.module_enabled ? '已建立关联' : '尚未关联贷款'"
          :description="(liabilityInfo.note as string) || '房贷/车贷由负债模块统一管理。'"
        />
      </div>

      <!-- 租赁：直接显示该房产的租约（户号按房产自动带出） -->
      <div v-else-if="activeTab === 'leases'" class="pane">
        <StateBlock
          v-if="!leaseItems.length"
          state="empty"
          title="暂无租约"
          description="在「家庭台账 → 租赁」为该房产新建租约后，租金、下一期收租日与押金状态会自动显示在这里。"
        />
        <template v-else>
        <div v-for="l in leaseItems" :key="l.id" class="lease-card">
          <header class="lease-head">
            <StatusBadge :label="l.status_label" :tone="leaseStatusTone(l.status)" />
            <strong>{{ l.tenant_name }}</strong>
            <small class="lease-no">{{ l.lease_no }}</small>
          </header>
          <dl class="lease-grid">
            <div><dt>月租</dt><dd>{{ l.monthly_rent }} {{ l.currency }}</dd></div>
            <div><dt>租期</dt><dd>{{ fmtDay(l.start_date) }} ~ {{ fmtDay(l.end_date) }}</dd></div>
            <div>
              <dt>下一期收租</dt>
              <dd :class="{ warn: !l.next_receipt_date }">
                {{ l.next_receipt_date ? fmtDay(l.next_receipt_date) : '未生成收租计划' }}
              </dd>
            </div>
            <div>
              <dt>押金</dt>
              <dd>
                {{ l.deposit_amount }} {{ l.currency }}
                <span :class="['dep-tag', l.deposit_summary?.received ? 'ok' : 'muted']">
                  {{ l.deposit_summary?.received ? '已收' : '未收' }}
                </span>
              </dd>
            </div>
            <div>
              <dt>待收期数</dt>
              <dd>{{ l.receivable_summary?.pending ?? 0 }} 期 · {{ l.receivable_summary?.total_pending || '0.00' }}</dd>
            </div>
            <div>
              <dt>计入净资产（按资产）</dt>
              <dd>{{ detail.counts_in_net_worth ? '是' : '否' }}</dd>
            </div>
          </dl>
          <div class="lease-utils">
            <template v-if="l.utilities && l.utilities.length">
              <span v-for="u in l.utilities" :key="u.id" class="util-chip">
                {{ u.utility_type_label }} {{ u.account_no }}
              </span>
            </template>
            <span v-else class="muted">该房产还没有登记水电煤户号</span>
          </div>
        </div>
        </template>
      </div>

      <!-- 所属不动产（车位） -->
      <!-- 借出款：应收、回款记录与登记回款 -->
      <div v-else-if="activeTab === 'receivable'" class="pane">
        <StateBlock v-if="!rec" state="empty" title="这不属于借出款" />
        <template v-else>
          <div class="rec-stats">
            <div><span class="k">借款本金</span><strong>{{ rec.principal }}</strong></div>
            <div><span class="k">已收回</span><strong class="ok">{{ rec.repaid_total }}</strong></div>
            <div><span class="k">未收回</span><strong class="warn">{{ rec.outstanding }}</strong></div>
          </div>

          <!-- 对账：每期金额不固定，只比「已还进度 vs 时间进度」 -->
          <div class="recon" :class="`st-${rec.reconcile.status}`">
            <div class="recon-head">
              <StatusBadge :label="rec.reconcile.status_label"
                           :tone="rec.reconcile.status === 'overdue' || rec.reconcile.status === 'at_risk' ? 'danger'
                             : rec.reconcile.status === 'settled' ? 'success' : 'neutral'" />
              <small v-if="rec.reconcile.has_schedule_window">
                距约定还清日 {{ rec.reconcile.days_left }} 天（已过 {{ rec.reconcile.time_percent }}%）
              </small>
            </div>

            <!-- 双进度条：上=已还比例，下=时间进度 -->
            <div class="bars" v-if="rec.reconcile.has_schedule_window">
              <div class="bar-row">
                <span class="bl">已还</span>
                <div class="bar"><i class="fill paid" :style="{ width: `${rec.reconcile.paid_percent}%` }" /></div>
                <span class="bv">{{ rec.reconcile.paid_percent }}%</span>
              </div>
              <div class="bar-row">
                <span class="bl">时间</span>
                <div class="bar"><i class="fill time" :style="{ width: `${rec.reconcile.time_percent || 0}%` }" /></div>
                <span class="bv">{{ rec.reconcile.time_percent }}%</span>
              </div>
            </div>

            <ul class="recon-facts">
              <li v-if="rec.reconcile.has_schedule_window">
                <span class="k">剩余期限每天需还</span>
                <span class="v">{{ rec.reconcile.daily_needed }} 元</span>
              </li>
              <li v-if="rec.reconcile.projected_settle_date">
                <span class="k">按当前节奏预计还清</span>
                <span class="v" :class="{ warn: rec.reconcile.will_be_late }">
                  {{ fmtDate(rec.reconcile.projected_settle_date) }}
                  <template v-if="rec.reconcile.will_be_late">（会逾期）</template>
                </span>
              </li>
              <li><span class="k">回款笔数</span><span class="v">{{ rec.reconcile.repayment_count }} 笔</span></li>
            </ul>
            <p class="recon-tip">
              约定是「期限内还完」，不要求每期等额，所以这里不判「进度落后」，
              只看：还剩多少、还剩几天、每天得还多少。想精确到某期，回款时填一下期次即可。
            </p>
          </div>
          <ul class="kv">
            <li><span class="k">借款人</span><span class="v">{{ rec.borrower_name }}</span></li>
            <li><span class="k">身份证号</span><span class="v">{{ rec.borrower_id_no || '—' }}</span></li>
            <li><span class="k">登记方式</span><span class="v">{{ rec.lent_mode_label }}</span></li>
            <li><span class="k">出借方式</span><span class="v">{{ rec.lend_type_label }}</span></li>
            <li><span class="k">借款日期</span><span class="v">{{ fmtDate(rec.lent_at) }}</span></li>
            <li><span class="k">还款方式</span><span class="v">{{ rec.repayment_method_label }}</span></li>
            <li><span class="k">约定还款日</span><span class="v">{{ fmtDate(rec.due_date) }}</span></li>
            <li>
              <span class="k">利息约定</span>
              <span class="v">
                <template v-if="rec.interest_enabled">
                  年化 {{ rec.interest_rate }}%<template v-if="Number(rec.interest_received) > 0">
                    · 已收利息 {{ rec.interest_received }}</template>
                </template>
                <template v-else>不计息</template>
              </span>
            </li>
            <li v-if="rec.fee_amount">
              <span class="k">手续费</span>
              <span class="v">{{ rec.fee_amount }} · {{ rec.fee_bearer_label }}</span>
            </li>
            <li><span class="k">状态</span><span class="v">{{ rec.settled ? '已全部收回' : '待收回' }}</span></li>
            <li v-if="rec.note"><span class="k">备注</span><span class="v">{{ rec.note }}</span></li>
          </ul>
          <div class="rec-actions">
            <AppButton v-if="canConfirm && !rec.settled" variant="primary" @click="openRepay">
              登记回款
            </AppButton>
            <span class="rec-tip">回款只把「应收」变成「现金」，不计收入；可以分多次登记。</span>
          </div>
          <!-- 分期计划：金额每期可不同，逐期核对 -->
          <div class="sched-head">
            <h4 class="rec-h">分期计划</h4>
            <div class="sched-actions">
              <span v-if="rec.periods.length" class="sched-sum">
                共 {{ rec.periods.length }} 期 · 已还 {{ rec.reconcile.paid_periods }} 期
                · 剩余 {{ rec.reconcile.unpaid_periods }} 期
                <template v-if="rec.reconcile.overdue_periods">
                  · <strong class="warn">逾期 {{ rec.reconcile.overdue_periods }} 期</strong>
                </template>
              </span>
              <AppButton v-if="canEdit" variant="secondary" size="sm" @click="openSchedule">
                {{ rec.periods.length ? '重新设置' : '设置分期计划' }}
              </AppButton>
            </div>
          </div>
          <p v-if="!rec.periods.length" class="sched-empty">
            还没有分期计划。金额每期不同的分期，建一张计划表才能逐期核对「第几期该还多少、还欠多少」。
          </p>
          <div v-else class="sched-wrap">
            <table class="sched-table">
              <thead>
                <tr>
                  <th>期数</th><th>应还月</th><th class="ta-r">应还金额</th>
                  <th class="ta-r">已还</th><th>状态</th><th class="ta-r">剩余欠款</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(p, idx) in rec.periods" :key="p.id"
                    :class="`p-${p.status}`">
                  <td>{{ p.period_no }}</td>
                  <td>{{ (p.due_date || '').slice(0, 7) }}</td>
                  <td class="ta-r">{{ p.amount }}</td>
                  <td class="ta-r">{{ p.paid_amount }}</td>
                  <td><span class="p-tag">{{ p.status_label }}</span></td>
                  <td class="ta-r">{{ periodBalanceAfter(idx) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <h4 class="rec-h">回款记录</h4>
          <StateBlock v-if="!rec.repayments.length" state="empty" title="还没有回款记录" />
          <ul v-else class="list">
            <li v-for="r in rec.repayments" :key="r.id">
              <div class="l-main">
                <strong>
                  <span v-if="r.period_range" class="period-tag">第 {{ r.period_range }} 期</span>
                  <span v-else-if="r.period_no" class="period-tag">第 {{ r.period_no }} 期</span>
                  <span v-if="r.affects_account === false" class="period-tag hist">历史</span>
                  {{ r.amount }}
                </strong>
                <small>
                  {{ fmtDate(r.received_at) }} · {{ r.account_name || '—' }}
                  <template v-if="Number(r.interest_part) > 0">
                    · 本金 {{ r.principal_part }} + 利息 {{ r.interest_part }}
                  </template>
                </small>
              </div>
              <span class="l-note">{{ r.note || '' }}</span>
            </li>
          </ul>
        </template>
      </div>

      <div v-else-if="activeTab === 'linked'" class="pane">
        <ul class="kv">
          <li><span class="k">所属不动产</span><span class="v">{{ ext.linked_real_estate_name || '未关联' }}</span></li>
          <li><span class="k">关联说明</span><span class="v">一个车位最多关联一套不动产；解除关联会保留审计历史。</span></li>
        </ul>
      </div>

      <!-- 保管备注（贵金属） -->
      <div v-else-if="activeTab === 'storage'" class="pane">
        <ul class="kv">
          <li><span class="k">保管位置</span><span class="v">{{ ext.storage_location || '—' }}</span></li>
          <li><span class="k">备注</span><span class="v">{{ ext.note || detail.note || '—' }}</span></li>
        </ul>
      </div>

      <!-- 保养维修：由统一费用流水承载 -->
      <div v-else-if="activeTab === 'maintenance'" class="pane">
        <p class="note">保养与维修统一通过费用流水记录，使用费用分类区分（保养、维修、年检、洗车等）。</p>
        <StateBlock
          v-if="!maintenanceEvents.length"
          state="empty" title="暂无保养维修记录"
          description="在「费用流水」中以支出事件记录，关联到本车辆后即会出现在这里。"
        />
        <ul v-else class="list">
          <li v-for="e in maintenanceEvents" :key="e.id">
            <div class="l-main"><strong>{{ e.title }}</strong><small>{{ e.event_no }} · {{ fmtDate(e.occurred_at) }}</small></div>
            <span class="l-amount">{{ e.amount }} {{ e.currency }}</span>
          </li>
        </ul>
      </div>

      <!-- 关联负债 / 租赁（其他资产） -->
      <div v-else-if="activeTab === 'related'" class="pane">
        <StateBlock
          state="pending"
          :title="liabilityInfo.module_enabled ? '已建立关联' : '负债模块尚未启用'"
          :description="(liabilityInfo.note as string) || '关联负债由负债模块统一管理。'"
        />
        <StateBlock
          state="pending" title="租赁模块尚未启用"
          :description="(leaseInfo.note as string) || '租赁信息由租赁模块统一管理。'"
        />
      </div>

      <!-- 估值历史 -->
      <div v-else-if="activeTab === 'valuations'" class="pane">
        <div class="pane-actions">
          <AppButton v-if="canEdit" variant="secondary" @click="openValuation">
            <Plus :size="15" /> 新增估值
          </AppButton>
        </div>
        <StateBlock
          v-if="!detail.valuations.length" state="empty" title="暂无估值记录"
          description="登记一次估值后，这里会形成估值时间线。"
        />
        <ul v-else class="list">
          <li v-for="v in detail.valuations" :key="v.id">
            <div class="l-main">
              <strong>{{ v.value }} {{ v.currency }}</strong>
              <small>{{ fmtDateTime(v.valued_at) }} · {{ v.source_label }}<template v-if="v.source_note"> · {{ v.source_note }}</template></small>
              <small v-if="v.reason" class="reason">调整原因：{{ v.reason }}</small>
            </div>
          </li>
        </ul>
      </div>

      <!-- 水电煤户号 -->
      <div v-else-if="activeTab === 'utility'" class="pane">
        <div class="pane-actions">
          <AppButton v-if="canEdit" variant="secondary" @click="openUtility()">
            <Plus :size="15" /> 新增户号
          </AppButton>
        </div>
        <StateBlock
          v-if="!utilityRows.length" state="empty" title="暂无水电煤户号"
          description="户号必须挂在具体不动产上；账单可在智能录入模块中通过 OCR 识别。"
        />
        <ul v-else class="list">
          <li v-for="u in utilityRows" :key="String(u.id)">
            <div class="l-main">
              <strong>{{ u.utility_type_label }} · {{ u.provider || '未填写供应商' }}</strong>
              <small>户号 {{ u.account_no || '—' }} · 户名 {{ u.account_name || '—' }}</small>
              <small>周期 {{ u.billing_cycle_label || '—' }} · 缴费日 {{ u.billing_day || '—' }}</small>
            </div>
            <div class="l-actions">
              <StatusBadge :label="u.enabled ? '启用' : '已停用'" :tone="u.enabled ? 'success' : 'neutral'" />
              <button v-if="canEdit" class="act" type="button" @click="openUtility(u)">编辑</button>
              <button v-if="canEdit" class="act" type="button" @click="toggleUtility(u)">{{ u.enabled ? '停用' : '启用' }}</button>
              <button v-if="auth.can('delete')" class="act danger" type="button" @click="removeUtility(u)">
                <Trash2 :size="13" />
              </button>
            </div>
          </li>
        </ul>
      </div>

      <!-- 车辆保险 -->
      <div v-else-if="activeTab === 'insurance'" class="pane">
        <div class="pane-actions">
          <AppButton v-if="canEdit" variant="secondary" @click="openInsurance()">
            <Plus :size="15" /> 新增保险
          </AppButton>
        </div>
        <StateBlock
          v-if="!insuranceRows.length" state="empty" title="暂无保险记录"
          description="记录交强险与商业险后，这里会显示到期状态；到期提醒将在提醒模块交付后接入。"
        />
        <ul v-else class="list">
          <li v-for="i in insuranceRows" :key="String(i.id)">
            <div class="l-main">
              <strong>{{ i.insurance_type_label }} · {{ i.insurer || '未填写保险公司' }}</strong>
              <small>到期 {{ fmtDate(i.expiry_date) }} · 保费 {{ i.premium || '—' }} · 保额 {{ i.coverage || '—' }}</small>
              <small v-if="i.expiring_soon" class="warn">30 天内到期（剩余 {{ i.days_to_expiry }} 天）</small>
              <small v-else-if="i.expired" class="danger">已过期 {{ -(i.days_to_expiry as number) }} 天</small>
            </div>
            <div class="l-actions">
              <StatusBadge :label="i.renewal_status_label as string" tone="neutral" />
              <button v-if="canEdit" class="act" type="button" @click="openInsurance(i)">编辑</button>
              <button v-if="auth.can('delete')" class="act danger" type="button" @click="removeInsurance(i)">
                <Trash2 :size="13" />
              </button>
            </div>
          </li>
        </ul>
      </div>

      <!-- 资金事件 / 费用流水 -->
      <div v-else-if="activeTab === 'events'" class="pane">
        <StateBlock
          v-if="!detail.events.length" state="empty" title="暂无关联事件"
          description="资产购置、处置与费用流水都会显示在这里。"
        />
        <ul v-else class="list">
          <li v-for="e in detail.events" :key="e.id">
            <div class="l-main">
              <strong>{{ e.title }}</strong>
              <small>{{ e.event_type_label }} · {{ e.event_no }} · {{ fmtDate(e.occurred_at) }}</small>
              <small v-if="e.movements.length">
                资金变动：{{ e.movements.map((m) => `${m.direction_label} ${m.amount} ${m.currency}`).join(' / ') }}
              </small>
              <small v-if="e.realized_gain !== null && e.realized_gain !== undefined">
                已实现损益：{{ Number(e.realized_gain) >= 0 ? '+' : '' }}{{ e.realized_gain }} {{ e.currency }}
              </small>
            </div>
            <StatusBadge :label="e.status_label" :tone="e.status === 'confirmed' ? 'success' : 'neutral'" />
          </li>
        </ul>
      </div>

      <!-- 附件：上传、预览、下载、删除 -->
      <div v-else-if="activeTab === 'attachments'" class="pane">
        <AssetAttachments
          :asset-id="detail.id"
          :disabled="!canEdit"
          hint="房产证、合同、发票、照片等；图片可直接预览"
        />
      </div>

      <!-- AI 分析 -->
      <div v-else-if="activeTab === 'ai'" class="pane">
        <div class="ai">
          <h4><Sparkles :size="14" /> AI 可以做什么</h4>
          <ul><li v-for="c in detail.ai.allowed" :key="c">{{ c }}</li></ul>
          <h4>AI 不能做什么</h4>
          <ul class="forbidden"><li v-for="c in detail.ai.forbidden" :key="c">{{ c }}</li></ul>
          <AppButton variant="secondary" @click="ui.openAiPanel(`资产：${detail.name}`)">
            <Sparkles :size="15" /> 打开 AI 分析
          </AppButton>
        </div>
      </div>

      <!-- 类型专属插槽 -->
      <div v-else class="pane">
        <slot :name="`tab-${activeTab}`" :detail="detail" />
      </div>

      <p class="dep-note">{{ detail.pending_dependency_note }}</p>
    </template>

    <template #footer>
      <div class="footer">
        <!-- 借出款：唯一要做的就是登记回款，不该出现「记一笔费用 / 新增估值」 -->
        <AppButton
          v-if="isLend && canConfirm && rec && !rec.settled"
          variant="secondary" @click="switchToLendTab(); openRepay()"
        >
          <Coins :size="15" /> 登记回款
        </AppButton>
        <AppButton
          v-else-if="canCreateDraft && detail && !['disposed', 'scrapped', 'archived'].includes(detail.status)"
          variant="secondary" @click="openExpense"
        >
          <Plus :size="15" /> 记一笔费用
        </AppButton>
        <AppButton v-if="canEdit && !isLend" variant="secondary" @click="openValuation">
          <TrendingUp :size="15" /> 新增估值
        </AppButton>
        <AppButton
          v-if="canEdit && detail?.asset_type === 'real_estate'"
          variant="secondary" @click="openBeike"
        >
          <Building2 :size="15" /> 贝壳估价
        </AppButton>
        <AppButton
          v-if="canDispose && detail && !isLend && !['disposed', 'scrapped', 'archived'].includes(detail.status)"
          variant="secondary" @click="openDispose"
        >
          处置
        </AppButton>
        <!-- 借出款只有「款项结清」之后才给归档入口，否则归档等于把没收回的钱藏起来 -->
        <AppButton
          v-if="canArchive && detail && detail.status !== 'archived'
                && (!isLend || (rec && rec.settled))"
          variant="secondary" @click="openArchive"
        >
          <Archive :size="15" /> 归档
        </AppButton>
        <AppButton variant="primary" @click="emit('close')">关闭</AppButton>
      </div>
    </template>
  </AppDrawer>

  <!-- 新增估值 -->
  <AppModal
    :open="valuationOpen" title="新增估值" confirm-text="保存估值"
    :confirm-loading="valuationSaving" @close="valuationOpen = false" @confirm="saveValuation"
  >
    <div class="form">
      <template v-if="supportsAreaCalc">
        <div class="grid-2">
          <AppField
            v-if="profileArea"
            :label="assetType === 'real_estate' ? '建筑面积（㎡）' : '面积（㎡）'"
            hint="来自资产档案，可在档案里修改"
          >
            <AppInput :model-value="String(profileArea)" disabled />
          </AppField>
          <AppField
            v-else :label="assetType === 'real_estate' ? '建筑面积（㎡）' : '面积（㎡）'"
            hint="档案里还没填面积，这里填一个即可按单价估算（不写回档案）"
          >
            <AppInput v-model="valuationAreaInput" type="number" placeholder="例如：89.5" />
          </AppField>
          <AppField label="市场单价（元/㎡）" hint="按当地市场均价填写，自动算出估值金额">
            <AppInput v-model="valuationUnitPrice" type="number" placeholder="例如：42000" />
          </AppField>
        </div>
        <p class="calc-hint">
          估值金额 = 单价 × 面积
          <template v-if="Number(valuationUnitPrice) > 0">
            = {{ formatAmount(Number(valuationUnitPrice), 2) }} × {{ valuationArea }} =
            <strong>{{ formatAmount(Number(valuationUnitPrice) * valuationArea, ui.amountPrecision) }}</strong>
          </template>
        </p>
      </template>
      <AppField label="估值金额" required :error="valuationErrors.value">
        <AppInput
          v-model="valuationForm.value" type="number" placeholder="0.00"
          @update:model-value="valuationValueTouched = true"
        />
      </AppField>
      <AppField label="估值日期" :error="valuationErrors.valued_at" hint="留空表示当前时间">
        <AppDatePicker v-model="valuationForm.valued_at" value-format="datetime" placeholder="留空为当前时间" />
      </AppField>
      <AppField label="估值来源" required :error="valuationErrors.source">
        <AppSelect v-model="valuationForm.source" :options="sourceOptions" />
      </AppField>
      <AppField label="来源说明" :error="valuationErrors.source_note" hint="市场行情参考时必须填写来源与日期">
        <AppInput v-model="valuationForm.source_note" placeholder="例如：某平台 2026-10 挂牌均价" />
      </AppField>
      <AppField label="调整原因" :error="valuationErrors.reason" hint="调整已确认价值时必填，会写入估值时间线">
        <AppInput v-model="valuationForm.reason" placeholder="例如：第三方评估机构重新评估" />
      </AppField>
      <AppField label="备注">
        <AppInput v-model="valuationForm.note" />
      </AppField>
    </div>
  </AppModal>

  <!-- 贝壳估价 -->
  <AppModal
    :open="beikeOpen" title="贝壳估价（壳e估）"
    :confirm-text="beikeCachedAt ? '重新查询（消耗 1 次额度）' : '查询估价'"
    :confirm-loading="beikeBusy" @close="beikeOpen = false" @confirm="runBeike"
  >
    <div class="form">
      <AppField label="城市" required :error="beikeErrors.city" hint="如：北京市">
        <AppInput v-model="beikeForm.city" placeholder="北京市" />
      </AppField>
      <AppField label="行政区划代码" required :error="beikeErrors.city_code" hint="北京 110000 / 上海 310000">
        <AppInput v-model="beikeForm.city_code" placeholder="110000" />
      </AppField>
      <AppField label="详细地址" required :error="beikeErrors.address">
        <AppInput v-model="beikeForm.address" placeholder="小区 + 楼栋 + 单元 + 房号" />
      </AppField>
      <AppField label="建筑面积（㎡）" required :error="beikeErrors.area">
        <AppInput v-model="beikeForm.area" type="number" placeholder="例如：89.5" />
      </AppField>
    </div>
    <div v-if="beikeQuota" class="beike-quota">
      <strong>贝壳估价额度已用完</strong>
      <p>{{ beikeQuota }}</p>
      <div class="quota-actions">
        <a class="quota-link" href="https://open.ke.com/" target="_blank" rel="noopener noreferrer">
          去贝壳开放平台看额度
        </a>
        <AppButton variant="secondary" size="sm" @click="switchToManualValuation">改用手工估值</AppButton>
      </div>
    </div>

    <div v-if="beikeResult" class="beike-result">
      <p v-if="beikeCachedAt" class="beike-cached">
        <template v-if="Date.now() - beikeCachedAt <= BEIKE_TTL">
          这是 {{ cacheAgeLabel(beikeCachedAt) }} 的估价结果（7 天内同地址不再重复扣额度）
        </template>
        <template v-else>
          这是 {{ cacheAgeLabel(beikeCachedAt) }} 的估价结果，仅供参考；额度恢复后建议重新查询
        </template>
      </p>
      <p><span>参考单价</span><strong>{{ Number(beikeResult.avg_price || 0).toLocaleString('zh-CN') }} 元/㎡</strong></p>
      <p><span>参考总价</span><strong>{{ Number(beikeResult.total_price || 0).toLocaleString('zh-CN') }} 元</strong></p>
      <p><span>价格维度</span><strong>{{ dimLabel(beikeResult.price_dim) }}</strong></p>
      <p v-if="beikeResult.warning" class="beike-warn">{{ beikeResult.warning }}</p>
      <AppButton variant="primary" :loading="beikeApplying" @click="applyBeike">
        采用为当前价值
      </AppButton>
    </div>
  </AppModal>

  <!-- 记一笔费用 -->
  <AppModal
    :open="expenseOpen" title="记一笔费用" :mask-closable="false" :esc-closable="false"
    @close="expenseOpen = false"
  >
    <div class="form">
      <p class="modal-tip">该笔支出会关联到本资产，进入「费用流水」；车辆燃油、保养、维修等费用会同步出现在「保养维修」页签。</p>
      <AppField label="费用标题" required :error="expenseErrors.title">
        <AppInput v-model="expenseForm.title" placeholder="例如：加油 / 保养 / 物业费" />
      </AppField>
      <AppField label="金额" required :error="expenseErrors.amount">
        <AppInput v-model="expenseForm.amount" type="number" placeholder="0.00" />
      </AppField>
      <AppField label="账户" required :error="expenseErrors.account_id">
        <AppSelect
          v-model="expenseForm.account_id"
          :options="[{ value: '', label: '请选择账户' }, ...accounts.filter((a) => a.status === 'active').map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
        />
      </AppField>
      <AppField label="分类" :error="expenseErrors.category_id">
        <AppSelect
          v-model="expenseForm.category_id"
          :options="[{ value: '', label: '未选择分类' }, ...expenseCategories.map((c) => ({ value: c.id, label: c.name }))]"
        />
      </AppField>
      <AppField label="日期" :error="expenseErrors.occurred_at" hint="留空表示当前时间">
        <AppDatePicker v-model="expenseForm.occurred_at" value-format="datetime" placeholder="留空为当前时间" />
      </AppField>
      <AppField label="备注">
        <AppInput v-model="expenseForm.note" />
      </AppField>
    </div>
    <template #footer>
      <AppButton variant="secondary" @click="expenseOpen = false">取消</AppButton>
      <AppButton variant="secondary" :loading="expenseSaving" @click="saveExpense(false)">保存草稿</AppButton>
      <AppButton v-if="canConfirm" variant="primary" :loading="expenseSaving" @click="saveExpense(true)">保存并确认</AppButton>
    </template>
  </AppModal>

  <!-- 处置 -->
  <AppModal
    :open="disposeOpen" title="处置资产" danger confirm-text="确认处置"
    :confirm-loading="disposeSaving" @close="disposeOpen = false" @confirm="saveDispose"
  >
    <div class="form">
      <p class="modal-tip">处置必须填写处置日期、金额、去向账户与原因，并会生成已确认的资产处置事件。</p>
      <AppField label="处置方式" required :error="disposeErrors.dispose_type">
        <AppSelect
          v-model="disposeForm.dispose_type"
          :options="[{ value: 'sold', label: '出售' }, { value: 'scrapped', label: '报废' }]"
        />
      </AppField>
      <AppField label="处置日期" :error="disposeErrors.occurred_at" hint="留空表示当前时间">
        <AppDatePicker v-model="disposeForm.occurred_at" value-format="datetime" placeholder="留空为当前时间" />
      </AppField>
      <AppField label="处置金额" :error="disposeErrors.amount" hint="报废无收入时可填 0">
        <AppInput v-model="disposeForm.amount" type="number" placeholder="0.00" />
      </AppField>
      <AppField label="去向账户" :error="disposeErrors.account_id" hint="金额大于 0 时必须选择">
        <AppSelect
          v-model="disposeForm.account_id"
          :options="[{ value: '', label: '请选择账户' }, ...accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
        />
      </AppField>
      <AppField label="买方 / 回收方" :error="disposeErrors.counterparty">
        <AppInput v-model="disposeForm.counterparty" />
      </AppField>
      <AppField label="处置原因" required :error="disposeErrors.reason">
        <AppInput v-model="disposeForm.reason" placeholder="例如：置换新车 / 到期报废" />
      </AppField>
      <AppField label="备注">
        <AppInput v-model="disposeForm.note" />
      </AppField>
    </div>
  </AppModal>

  <!-- 归档 -->
  <AppModal
    :open="archiveOpen" title="归档资产" danger confirm-text="确认归档"
    :confirm-loading="archiveSaving" @close="archiveOpen = false" @confirm="saveArchive"
  >
    <div class="form">
      <p class="modal-tip">归档不删除任何历史与关联事件，只把资产标记为「已归档」并退出净资产统计。</p>
      <AppField label="归档原因" required>
        <AppInput v-model="archiveReason" placeholder="请填写归档原因" />
      </AppField>
    </div>
  </AppModal>

  <!-- 分期计划 -->
  <AppModal
    :open="scheduleOpen" title="设置分期计划" confirm-text="保存计划"
    :confirm-loading="scheduleSaving" @close="scheduleOpen = false" @confirm="saveSchedule"
  >
    <div class="form">
      <p class="modal-tip">
        每期金额可以不同：按「连续 N 期 × 每期金额」分段填写，系统会按月展开成逐期计划。
        合计必须等于借款本金。
      </p>
      <AppField label="起始月份" required :error="scheduleErrors.start_month"
                hint="第 1 期的应还月份">
        <AppDatePicker v-model="scheduleForm.start_month" value-format="month"
                       placeholder="选择起始月份" />
      </AppField>
      <div class="seg-list">
        <div v-for="(seg, i) in scheduleForm.segments" :key="seg.id" class="seg-row">
          <AppInput v-model="seg.count" type="number" placeholder="期数" />
          <span class="seg-x">期 ×</span>
          <AppInput v-model="seg.amount" type="number" placeholder="每期金额" />
          <button class="seg-del" type="button" :disabled="scheduleForm.segments.length <= 1"
                  @click="removeSegment(i)">删除</button>
        </div>
      </div>
      <AppButton variant="secondary" size="sm" @click="addSegment">+ 加一段</AppButton>
      <p class="sched-preview">
        合计 {{ schedulePreview.count }} 期 · ￥{{ schedulePreview.total.toFixed(2) }}
        <template v-if="Number(rec?.principal) && Math.abs(schedulePreview.total - Number(rec?.principal)) > 0.01">
          <strong class="warn">（应等于本金 {{ rec?.principal }}）</strong>
        </template>
      </p>
    </div>
  </AppModal>

  <!-- 借出款回款 -->
  <AppModal
    :open="repayOpen" title="登记借出款回款" confirm-text="确认回款"
    :confirm-loading="repaySaving" @close="repayOpen = false" @confirm="submitRepay"
  >
    <div class="form">
      <p class="modal-tip">回款只把「应收」变成「现金」，不计收入；未收齐可以分多次登记。</p>
      <AppField label="回款性质"
                hint="对方早就还过、钱也已经花掉了的，选「补录历史回款」——只冲减应收，不会把当前账户余额冲高">
        <AppSwitch v-model="repayForm.affects_account" label="这次回款进入当前账户" />
        <p v-if="!repayForm.affects_account" class="hist-note">
          补录历史回款：不生成资金流水、不改变账户余额，也不会重复计入收入。
        </p>
      </AppField>
      <AppField label="回款金额" required :error="repayErrors.amount"
                :hint="rec?.interest_enabled ? '含利息的实收总额' : ''">
        <AppInput v-model="repayForm.amount" type="number" placeholder="0.00" />
      </AppField>
      <!-- 约定的计息借出：可把回款里的利息单独算出来，利息计入收入 -->
      <AppField v-if="rec?.interest_enabled" label="其中利息（选填）" :error="repayErrors.interest_part"
                hint="填了会把利息单独记成收入（投资收益 · 利息收入），其余冲减本金">
        <AppInput v-model="repayForm.interest_part" type="number" placeholder="例如：1250" />
      </AppField>
      <AppField v-if="repayForm.affects_account" label="到账账户" required :error="repayErrors.account_id">
        <AppSelect
          v-model="repayForm.account_id"
          :options="[{ value: '', label: '请选择账户' }, ...accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
        />
      </AppField>
      <AppField label="到账日期" :error="repayErrors.occurred_at">
        <AppDatePicker v-model="repayForm.occurred_at" value-format="datetime" placeholder="留空为今天" />
      </AppField>
      <AppField label="备注">
        <AppInput v-model="repayForm.note" placeholder="例如：微信转账 / 现金归还" />
      </AppField>
    </div>
  </AppModal>

  <!-- 车辆保险 -->
  <AppModal
    :open="insuranceOpen" :title="editingInsuranceId ? '编辑保险' : '新增保险'"
    confirm-text="保存" :confirm-loading="insuranceSaving"
    @close="insuranceOpen = false" @confirm="saveInsurance"
  >
    <div class="form">
      <AppField label="保险类型" required :error="insuranceErrors.insurance_type">
        <AppSelect v-model="insuranceForm.insurance_type" :options="insuranceTypeOptions" />
      </AppField>
      <AppField label="保险公司" :error="insuranceErrors.insurer">
        <AppInput v-model="insuranceForm.insurer" />
      </AppField>
      <AppField label="保单号" :error="insuranceErrors.policy_no">
        <AppInput v-model="insuranceForm.policy_no" />
      </AppField>
      <AppField label="保险期间（生效 / 到期）">
        <AppDateRangePicker
          v-model:start="insuranceForm.effective_date" v-model:end="insuranceForm.expiry_date"
          placeholder="选择生效与到期日期"
        />
      </AppField>
      <AppField label="保费 / 保额 / 免赔额">
        <div class="grid-3">
          <AppInput v-model="insuranceForm.premium" type="number" placeholder="保费" />
          <AppInput v-model="insuranceForm.coverage" type="number" placeholder="保额" />
          <AppInput v-model="insuranceForm.deductible" type="number" placeholder="免赔额" />
        </div>
      </AppField>
      <AppField label="被保险人 / 受益人">
        <div class="grid-2">
          <AppInput v-model="insuranceForm.insured_person" placeholder="被保险人" />
          <AppInput v-model="insuranceForm.beneficiary" placeholder="受益人" />
        </div>
      </AppField>
      <AppField label="续保状态">
        <AppSelect v-model="insuranceForm.renewal_status" :options="renewalOptions" />
      </AppField>
      <AppField label="提醒日期" hint="到期提醒按此日期触发">
        <AppDatePicker v-model="insuranceForm.remind_date" placeholder="选择提醒日期" />
      </AppField>
      <AppField label="备注">
        <AppInput v-model="insuranceForm.note" />
      </AppField>
    </div>
  </AppModal>

  <!-- 水电煤户号 -->
  <AppModal
    :open="utilityOpen" :title="editingUtilityId ? '编辑户号' : '新增户号'"
    confirm-text="保存" :confirm-loading="utilitySaving"
    @close="utilityOpen = false" @confirm="saveUtility"
  >
    <div class="form">
      <AppField label="类型" required :error="utilityErrors.utility_type">
        <AppSelect v-model="utilityForm.utility_type" :options="utilityTypeOptions" />
      </AppField>
      <AppField label="户号" :error="utilityErrors.account_no" hint="每 4 位自动分组，便于核对">
        <AppInput v-model="utilityForm.account_no" placeholder="如 1234 5678 9012" />
      </AppField>
      <AppField label="户名" :error="utilityErrors.account_name">
        <AppInput v-model="utilityForm.account_name" />
      </AppField>
    </div>
  </AppModal>
</template>

<style scoped>
/* 租赁卡片 */
.lease-card {
  padding: 12px 14px; border-radius: 12px;
  border: 1px solid var(--border); background: var(--glass-1);
  margin-bottom: 10px;
}
.lease-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.lease-head strong { font-size: 14px; color: var(--ink-strong); }
.lease-no { margin-left: auto; font-size: 11.5px; color: var(--ink-muted); }
.lease-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 8px 14px; margin: 0;
}
.lease-grid div { display: flex; flex-direction: column; gap: 2px; }
.lease-grid dt { font-size: 11.5px; color: var(--ink-muted); }
.lease-grid dd { margin: 0; font-size: 13px; color: var(--ink-strong); font-variant-numeric: tabular-nums; white-space: nowrap; }
.lease-grid dd.warn { color: var(--warning, #b26a00); }
.dep-tag { margin-left: 4px; font-size: 11.5px; }
.dep-tag.ok { color: var(--success, #19875a); }
.dep-tag.muted, .lease-utils .muted { color: var(--ink-muted); }
.lease-utils { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; font-size: 12px; }
.util-chip {
  padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--glass-2); color: var(--ink);
}

/* 分期计划表 */
.sched-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.sched-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sched-sum { font-size: 11.5px; color: var(--ink-muted); }
.sched-sum .warn { color: var(--warning, #b26a00); }
.sched-empty { margin: 6px 0 0; padding: 9px 11px; border: 1px dashed var(--border); border-radius: 10px;
               font-size: 12px; line-height: 1.55; color: var(--ink-muted); background: var(--glass-1); }
.sched-wrap { margin-top: 8px; max-height: 320px; overflow: auto; border: 1px solid var(--border); border-radius: 10px; }
.sched-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.sched-table th { position: sticky; top: 0; background: var(--glass-2); padding: 7px 9px; text-align: left;
                  font-weight: 600; color: var(--ink-muted); font-size: 11.5px; white-space: nowrap; }
.sched-table td { padding: 6px 9px; border-top: 1px solid var(--border); color: var(--ink-strong); white-space: nowrap; }
.sched-table .ta-r { text-align: right; }
.sched-table tr.p-paid td { color: var(--ink-muted); }
.sched-table tr.p-paid .p-tag { color: #15803d; }
.sched-table tr.p-partial .p-tag { color: #b26a00; }
.sched-table tr.p-overdue td { background: color-mix(in srgb, var(--danger) 6%, transparent); }
.sched-table tr.p-overdue .p-tag { color: var(--danger); font-weight: 600; }
.p-tag { font-size: 11.5px; }
.seg-list { display: flex; flex-direction: column; gap: 8px; margin: 8px 0; }
.seg-row { display: flex; align-items: center; gap: 6px; }
.seg-row > :first-child { width: 84px; }
.seg-row > :nth-child(3) { flex: 1; }
.seg-x { font-size: 12px; color: var(--ink-muted); flex: none; }
.seg-del { flex: none; padding: 4px 8px; font-size: 11.5px; color: var(--ink-muted);
           background: none; border: 1px solid var(--border); border-radius: 7px; cursor: pointer; }
.seg-del:disabled { opacity: 0.4; cursor: not-allowed; }
.sched-preview { margin: 8px 0 0; font-size: 12.5px; color: var(--ink-strong); }
.sched-preview .warn { color: var(--warning, #b26a00); }

.hist-note { margin-top: 6px; font-size: 11.5px; line-height: 1.5; color: var(--ink-muted); }
/* 借出款对账面板 */
.recon { margin: 4px 0 10px; padding: 11px 13px; border-radius: 12px;
         border: 1px solid var(--border); background: var(--glass-1); }
.recon.st-overdue { border-color: color-mix(in srgb, var(--danger) 40%, var(--border)); }
.recon.st-behind { border-color: color-mix(in srgb, var(--warning) 45%, var(--border)); }
.recon-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.recon-head small { font-size: 11.5px; color: var(--ink-muted); }
.bars { display: flex; flex-direction: column; gap: 6px; margin: 10px 0 8px; }
.bar-row { display: flex; align-items: center; gap: 8px; }
.bl { width: 30px; font-size: 11px; color: var(--ink-muted); flex: none; }
.bar { flex: 1; height: 7px; border-radius: 999px; background: var(--track, rgba(148,163,184,0.22)); overflow: hidden; }
.fill { display: block; height: 100%; border-radius: 999px; transition: width 0.35s ease; }
.fill.paid { background: linear-gradient(90deg, #22c55e, #4ade80); }
.fill.time { background: linear-gradient(90deg, #94a3b8, #cbd5e1); }
.bv { width: 44px; text-align: right; font-size: 11px; color: var(--ink-muted); flex: none; }
.recon-facts { margin: 8px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 3px; }
.recon-facts li { display: flex; justify-content: space-between; gap: 10px; font-size: 12.5px; }
.recon-facts .k { color: var(--ink-muted); }
.recon-facts .v { color: var(--ink-strong); }
.recon-facts .v.warn { color: var(--warning, #b26a00); }
.recon-tip { margin: 8px 0 0; font-size: 11px; color: var(--ink-muted); line-height: 1.5; }
.period-tag.hist { color: var(--warning, #b26a00); }
.period-tag { display: inline-block; margin-right: 5px; padding: 1px 6px; border-radius: 6px;
              font-size: 10.5px; font-weight: 600; color: var(--ink-muted); background: var(--glass-2); }

.sub { margin-left: 2px; font-size: 11.5px; color: var(--ink-muted); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.calc-hint {
  margin: -4px 0 2px; font-size: 12px; line-height: 1.6; color: var(--ink-muted);
}
.calc-hint strong { color: var(--ink-strong); font-variant-numeric: tabular-nums; }
@media (max-width: 640px) { .grid-2 { grid-template-columns: 1fr; } }
.head { margin-bottom: var(--space-3); }
.head .row { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.value { display: flex; align-items: baseline; gap: 8px; font-size: 13px; color: var(--ink-muted); }
.value :deep(.money) { font-size: 22px; font-weight: 600; }
.meta { margin-top: 6px; font-size: 12.5px; color: var(--ink-muted); }
.tabs { display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid var(--border); margin-bottom: var(--space-3); }
.tab {
  flex: none; padding: 8px 12px; border: none; background: transparent; cursor: pointer;
  font-size: 13px; color: var(--ink-muted); border-bottom: 2px solid transparent;
}
.tab.is-active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 500; }
.pane { display: flex; flex-direction: column; gap: var(--space-3); }
.pane-actions { display: flex; justify-content: flex-end; }
.kv { display: flex; flex-direction: column; }
.kv li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 8px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.kv .k { color: var(--ink-muted); flex: none; }
.kv .v { color: var(--ink-strong); text-align: right; word-break: break-all; }
.kv .v.strong { font-size: 15px; font-weight: 600; }
.list { display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.l-main { display: flex; flex-direction: column; min-width: 0; gap: 2px; }
.l-main strong { font-size: 13.5px; color: var(--ink-strong); }
.l-main small { font-size: 11.5px; color: var(--ink-muted); }
.l-main small.reason { color: var(--primary); }
.l-main small.warn { color: var(--warning, #d97706); }
.l-main small.danger { color: var(--danger); }
.l-amount { font-variant-numeric: tabular-nums; font-size: 13.5px; white-space: nowrap; }
.l-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.act {
  display: inline-flex; align-items: center; gap: 4px; height: 26px; padding: 0 8px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1);
  color: var(--ink-strong); font-size: 12px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.6; }
.ai { display: flex; flex-direction: column; gap: 10px; }
.ai h4 { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul.forbidden li { color: var(--ink-muted); }
.custom h4 { font-size: 13px; color: var(--ink-strong); margin-bottom: 6px; }
.custom pre { padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); font-size: 12px; overflow: auto; }
.dep-note { margin-top: var(--space-4); font-size: 12px; color: var(--ink-muted); line-height: 1.6; }
.kv.loan { border: 1px solid var(--border); border-radius: var(--glass-radius-control); padding: 4px 12px; background: var(--glass-1); }
.footer { display: flex; gap: var(--space-2); justify-content: flex-end; flex-wrap: wrap; }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.modal-tip { font-size: 12.5px; color: var(--ink-muted); line-height: 1.6; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
@media (max-width: 700px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
.beike-quota {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  border: 1px solid rgba(240, 173, 78, 0.45);
  background: rgba(240, 173, 78, 0.1);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink);
}
.beike-quota strong { display: block; margin-bottom: 4px; font-size: 13px; color: var(--warning); }
.beike-quota p { margin: 0; }
.quota-actions { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.quota-link {
  font-size: 12.5px;
  color: var(--primary);
  text-decoration: none;
  border-bottom: 1px dashed currentColor;
}
.beike-cached { margin: 0 0 8px; font-size: 11.5px; color: var(--ink-muted); }
.beike-result {
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--glass-2);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.beike-result p { display: flex; justify-content: space-between; margin: 0; font-size: 13px; color: var(--ink); }
.beike-result strong { color: var(--ink-strong); }
.beike-warn { color: var(--warning) !important; }

.rec-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }
.rec-stats > div { padding: 10px 12px; border-radius: var(--radius-md); background: var(--glass-1); border: 1px solid var(--border); }
.rec-stats .k { display: block; font-size: 11.5px; color: var(--ink-muted); }
.rec-stats strong { font-size: 18px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.rec-stats .ok { color: var(--success); }
.rec-stats .warn { color: var(--warning); }
.rec-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 10px 0 4px; }
.rec-tip { font-size: 11.5px; color: var(--ink-muted); }
.rec-h { margin: 14px 0 6px; font-size: 13px; color: var(--ink-strong); }
.l-note { font-size: 11.5px; color: var(--ink-muted); }
@media (max-width: 640px) { .rec-stats { grid-template-columns: 1fr; } }
</style>
