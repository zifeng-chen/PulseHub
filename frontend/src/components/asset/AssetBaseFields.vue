<script setup lang="ts">
/** 资产通用字段（06 §2）：类型与分类置顶，再填名称与详情；分类支持子分类与内联新增。 */
import { computed } from 'vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import CategoryPicker from '@/components/CategoryPicker.vue'
import { zh } from '@/i18n'
import {
  ASSET_TYPE_VALUES, CURRENCY_OPTIONS, type AccountLite, type AssetCategory, type Member,
} from '@/views/ledger/asset-shared'

const props = defineProps<{
  form: Record<string, any>
  errors: Record<string, string>
  members: Member[]
  accounts: AccountLite[]
  categories?: AssetCategory[]
  lockType?: boolean
  showType?: boolean
  /** 只渲染「类型 / 分类 / 名称 + 专项插槽」，用于住宅这类字段精简的表单 */
  compact?: boolean
  /** 紧凑模式下是否仍显示「购置成本」（车位需要，住宅不需要） */
  showAcquireCost?: boolean
  /** 购置成本在紧凑模式下的标题，例如车位叫「购入价格」 */
  acquireCostLabel?: string
}>()

/**
 * 贵金属精简口径：当前价值由金价自动估算，购置成本由克重/克价或一口价推算，
 * 持有成员、价值来源、折旧方式、维护日期对贵金属没有意义，一律不展示。
 */
const isMetal = computed(() => props.form.asset_type === 'precious_metal')

