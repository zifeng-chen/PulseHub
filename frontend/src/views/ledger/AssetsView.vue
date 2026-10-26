<script setup lang="ts">
/**
 * 资产页面 /ledger/assets（06 §2、15）。
 * 顶部统计：资产总值、流动资产、非流动资产、待估值资产；
 * 工具栏：搜索、资产类型、成员、状态、估值日期、AI 分析、新增资产；
 * 列表用卡片展示（与账户页一致），点卡片在当前页打开详情抽屉，不再跳到子页面。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Building2, Coins, HandCoins, Plus, RefreshCw, Search, Sparkles, Wallet,
} from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import {
  emptyUtilities, finalizeAssetCreate, uploadAssetFiles, type UtilityDraft,
} from './asset-create-flow'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppToolbar from '@/components/ui/AppToolbar.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppDateRangePicker from '@/components/ui/AppDateRangePicker.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import AssetAttachments from '@/components/asset/AssetAttachments.vue'
import AssetBaseFields from '@/components/asset/AssetBaseFields.vue'
import ReceivableFields from '@/components/asset/ReceivableFields.vue'
import ResidenceFields from '@/components/asset/ResidenceFields.vue'
import ParkingFields from '@/components/asset/ParkingFields.vue'
import VehicleFields from '@/components/asset/VehicleFields.vue'
import MetalFields from '@/components/asset/MetalFields.vue'
import AssetCard from '@/components/asset/AssetCard.vue'
import AssetDetailDrawer from '@/components/asset/AssetDetailDrawer.vue'
import {
  ASSET_STATUS_VALUES, ASSET_TABS, ASSET_TYPE_VALUES,
  type AccountLite, type AssetCategory, type AssetRow, type AssetSummary, type Member,
} from './asset-shared'

const route = useRoute()
const router = useRouter()
const queryHandled = ref(false)
const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const rows = ref<AssetRow[]>([])
const summary = ref<AssetSummary | null>(null)
const members = ref<Member[]>([])
const accounts = ref<AccountLite[]>([])
const categories = ref<AssetCategory[]>([])

const keyword = ref('')
const typeFilter = ref('')
const categoryFilter = ref('')
const memberFilter = ref('')
const statusFilter = ref('')
const liquidityFilter = ref('')
const valuationFrom = ref('')
const valuationTo = ref('')

/** 待估值筛选：点「待估值资产」卡片直接只看这些资产 */
const pendingOnly = ref(false)
/** 待估值筛选是唯一会即时改变查询条件的开关：在点击处显式加载，避免重置时触发两次请求 */
function togglePendingOnly() {
  if (!summary.value?.pending_valuation_count) return
  pendingOnly.value = !pendingOnly.value
  void load()
}

/** 移动端筛选按钮上的角标：收进弹层的条件里有几个生效 */
const assetFilterCount = computed(() => [
  memberFilter.value, statusFilter.value, liquidityFilter.value,
  valuationFrom.value, valuationTo.value,
].filter(Boolean).length)

function resetAssetFilters() {
  memberFilter.value = ''
  statusFilter.value = ''
  liquidityFilter.value = ''
  valuationFrom.value = ''
  valuationTo.value = ''
  pendingOnly.value = false
  // 这里只改值，不触发额外请求；下面统一加载一次
  void load()
}

const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const detailOpen = ref(false)
const detailId = ref('')
const detailType = ref('other')

const form = reactive({
  id: '', version: 1, asset_type: 'real_estate', name: '', sub_type: '', category_id: '', currency: 'CNY',
  owner_member_id: '', acquire_date: '', acquire_cost: '', current_value: '',
  value_source: '', depreciation_method: '', maintenance_date: '', linked_account_id: '',
  include_in_net_worth: true, note: '', reason: '',
  acquisition_mode: 'none', acquisition_account: '', acquisition_amount: '',
})
const extForm = reactive<Record<string, any>>({})
const receivable = reactive({
  borrower_name: '', borrower_id_no: '', lend_type: 'cash', principal: '', lent_at: '',
  lent_mode: 'account', interest_enabled: false, interest_rate: '',
  repayment_method: 'lump_sum', due_date: '', fee_bearer: 'borrower', fee_amount: '',
  source_account_id: '', include_in_net_worth: true, note: '',
})
const utilities = reactive<UtilityDraft>(emptyUtilities())
const pendingFiles = ref<File[]>([])

