<script setup lang="ts">
/**
 * 贵金属专属字段（15 §6.1 修订版）。
 *
 * 设计原则：
 * - 材质由「资产分类」表达，不再重复出现「品类」（避免与分类选项冲突）；
 * - 「计价方式」只有两条路：克重计价（克价 × 克重 + 加工费）或一口价；
 * - 品牌取自国内 + 中国香港/中国澳门常见品牌，选定品牌后购买渠道随之联动；
 * - 证书编号只对「投资/理财金条」有意义，其他分类不显示；
 * - 当前价值不手填：按当日上金所金价 × 克重 × 纯度自动估算，并展示回收价参考。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{
  form: Record<string, any>
  errors: Record<string, string>
  /** 当前选中的资产分类名（用于判断是否「投资金条」才显示证书编号） */
  categoryName?: string
}>()

const ui = useUiStore()

interface Brand { code: string; name: string; region: string; channels: string[] }
interface MetalPrices {
  available: boolean
  stale?: boolean
  detail?: string
  date?: string | null
  gold?: { price: string; unit: string; updated?: string } | null
  recycle_gold?: string | null
  recycle_22k?: string | null
  recycle_18k?: string | null
}

const brands = ref<Brand[]>([])
const prices = ref<MetalPrices | null>(null)
const loadingPrices = ref(false)

const pricingMode = computed({
  get: () => props.form.pricing_mode || 'by_weight',
  set: (v: string) => { props.form.pricing_mode = v },
})

const brandOptions = computed(() => [
  { value: '', label: '未选择品牌' },
  ...brands.value.map((b) => ({ value: b.code, label: `${b.name}（${b.region}）` })),
])

/** 购买渠道与品牌挂钩：选品牌后只列该品牌常见渠道 */
const channelOptions = computed(() => {
  const brand = brands.value.find((b) => b.code === props.form.brand_code)
  const list = brand?.channels?.length
    ? brand.channels
    : ['线下门店', '官方旗舰店（天猫/京东）', '银行柜面', '其他']
  return [{ value: '', label: '未选择渠道' }, ...list.map((c) => ({ value: c, label: c }))]
})

const purityOptions = [
  { value: '999.9', label: '足金 999.9（Au9999）' },
  { value: '999', label: '足金 999（Au999）' },
  { value: '916', label: '22K / 916' },
  { value: '750', label: '18K / Au750' },
  { value: '585', label: '14K / 585' },
  { value: '999.5', label: '铂金 Pt999.5' },
  { value: '950', label: '铂金 Pt950' },
  { value: '925', label: '银 925' },
  { value: '999.0', label: '银 999' },
]

/** 只有投资 / 理财金条才需要证书编号（分类名优先取父级传入，否则自行解析 category_id） */
const categoryName = ref('')
const needsCertificate = computed(() => {
  const name = props.categoryName || categoryName.value || ''
  return ['金条', '投资金', '理财金', '收藏金'].some((k) => name.includes(k))
})

const categoryNameById = ref<Record<string, string>>({})

async function loadCategories() {
  try {
    const res = await api.get<{ items: { id: string; name: string }[] }>('/ledger/categories')
    categoryNameById.value = Object.fromEntries((res.items || []).map((c) => [c.id, c.name]))
    categoryName.value = categoryNameById.value[props.form.category_id] || ''
  } catch {
    categoryNameById.value = {}
  }
}

watch(() => props.form.category_id, (id) => {
  if (!props.categoryName) categoryName.value = categoryNameById.value[id] || ''
})

function onBrandChange(code: string) {
  props.form.brand_code = code
  const brand = brands.value.find((b) => b.code === code)
  props.form.brand = brand?.name || ''
  // 换品牌后原渠道可能不属于该品牌，清空避免脏值
  if (props.form.purchase_channel && !(brand?.channels || []).includes(props.form.purchase_channel)) {
    props.form.purchase_channel = ''
  }
}