// parking 只剩历史数据兼容：新车位从「不动产 / 车位」分类新增，不再作为可选类型
const typeOptions = computed(() => {
  const options = ASSET_TYPE_VALUES
    .filter((v) => v !== 'parking')
    .map((v) => ({ value: v, label: zh('asset_type', v) }))
  // 车位是选分类时推导出来的：下拉里不补这一项会显示成空白，像是没选类型
  if (props.form.asset_type === 'parking') {
    options.push({ value: 'parking', label: zh('asset_type', 'parking') })
  }
  return options
})
const currencyOptions = CURRENCY_OPTIONS
const depreciationOptions = [
  { value: '', label: '未设置' },
  ...['none', 'straight_line', 'double_declining', 'sum_of_years', 'other'].map((v) => ({
    value: v, label: zh('depreciation_method', v),
  })),
]
const valueSourceOptions = [
  { value: '', label: '未设置' },
  ...['manual', 'appraisal', 'market', 'contract', 'other'].map((v) => ({
    value: v, label: zh('valuation_source', v),
  })),
]
const memberOptions = computed(() => [
  { value: '', label: '未指定' },
  ...props.members.map((m) => ({ value: m.id, label: m.display_name })),
])
const accountOptions = computed(() => [
  { value: '', label: '未关联' },
  ...props.accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` })),
])

// 分类一级节点 → 资产类型（与后端 assets._CATEGORY_ROOT_TO_TYPE 保持一致）
const ASSET_TYPE_BY_ROOT: Record<string, string> = {
  不动产: 'real_estate',
  车位: 'parking',
  车辆: 'vehicle',
  贵金属: 'precious_metal',
  借出款: 'other',
}

/**
 * 分类联动类型：选了「不动产 / 车位 / 车辆 / 贵金属」下的分类时自动带出类型。
 * 不联动的话，从总览页新增会一直是 other：扩展字段不渲染（必填项无处可填），
 * 房贷与租约也关联不到这条资产。
 */
function onCategoryChange(id: string) {
  props.form.category_id = id
  if (!id) return
  const list = props.categories || []
  const node = list.find((c) => c.id === id)

  // 逐级向上找类型：车位是「不动产」下的二级分类，只看根会判成住宅
  let cursor = node
  let level = 0
  let type: string | undefined
  while (cursor && level < 5) {
    type = ASSET_TYPE_BY_ROOT[cursor.name]
    if (type) break
    const parentId: string | null | undefined = cursor.parent_id
    cursor = parentId ? list.find((c) => c.id === parentId) : undefined
    level += 1
  }
  if (type) props.form.asset_type = type

  // 根分类名仍要带给父级：不动产下的「车位」需要据此确定车库类型（不再让用户手选房产类型）
  let root = node
  let guard = 0
  while (root?.parent_id && guard < 4) {
    root = list.find((c) => c.id === root!.parent_id)
    guard += 1
  }
  if (root) (props.form as Record<string, unknown>).category_root_name = root.name
}

/**
 * 分类下拉的过滤键。
 *
 * 选完「车位」后资产类型会切成 parking，如果过滤键跟着变成 parking，
 * 车位分类本身（applies_to=real_estate）就会被过滤掉，下拉看起来像没选。
 * 所以只要选中的分类挂在「不动产」下，就始终按 real_estate 过滤。
 */
const categoryFilterKey = computed(() => {
  const list = props.categories || []
  let node = list.find((c) => c.id === props.form.category_id)
  let guard = 0
  while (node?.parent_id && guard < 4) {
    node = list.find((c) => c.id === node!.parent_id)
    guard += 1
  }
  return node?.name === '不动产' ? 'real_estate' : props.form.asset_type
})
</script>

<template>
  <AppField v-if="showType" label="资产类型" required :error="errors.asset_type"
            hint="类型决定必填字段；已有业务数据的资产不能直接切换类型">
    <AppSelect v-model="form.asset_type" :options="typeOptions" :disabled="lockType" />
  </AppField>
  <CategoryPicker :model-value="form.category_id" domain="asset" :error="errors.category_id"
                  :filter-key="categoryFilterKey"
                  hint="只显示当前资产类型下的分类（不动产：住宅 / 车位）"
                  @update:model-value="onCategoryChange" />
  <AppField label="资产名称" required :error="errors.name">
    <AppInput v-model="form.name" placeholder="例如：城东自住房 / 家用车 / 投资金条" />
  </AppField>

  <!-- 专项信息紧跟在名称之后：选完分类就能填最关键的字段（如住宅的地址与面积） -->
  <slot />

  <!-- 车位这类精简表单：只要一个购入价格，不要币种/估值/折旧那一整套 -->
  <AppField v-if="compact && showAcquireCost" :label="acquireCostLabel || '购置成本'"
            :error="errors.acquire_cost" hint="买入时实际支付的价格，用于计算持有成本">
    <AppInput v-model="form.acquire_cost" type="number" placeholder="0.00" />
  </AppField>

  <template v-if="!compact">
  <!-- 资产默认人民币：只有外币资产才展开币种选择 -->
  <AppField v-if="form.currency !== 'CNY'" label="币种" :error="errors.currency"
            hint="默认人民币；仅外币资产需要修改">
    <AppSelect v-model="form.currency" :options="currencyOptions" />
  </AppField>
  <AppField v-else label="币种">
    <div class="ccy-row">
      <span>人民币 CNY</span>
      <button class="ccy-link" type="button" @click="form.currency = 'USD'">记外币</button>
    </div>
  </AppField>
  <!-- 贵金属：当前价值由金价自动估算，购置成本由克重/克价推算，不手填 -->
  <template v-if="!isMetal">
    <AppField label="当前价值" :error="errors.current_value"
              hint="决定净资产与资产结构；修改已登记的价值需填写调整原因并写入估值时间线">
      <AppInput v-model="form.current_value" type="number" placeholder="0.00" />
    </AppField>
    <AppField label="购置成本" :error="errors.acquire_cost">
      <AppInput v-model="form.acquire_cost" type="number" placeholder="0.00" />
    </AppField>
  </template>
  <AppField label="购买日期" :error="errors.acquire_date">
    <AppDatePicker v-model="form.acquire_date" placeholder="选择购买日期" />
  </AppField>
  <template v-if="!isMetal">
    <AppField label="持有成员" :error="errors.owner_member_id">
      <AppSelect v-model="form.owner_member_id" :options="memberOptions" />
    </AppField>
    <AppField label="价值来源" :error="errors.value_source">
      <AppSelect v-model="form.value_source" :options="valueSourceOptions" />
    </AppField>
    <AppField label="折旧方式" :error="errors.depreciation_method">
      <AppSelect v-model="form.depreciation_method" :options="depreciationOptions" />
    </AppField>
    <AppField label="维护日期" :error="errors.maintenance_date">
      <AppDatePicker v-model="form.maintenance_date" placeholder="选择维护日期" />
    </AppField>
  </template>
  <AppField label="关联账户" :error="errors.linked_account_id">
    <AppSelect v-model="form.linked_account_id" :options="accountOptions" />
  </AppField>
  <AppField label="备注" :error="errors.note">
    <AppInput v-model="form.note" />
  </AppField>
  </template>
  <!-- 不动产用紧凑模式，但「计入净资产」必须保留：它是房产维度的唯一开关 -->
  <AppField label="计入净资产"
            hint="资产当前价值是净资产的加项；出售、报废、归档后自动排除">
    <AppSwitch v-model="form.include_in_net_worth" label="计入净资产" />
  </AppField>
</template>
<style scoped>
.ccy-row { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--ink-strong); }
.ccy-link {
  border: none; background: transparent; padding: 0; cursor: pointer;
  font-size: 12.5px; color: var(--primary); text-decoration: underline;
}
</style>