/**
 * 类型切换（含由分类联动带出的类型）时补齐扩展字段默认值：
 * 否则 real_estate 的必填项「房产类型」为空，提交会被后端拦下。
 */
watch(() => form.asset_type, (type) => {
  if (type === 'real_estate' && !extForm.property_type) extForm.property_type = 'residence'
  if (type === 'parking' && !extForm.parking_type) extForm.parking_type = 'ownership'
  if (type === 'precious_metal') {
    // 品类已由资产分类表达；这里只补计价方式与计量单位
    if (!extForm.pricing_mode) extForm.pricing_mode = 'by_weight'
    if (!extForm.unit) extForm.unit = 'g'
  }
})

const canCreate = computed(() => auth.can('create_draft'))
const canEdit = computed(() => auth.can('edit_draft'))

const typeOptions = [{ value: '', label: '全部类型' }, ...ASSET_TYPE_VALUES.filter((v) => v !== 'parking').map((v) => ({ value: v, label: zh('asset_type', v) }))]
const categoryOptions = computed(() => [
  { value: '', label: '全部分类' },
  ...categories.value.map((c) => ({ value: c.id, label: c.name })),
])
const statusOptions = [{ value: '', label: '全部状态' }, ...ASSET_STATUS_VALUES.map((v) => ({ value: v, label: zh('asset_status', v) }))]
const liquidityOptions = [
  { value: '', label: '全部流动性' },
  { value: 'current', label: '流动资产' },
  { value: 'non_current', label: '非流动资产' },
]
const memberOptions = computed(() => [
  { value: '', label: '全部成员' },
  ...members.value.map((m) => ({ value: m.id, label: m.display_name })),
])
const accountOptions = computed(() => [
  { value: '', label: '请选择账户' },
  ...accounts.value.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` })),
])

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const [list, household, cats] = await Promise.all([
      api.get<{ items: AssetRow[]; summary: AssetSummary }>('/ledger/assets', {
        keyword: keyword.value || undefined,
        asset_type: typeFilter.value || undefined,
        category_id: categoryFilter.value || undefined,
        member_id: memberFilter.value || undefined,
        status: statusFilter.value || undefined,
        liquidity: liquidityFilter.value || undefined,
        valuation_from: valuationFrom.value || undefined,
        valuation_to: valuationTo.value || undefined,
        pending_valuation: pendingOnly.value || undefined,
      }),
      api.get<{ members: Member[] }>('/settings/household'),
      api.get<{ items: AssetCategory[] }>('/settings/categories', { domain: 'asset' }),
    ])
    if (seq !== loadSeq) return
    rows.value = list.items
    summary.value = list.summary
    members.value = household.members || []
    categories.value = (cats.items || []).filter((c) => c.status === 'active')
    if (!accounts.value.length) {
      const acc = await api.get<{ items: AccountLite[] }>('/ledger/accounts')
      if (seq !== loadSeq) return
      accounts.value = (acc.items || []).filter((a) => a.status === 'active')
    }
    // 从智能录入带过来的房产证识别结果：直接填表
    if (!queryHandled.value && route.query.ai) {
      queryHandled.value = true
      const raw = sessionStorage.getItem('fam.asset.prefill')
      sessionStorage.removeItem('fam.asset.prefill')
      if (raw) {
        try { applyCertPrefill(JSON.parse(raw)) } catch { /* 忽略损坏的预填数据 */ }
      }
      void router.replace({ path: route.path })
    }
    // 从台账总览「借出款」直达：打开新增并预选分类
    if (!queryHandled.value && route.query.new) {
      queryHandled.value = true
      openCreate('other')
      const wanted = String(route.query.category || '')
      const target = categories.value.find((c) => c.name === wanted)
      if (target) {
        // 「借出款」是父分类：默认选中「现金出借」，用户可以再换成信用卡/电子钱包
        const child = categories.value.find((c) => c.parent_id === target.id)
        form.category_id = child ? child.id : target.id
      }
      void router.replace({ path: route.path })
    }
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function resetExt() {
  Object.keys(extForm).forEach((k) => delete extForm[k])
}

/**
 * 「记一笔借出款」快捷入口。
 *
 * 借出款是资产类型 other 下的一级分类（applies_to=other），而「新增资产」默认类型是不动产，
 * 分类下拉会按类型过滤，导致用户根本看不到「借出款」——这是之前找不到入口的原因。
 * 这里直接带上类型与分类，打开就是借出款表单。
 */
function openCreateReceivable() {
  openCreate('other')
  const root = categories.value.find((c) => c.name === '借出款' && !c.parent_id)
  if (root) form.category_id = root.id
}

function openCreate(preType?: string) {
  fieldErrors.value = {}
  resetExt()
  Object.assign(form, {
    id: '', version: 1, asset_type: preType || 'real_estate', name: '', sub_type: '', category_id: '', currency: 'CNY',
    owner_member_id: '', acquire_date: '', acquire_cost: '', current_value: '',
    value_source: '', depreciation_method: '', maintenance_date: '', linked_account_id: '',
    include_in_net_worth: true, note: '', reason: '',
    acquisition_mode: 'none', acquisition_account: '', acquisition_amount: '',
  })
  if (preType === 'parking') extForm.parking_type = 'ownership'
  if (preType === 'real_estate') extForm.property_type = 'residence'
  if (preType === 'precious_metal') {
    extForm.pricing_mode = 'by_weight'
    extForm.unit = 'g'
    extForm.purity = '999.9'
  }
  resetReceivable()
  Object.assign(utilities, emptyUtilities())
  pendingFiles.value = []
  editorMode.value = 'create'
  editorOpen.value = true
}

async function openEdit(row: AssetRow) {
  fieldErrors.value = {}
  resetExt()
  Object.assign(form, {
    id: row.id, version: row.version, asset_type: row.asset_type, name: row.name,
    sub_type: row.sub_type || '', category_id: row.category_id || '', currency: row.currency,
    owner_member_id: row.owner_member_id || '',
    acquire_date: row.acquire_date ? row.acquire_date.slice(0, 10) : '',
    acquire_cost: row.acquire_cost, current_value: row.current_value,
    value_source: row.value_source || '', depreciation_method: row.depreciation_method || '',
    maintenance_date: row.maintenance_date ? row.maintenance_date.slice(0, 10) : '',
    linked_account_id: row.linked_account_id || '', include_in_net_worth: row.include_in_net_worth,
    note: row.note || '', reason: '', acquisition_mode: 'none', acquisition_account: '', acquisition_amount: '',
  })
  Object.assign(extForm, row.extension || {})
  resetReceivable()
  Object.assign(utilities, emptyUtilities())
  pendingFiles.value = []
  editorMode.value = 'edit'
  editorOpen.value = true

  // 借出款明细只在详情接口里：列表项没有，不拉一次编辑表单就是空的
  if (isReceivable.value) {
    try {
      const detail = await api.get<Record<string, any>>(`/ledger/assets/${row.id}`)
      const rec = (detail.related || {}).receivable
      if (rec) {
        Object.assign(receivable, {
          borrower_name: rec.borrower_name || '',
          // 回填「前 6 后 6」掩码值，方便确认是谁；后端识别星号即视为不修改
          borrower_id_no: rec.borrower_id_no || '',
          lend_type: rec.lend_type || 'cash',
          lent_mode: rec.lent_mode || 'account',
          principal: rec.principal || '',
          lent_at: (rec.lent_at || '').slice(0, 10),
          repayment_method: rec.repayment_method || 'lump_sum',
          due_date: (rec.due_date || '').slice(0, 10),
          interest_enabled: Boolean(rec.interest_enabled),
          interest_rate: rec.interest_rate || '',
          fee_bearer: rec.fee_bearer || 'borrower',
          fee_amount: rec.fee_amount || '',
          source_account_id: rec.source_account_id || '',
          include_in_net_worth: row.include_in_net_worth,
          note: rec.note || '',
        })
      }
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }
}

const EXT_TYPE = computed(() => (editorMode.value === 'edit' ? form.asset_type : form.asset_type))

/** 不动产（住宅 / 车位）走精简表单：只录证件、面积、价格与登记方式 */
const isRealEstate = computed(() => form.asset_type === 'real_estate')

/** 当前选中分类名：贵金属据此判断是否「投资金条」（只有金条才需要证书编号） */
const selectedCategoryName = computed(
  () => categories.value.find((c) => c.id === form.category_id)?.name || '',
)

/** 把识别结果填进新增表单（用户核对后再保存） */
function applyCertPrefill(fields: Record<string, string>) {
  openCreate('real_estate')
  const cat = categories.value.find((c) => c.name === '住宅')
  if (cat) form.category_id = cat.id
  form.name = fields.name || '房产'
  const map: Record<string, string> = {
    address: 'address', building_area: 'building_area', inner_area: 'inner_area',
    ownership_no: 'ownership_no', title_no: 'title_no',
  }
  for (const [src, dst] of Object.entries(map)) {
    if (fields[src]) extForm[dst] = fields[src]
  }
}

/** 借出款：一级分类「借出款」及其下的现金/信用卡/电子钱包等子分类 */
const isReceivable = computed(() => {
  const cat = categories.value.find((c) => c.id === form.category_id)
  if (!cat) return false
  if (cat.name === '借出款') return true
  return categories.value.find((c) => c.id === cat.parent_id)?.name === '借出款'
})

const LEND_TYPE_BY_NAME: Record<string, string> = {
  现金出借: 'cash', 银行卡出借: 'bank_card', 信用卡出借: 'credit_card',
  电子钱包出借: 'e_wallet', 其他出借: 'other',
}

function resetReceivable() {
  Object.assign(receivable, {
    borrower_name: '', borrower_id_no: '', lend_type: 'cash', principal: '', lent_at: '',
    lent_mode: 'account', interest_enabled: false, interest_rate: '',
    repayment_method: 'lump_sum', due_date: '', fee_bearer: 'borrower', fee_amount: '',
    source_account_id: '', include_in_net_worth: true, note: '',
  })
}

// 选「信用卡出借 / 电子钱包出借」等子分类时自动带出出借方式
watch(() => form.category_id, (id) => {
  if (!id) return
  const cat = categories.value.find((c) => c.id === id)
  if (!cat) return
  const mapped = LEND_TYPE_BY_NAME[cat.name]
  if (mapped) receivable.lend_type = mapped
})

// 住宅没让用户填「当前价值」：以购入价格为准；填了参考单价则按 面积 × 单价 估算
watch([() => form.acquire_cost, () => extForm.unit_price, () => extForm.building_area], () => {
  if (editorMode.value !== 'create' || !isRealEstate.value) return
  const unitPrice = Number(extForm.unit_price || 0)
  const area = Number(extForm.building_area || 0)
  form.current_value = unitPrice > 0 && area > 0
    ? (unitPrice * area).toFixed(2)
    : form.acquire_cost
})

async function save() {
  saving.value = true
  fieldErrors.value = {}
  const body: Record<string, unknown> = {
    asset_type: form.asset_type,
    name: form.name,
    sub_type: form.sub_type || null,
    category_id: form.category_id || null,
    currency: form.currency,
    owner_member_id: form.owner_member_id || null,
    acquire_date: form.acquire_date || null,
    acquire_cost: form.acquire_cost || '0',
    current_value: form.current_value || null,
    value_source: form.value_source || null,
    depreciation_method: form.depreciation_method || null,
    maintenance_date: form.maintenance_date || null,
    linked_account_id: form.linked_account_id || null,
    include_in_net_worth: form.include_in_net_worth,
    note: form.note || null,
    extension: isReceivable.value ? {} : { ...extForm },
  }
  if (isReceivable.value) {
    // 借出款：本金即资产价值；资金来源账户必填，借出后账户减少、应收增加
    body.acquire_cost = receivable.principal || '0'
    body.current_value = receivable.principal || '0'
    body.acquire_date = receivable.lent_at || null
    body.include_in_net_worth = receivable.include_in_net_worth
    body.note = (receivable.note || '').trim() || null
    body.receivable = { ...receivable, principal: receivable.principal || '0' }
    // 补录既有借出：钱早就出去了，不生成资金事件，否则会把现在的账户余额再扣一次
    const lentFromHere = (receivable.lent_mode || 'account') === 'account'
    body.acquisition = lentFromHere
      ? {
          mode: 'confirm',
          account_id: receivable.source_account_id || null,
          amount: receivable.principal || '0',
        }
      : { mode: 'none' }
  }
  try {
    if (editorMode.value === 'create') {
      if (!isReceivable.value && form.acquisition_mode !== 'none') {
        body.acquisition = {
          mode: form.acquisition_mode,
          account_id: form.acquisition_account || null,
          amount: form.acquisition_amount || form.acquire_cost || '0',
        }
      }
      const created = await api.post<AssetRow>('/ledger/assets', body)
      toast.success('资产已创建')
      editorOpen.value = false
      const extra = await finalizeAssetCreate({
        assetId: created.id,
        assetType: created.asset_type,
        assetName: created.name,
        pendingFiles: pendingFiles.value,
        utilities,
        hasMortgage: Boolean(extForm.has_mortgage),
        hasLoan: Boolean(extForm.has_loan),
        router,
      })
      pendingFiles.value = []
      if (extra.failedFiles) toast.error(`${extra.failedFiles} 个附件上传失败，可在资产详情里重新上传`)
      if (extra.failedUtilities) toast.error(`${extra.failedUtilities} 个户号未保存，可在资产详情里补充`)
      if (extra.loanType) {
        toast.success('已跳转到贷款信息填写，请补充金额与还款计划')
        return
      }
      await load()
      return
    } else {
      body.version = form.version
      if (form.reason) body.reason = form.reason
      await api.put(`/ledger/assets/${form.id}`, body)
      toast.success('资产已更新')
      // 编辑态识别房产证时照片先暂存在本地，保存成功后补传
      if (pendingFiles.value.length) {
        const failed = await uploadAssetFiles(form.id, pendingFiles.value)
        pendingFiles.value = []
        if (failed) toast.error(`${failed} 个附件上传失败，可在详情里重新上传`)
      }
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

function openDetail(row: AssetRow) {
  // 不跳子页面：子页面只是按类型筛选的同一种详情，跳转反而让人以为换了一层
  detailId.value = row.id
  detailType.value = row.asset_type
  detailOpen.value = true
}

/** 详情抽屉的页签跟着资产类型走（不动产有估值/水电煤，车辆有保险…） */
const detailTabs = computed(() => ASSET_TABS[detailType.value] || ASSET_TABS.other)

onMounted(load)
</script>

<template>
  <div class="assets">
    <PageHeader
      title="资产"
      subtitle="资产当前价值由资产主表提供；净资产纳入「计入净资产 且 未出售/未报废/未归档」的资产。"
      :breadcrumb="['家庭台账', '资产']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiAnalysis('assets', 'AI 资产分析')">
        <Sparkles :size="15" /> AI 分析
      </AppButton>
      <AppButton v-if="canCreate" variant="secondary" @click="openCreateReceivable">
        <HandCoins :size="15" /> 记一笔借出款
      </AppButton>
      <AppButton v-if="canCreate" variant="primary" @click="openCreate()">
        <Plus :size="15" /> 新增资产
      </AppButton>
    </PageHeader>

    <section v-if="summary" class="kpi-row">
      <StatCard
        :icon="Wallet" tone="primary" :label="`资产总值（${summary.base_currency}）`"
        :value="summary.total_value" :currency="summary.base_currency"
        :hint="`共 ${summary.count} 项 · 仅统计计入净资产的资产`"
      />
      <StatCard
        :icon="Coins" tone="success" label="流动资产"
        :value="summary.current_value" :currency="summary.base_currency"
        hint="现金、存款、可快速变现的部分"
      />
      <StatCard
        :icon="Building2" tone="ai" label="非流动资产"
        :value="summary.non_current_value" :currency="summary.base_currency"
        hint="不动产、车辆、贵金属等"
      />
      <StatCard
        :icon="HandCoins" tone="warning" label="待估值资产"
        :value="summary.pending_valuation_count" plain unit="项"
        hint="当前价值为空或从未登记估值"
        :clickable="Boolean(summary.pending_valuation_count)"
        :active="pendingOnly"
        @click="togglePendingOnly"
      >
        <template #label-extra>
          <span v-if="summary.pending_valuation_count" class="kpi-hint">{{ pendingOnly ? '已筛选' : '点击查看' }}</span>
        </template>
      </StatCard>
    </section>
    <p v-if="pendingOnly" class="filter-hint">
      正在只看待估值资产（共 {{ rows.length }} 项）：这些资产的当前价值为空或从未估值，点卡片进详情补一条估值即可。
      <button class="link" type="button" @click="pendingOnly = false">清除筛选</button>
    </p>
    <p v-if="summary && summary.multi_currency" class="multi">
      存在多币种资产，顶部统计仅展示基础币种 {{ summary.base_currency }}；不同币种不做相加。
    </p>

    <AppCard>
      <template #header>
        <AppToolbar
          :active-count="assetFilterCount" sheet-title="资产筛选"
          @reset="resetAssetFilters" @apply="load"
        >
          <div class="search">
            <Search :size="15" />
            <input v-model="keyword" placeholder="搜索资产名称" @keyup.enter="load" />
          </div>
          <AppSelect v-model="typeFilter" :options="typeOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="categoryFilter" :options="categoryOptions" class="sel" @update:model-value="load" />
          <template #advanced>
            <AppSelect v-model="memberFilter" :options="memberOptions" class="sel" @update:model-value="load" />
            <AppSelect v-model="statusFilter" :options="statusOptions" class="sel" @update:model-value="load" />
            <AppSelect v-model="liquidityFilter" :options="liquidityOptions" class="sel" @update:model-value="load" />
            <AppDateRangePicker
              v-model:start="valuationFrom" v-model:end="valuationTo"
              class="period" placeholder="估值期间" @change="load"
            />
            <AppButton variant="secondary" @click="load">筛选</AppButton>
          </template>
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
      <StateBlock
        v-else-if="!rows.length"
        state="empty" title="还没有资产"
        description="登记不动产、车位、车辆、贵金属或其他资产后，这里会显示资产总值与估值时间线。"
      >
        <template #actions>
          <AppButton v-if="canCreate" variant="primary" @click="openCreate()">
            <Plus :size="15" /> 新增资产
          </AppButton>
          <AppButton v-if="canCreate" variant="secondary" @click="openCreateReceivable">
            <HandCoins :size="15" /> 记一笔借出款
          </AppButton>
        </template>
      </StateBlock>

      <div v-else class="card-grid">
        <AssetCard
          v-for="row in rows"
          :key="row.id"
          :row="row"
          :can-edit="canEdit"
          @open="openDetail(row)"
          @edit="openEdit(row)"
        />
      </div>
    </AppCard>

    <!-- 新增 / 编辑抽屉 -->
    <AppDrawer
      :open="editorOpen" :title="editorMode === 'create' ? '新增资产' : '编辑资产'" :width="560"
      @close="editorOpen = false"
    >
      <div class="form">
        <AssetBaseFields
          :form="form" :errors="fieldErrors" :members="members" :accounts="accounts"
          :categories="categories" :lock-type="editorMode === 'edit'" show-type
          :compact="isRealEstate || isReceivable"
        >
          <!-- 不动产（住宅 / 车位）：只录证件、面积、价格与登记方式 -->
          <ResidenceFields
            v-if="isRealEstate" :form="form" :ext-form="extForm"
            :errors="fieldErrors" :accounts="accounts"
            :show-utilities="editorMode === 'create'" :utilities="utilities"
            v-model:pending="pendingFiles"
          />
          <!-- 借出款：借款人、金额、还款方式与资金来源 -->
          <ReceivableFields
            v-else-if="isReceivable" :receivable="receivable"
            :accounts="accounts" :errors="fieldErrors"
          />
        </AssetBaseFields>
        <ParkingFields v-if="!isReceivable && EXT_TYPE === 'parking'" :form="extForm" :errors="fieldErrors" :real-estates="[]" />
        <VehicleFields v-else-if="!isReceivable && EXT_TYPE === 'vehicle'" :form="extForm" :errors="fieldErrors" :parking-spaces="[]" />
        <MetalFields v-else-if="!isReceivable && EXT_TYPE === 'precious_metal'"
                     :form="extForm" :errors="fieldErrors" :category-name="selectedCategoryName" />

        <!-- 附件：新增时暂存，资产创建成功后统一上传；编辑时直接上传 -->
        <AssetAttachments
          v-model:pending="pendingFiles"
          :asset-id="editorMode === 'edit' ? form.id : ''"
          :disabled="editorMode === 'edit' && !canEdit"
          :hint="isReceivable ? '可上传借款合同（腾讯电子签）、转账凭证、身份证照片' : '可上传房产证、发票、合同、车辆照片等'"
        />
        <AppField
          v-if="editorMode === 'edit'"
          label="调整原因"
          hint="修改当前价值时必须填写，会写入估值时间线"
          :error="fieldErrors.reason"
        >
          <AppInput v-model="form.reason" placeholder="仅在调整当前价值时必填" />
        </AppField>

        <template v-if="editorMode === 'create' && !isRealEstate && !isReceivable">
          <h4 class="sec">购置事件（可选）</h4>
          <AppField label="登记方式" hint="草稿不计入账户余额，确认后才生成资金变动">
            <AppSelect
              v-model="form.acquisition_mode"
              :options="[
                { value: 'none', label: '仅登记资产（补录，不生成资金事件）' },
                { value: 'draft', label: '保存为购置草稿' },
                { value: 'confirm', label: '直接确认购置' },
              ]"
            />
          </AppField>
          <template v-if="form.acquisition_mode !== 'none'">
            <AppField label="资金来源账户" required :error="fieldErrors['acquisition.account_id']">
              <AppSelect v-model="form.acquisition_account" :options="accountOptions" />
            </AppField>
            <AppField label="购置金额" :error="fieldErrors['acquisition.amount']" hint="留空则使用购置成本">
              <AppInput v-model="form.acquisition_amount" type="number" placeholder="0.00" />
            </AppField>
          </template>
        </template>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">
          {{ editorMode === 'create' ? '创建资产' : '保存修改' }}
        </AppButton>
      </template>
    </AppDrawer>

    <AssetDetailDrawer
      :open="detailOpen" :asset-id="detailId" :asset-type="detailType"
      :tabs="detailTabs" :accounts="accounts"
      @close="detailOpen = false" @changed="load"
    />
  </div>
</template>

<style scoped>
.assets { display: flex; flex-direction: column; gap: var(--space-4); }
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(212px, 1fr)); gap: var(--space-3); }
.kpi {
  position: relative;
  padding: 16px 18px 14px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
  background: var(--glass-1);
  overflow: hidden;
}
.kpi::after {
  content: '';
  position: absolute;
  right: -30px; top: -40px;
  width: 120px; height: 120px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(53, 109, 255, 0.14), transparent 70%);
  pointer-events: none;
}
.kpi-ico {
  display: inline-grid;
  place-items: center;
  width: 34px; height: 34px;
  border-radius: 11px;
  margin-bottom: 10px;
  color: #fff;
  background: linear-gradient(140deg, #9aa7bd, #6b7a92);
  box-shadow: 0 6px 14px -8px rgba(16, 32, 64, 0.5);
}
.kpi-ico.tone-primary { background: linear-gradient(140deg, #6f97ff, #356dff); box-shadow: 0 8px 16px -9px rgba(53, 109, 255, 0.9); }
.kpi-ico.tone-success { background: linear-gradient(140deg, #4fdcae, #17a86f); box-shadow: 0 8px 16px -9px rgba(23, 168, 111, 0.9); }
.kpi-ico.tone-ai { background: linear-gradient(140deg, #a58bff, #7c5cfc); box-shadow: 0 8px 16px -9px rgba(124, 92, 252, 0.9); }
.kpi-ico.tone-warning { background: linear-gradient(140deg, #fbc471, #e2962a); box-shadow: 0 8px 16px -9px rgba(226, 150, 42, 0.9); }
.kpi-k { margin: 0; font-size: 12.5px; color: var(--ink-muted); display: flex; align-items: center; gap: 6px; }
.kpi-v {
  margin: 4px 0 2px;
  font-size: clamp(24px, 2.1vw, 30px);
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: -0.4px;
  color: var(--ink-strong);
  font-variant-numeric: tabular-nums;
}
.kpi-v :deep(.money) { font-size: inherit; font-weight: inherit; }
.kpi-v :deep(.sym) { font-size: 0.62em; font-weight: 600; }
.kpi-v small { margin-left: 4px; font-size: 13px; font-weight: 500; color: var(--ink-muted); }
.kpi-sub { margin: 0; font-size: 11.5px; color: var(--ink-muted); line-height: 1.5; }
.kpi-click { cursor: pointer; transition: border-color var(--motion-fast) var(--ease-out), transform var(--motion-fast) var(--ease-out); }
.kpi-click:hover { border-color: var(--warning); transform: translateY(-1px); }
.kpi-click.active { border-color: var(--warning); box-shadow: 0 0 0 3px rgba(240, 173, 78, 0.18); }
.kpi-click.zero { cursor: default; }
.kpi-hint {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 999px;
  color: var(--warning);
  background: rgba(240, 173, 78, 0.14);
}
.filter-hint {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  border: 1px solid rgba(240, 173, 78, 0.4);
  background: rgba(240, 173, 78, 0.1);
  font-size: 12.5px;
  color: var(--ink);
}
.link { border: none; background: none; color: var(--primary); font-size: 12.5px; cursor: pointer; padding: 0; text-decoration: underline; }
.multi { font-size: 12.5px; color: var(--ink-muted); }
.toolbar { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; width: 100%; }
.search {
  display: flex; align-items: center; gap: 6px; flex: 1; min-width: 170px; height: 34px;
  padding: 0 10px; border: 1px solid var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1); color: var(--ink-muted);
}
.search input { flex: 1; border: none; background: transparent; color: var(--ink-strong); font-size: 13.5px; outline: none; }
.sel { width: 140px; }
.period { width: 286px; }
.date {
  width: 168px; height: 34px; padding: 0 10px; border: 1px solid var(--border);
  border-radius: var(--glass-radius-control); background: var(--glass-1); color: var(--ink-strong); font-size: 13px;
}
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act {
  display: inline-flex; align-items: center; gap: 4px; height: 28px; padding: 0 10px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1);
  color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.sec { font-size: 13.5px; color: var(--ink-strong); border-top: 1px solid var(--border); padding-top: var(--space-3); }
@media (max-width: 700px) { .sel, .date, .period { width: 100%; } }
.hidden-file { display: none; }

/* 移动端：四张 KPI 卡两列，图标与金额同步缩小 */
@media (max-width: 640px) {
  .kpi-row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .kpi { padding: 12px 13px 11px; }
  .kpi-ico { width: 26px; height: 26px; margin-bottom: 6px; border-radius: 8px; }
  .kpi-ico :deep(svg) { width: 15px; height: 15px; }
  .kpi-k { font-size: 11.5px; }
  .kpi-v { font-size: 19px; letter-spacing: -0.2px; }
  .kpi-sub { font-size: 10.5px; }
}

</style>