/** 纯度系数：999.9 → 0.9999，兼容 99.99 / 999 / 9999 等写法 */
function purityRatio(raw: unknown): number {
  const v = Number(raw)
  if (!Number.isFinite(v) || v <= 0) return 0.999
  if (v <= 1) return v
  if (v <= 100) return v / 100
  if (v <= 1000) return v / 1000
  return v / 10000
}

const weight = computed(() => Number(props.form.weight) || 0)
const ratio = computed(() => purityRatio(props.form.purity))
const goldPrice = computed(() => Number(prices.value?.gold?.price) || 0)

/** 购入总价：克重计价 = 克重×克价+加工费；一口价 = 一口价金额 */
const purchaseTotal = computed(() => {
  if (pricingMode.value === 'flat') {
    const flat = Number(props.form.flat_price) || 0
    return flat > 0 ? flat : null
  }
  if (!weight.value) return null
  const unit = Number(props.form.unit_price) || 0
  if (!unit) return null
  const fee = Number(props.form.processing_fee) || 0
  return weight.value * unit + fee
})

/** 按当日金价估算的市值 */
const marketValue = computed(() => {
  if (!weight.value || !goldPrice.value) return null
  return weight.value * ratio.value * goldPrice.value
})

const recyclePrice = computed(() => {
  const p = ratio.value
  const data = prices.value
  if (!data) return null
  const pick = p >= 0.99 ? data.recycle_gold
    : p >= 0.916 ? data.recycle_22k
      : p >= 0.75 ? data.recycle_18k
        : null
  const value = Number(pick) || 0
  return value > 0 ? value : null
})

const recycleValue = computed(() => {
  if (!weight.value || !recyclePrice.value) return null
  return weight.value * recyclePrice.value
})

const priceHint = computed(() => {
  const data = prices.value
  if (!data) return '正在获取今日金价…'
  if (!data.available && !data.gold) {
    return data.detail || '金价接口不可用，登记后将按购置价计入，可稍后手工估值'
  }
  const suffix = data.stale ? '（上次成功数据）' : ''
  return `上金所 AU9999 ${data.gold?.price} ${data.gold?.unit || '元/克'}${suffix}`
})

async function loadDictionaries() {
  try {
    const res = await api.get<{ brands: Brand[] }>('/ledger/metal-brands')
    brands.value = res.brands || []
  } catch {
    brands.value = []
  }
}

async function loadPrices(refresh = false) {
  loadingPrices.value = true
  try {
    prices.value = await api.get<MetalPrices>('/ledger/metal-prices', refresh ? { refresh: true } : undefined)
  } catch {
    prices.value = null
  } finally {
    loadingPrices.value = false
  }
}

onMounted(() => {
  void loadDictionaries()
  void loadPrices()
  void loadCategories()
})
</script>

<template>
  <AppField label="品牌" :error="errors['extension.brand']" hint="选品牌后，购买渠道只列该品牌的常见渠道">
    <div class="grid-2">
      <AppSelect :model-value="form.brand_code || ''" :options="brandOptions" @update:model-value="onBrandChange" />
      <AppInput v-model="form.brand" placeholder="品牌名（可手填，如某地金店）" />
    </div>
  </AppField>

  <AppField label="购买渠道" :error="errors['extension.purchase_channel']" hint="随品牌联动，也可手填其他渠道">
    <AppSelect v-model="form.purchase_channel" :options="channelOptions" />
  </AppField>

  <AppField label="计价方式" required hint="克重计价按克价结算；一口价按件标价（常见于金饰、生肖金）">
    <AppSelect v-model="pricingMode" :options="[
      { value: 'by_weight', label: '克重计价（克价 × 克重）' },
      { value: 'flat', label: '一口价（按件标价）' },
    ]" />
  </AppField>

  <AppField label="克重与纯度" required :error="errors['extension.weight'] || errors['extension.purity']">
    <div class="grid-2">
      <AppInput v-model="form.weight" type="number" placeholder="克重（g）" />
      <AppSelect v-model="form.purity" :options="purityOptions" />
    </div>
  </AppField>

  <AppField v-if="pricingMode === 'by_weight'" label="克价与加工费"
            :error="errors['extension.unit_price'] || errors['extension.processing_fee']"
            hint="克价是购买时的每克单价；加工费按件计">
    <div class="grid-2">
      <AppInput v-model="form.unit_price" type="number" placeholder="克价（元/克）" />
      <AppInput v-model="form.processing_fee" type="number" placeholder="加工费（元）" />
    </div>
  </AppField>
  <AppField v-else label="一口价金额" :error="errors['extension.flat_price']"
            hint="按件标价的总金额（含工费），不按克价拆分">
    <AppInput v-model="form.flat_price" type="number" placeholder="一口价（元）" />
  </AppField>

  <AppField v-if="needsCertificate" label="证书编号" :error="errors['extension.certificate_no']"
            hint="投资金条才填；敏感字段，默认掩码">
    <AppInput v-model="form.certificate_no" placeholder="金条证书 / 鉴定证书编号" />
  </AppField>

  <AppField label="保管位置" hint="自由文本，不做位置管理">
    <AppInput v-model="form.storage_location" placeholder="例如：家中保险柜" />
  </AppField>

  <div class="price-panel">
    <div class="pp-head">
      <span class="pp-title">今日金价</span>
      <button class="pp-refresh" type="button" :disabled="loadingPrices" @click="loadPrices(true)">
        {{ loadingPrices ? '获取中…' : '刷新' }}
      </button>
    </div>
    <p class="pp-line">{{ priceHint }}</p>
    <p v-if="recyclePrice" class="pp-line">
      足金回收价：<strong>{{ recyclePrice.toFixed(2) }}</strong> 元/克
      <span class="pp-sub">（市场参考，实际以回收商报价为准）</span>
    </p>
    <div v-if="marketValue || recycleValue || purchaseTotal" class="pp-calc">
      <div v-if="marketValue">
        <span>按金价估算市值</span>
        <strong>¥{{ marketValue.toFixed(ui.amountPrecision) }}</strong>
      </div>
      <div v-if="recycleValue">
        <span>按回收价估算</span>
        <strong>¥{{ recycleValue.toFixed(ui.amountPrecision) }}</strong>
      </div>
      <div v-if="purchaseTotal">
        <span>购入总价</span>
        <strong>¥{{ purchaseTotal.toFixed(ui.amountPrecision) }}</strong>
      </div>
    </div>
    <p class="pp-note">
      当前价值由系统按「克重 × 纯度 × 今日金价」自动估算，无需手填；金价接口不可用时按购置价计。
    </p>
  </div>
</template>

<style scoped>
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.price-panel {
  display: flex; flex-direction: column; gap: 6px;
  padding: 12px 14px; border: 1px dashed var(--border);
  border-radius: var(--glass-radius-card); background: var(--glass-1);
}
.pp-head { display: flex; align-items: center; justify-content: space-between; }
.pp-title { font-size: 13px; font-weight: 600; color: var(--ink-strong); }
.pp-refresh {
  border: 1px solid var(--border); background: transparent; cursor: pointer;
  font-size: 12px; color: var(--primary); padding: 2px 8px; border-radius: 8px;
}
.pp-refresh:disabled { opacity: 0.6; cursor: default; }
.pp-line { font-size: 12.5px; color: var(--ink); }
.pp-line strong { color: var(--ink-strong); }
.pp-sub { color: var(--ink-muted); }
.pp-calc { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-top: 2px; }
.pp-calc > div { display: flex; flex-direction: column; gap: 1px; font-size: 12px; color: var(--ink-muted); }
.pp-calc strong { font-size: 14px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.pp-note { font-size: 11.5px; color: var(--ink-muted); line-height: 1.5; }
@media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }
</style>
