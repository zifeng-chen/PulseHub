<script setup lang="ts">
/**
 * 资产卡片（资产总览与不动产/车辆/车位/贵金属专项页共用）。
 * 版式与账户卡片一致：右上角角标、左侧图标、大金额、底部虚线操作区。
 */
import { computed } from 'vue'
import {
  CarFront, Clock, Gem, House, Package, SquareParking,
} from 'lucide-vue-next'
import MoneyText from '@/components/ui/MoneyText.vue'
import { formatAmount } from '@/utils/format'
import { useUiStore } from '@/stores/ui'
import { fmtDate, type AssetRow } from '@/views/ledger/asset-shared'

const props = withDefaults(
  defineProps<{ row: AssetRow; canEdit?: boolean }>(),
  { canEdit: false },
)
const emit = defineEmits<{ open: []; edit: [] }>()
const ui = useUiStore()

const LOOK: Record<string, { icon: unknown; tone: string }> = {
  // 换成更贴切的造型：房子 / 车位标牌 / 车头 / 宝石 / 包裹
  real_estate: { icon: House, tone: 'primary' },
  parking: { icon: SquareParking, tone: 'ai' },
  vehicle: { icon: CarFront, tone: 'success' },
  precious_metal: { icon: Gem, tone: 'warning' },
  other: { icon: Package, tone: 'neutral' },
}
const look = computed(() => LOOK[props.row.asset_type] || LOOK.other)
const ext = computed<Record<string, any>>(() => (props.row.extension || {}) as Record<string, any>)

/** 待估值：当前价值为空/0，或从未登记过估值 */
const pending = computed(() => {
  const v = Number(props.row.current_value || 0)
  return !v || !props.row.last_valuation_date
})

/** 副标题：优先放最有辨识度的一条（地址 / 车牌 / 编号） */
const subtitle = computed(() => {
  const e = ext.value
  switch (props.row.asset_type) {
    case 'real_estate':
      return String(e.address || props.row.category_label || props.row.asset_type_label)
    case 'parking':
      return [e.parking_no, e.linked_real_estate_name].filter(Boolean).join(' · ') || props.row.category_label
    case 'vehicle':
      return [e.plate_no, [e.brand, e.series].filter(Boolean).join(' ')].filter(Boolean).join(' · ')
        || props.row.category_label
    case 'precious_metal':
      return [e.category_label, e.form_label].filter(Boolean).join(' · ') || props.row.category_label
    default:
      return props.row.category_label || props.row.asset_type_label
  }
})

/** 类型专属的元信息（面积 / 里程 / 重量 / 管理费…） */
const extras = computed<string[]>(() => {
  const e = ext.value
  switch (props.row.asset_type) {
    case 'real_estate':
      return [
        e.property_type_label || '',
        e.building_area ? `${num(e.building_area)} ㎡` : '',
        e.has_mortgage ? '有房贷' : '',
      ].filter(Boolean)
    case 'parking':
      return [e.parking_type_label || '', e.management_fee ? `管理费 ${num(e.management_fee)}` : ''].filter(Boolean)
    case 'vehicle':
      return [
        e.energy_type_label || '',
        e.mileage ? `${num(e.mileage)} km` : '',
        e.has_loan ? '有贷款' : '',
      ].filter(Boolean)
    case 'precious_metal':
      return [
        e.weight ? `${num(e.weight)} ${e.unit_label || ''}`.trim() : '',
        e.purity || '',
      ].filter(Boolean)
    default:
      return []
  }
})

const inactive = computed(() => props.row.status !== 'active')

/** 面积/重量/里程这类数字后端按 4 位小数返回，展示时去掉多余的 0 */
function num(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return String(Number(n.toFixed(2))).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}
</script>

<template>
  <article
    class="entity-card"
    :class="{ 'is-muted': inactive }"
    role="button"
    tabindex="0"
    @click="emit('open')"
    @keyup.enter="emit('open')"
  >
    <span class="entity-corner" :class="`tone-${look.tone}`">
      {{ row.category_label || row.asset_type_label }}
    </span>

    <header class="entity-head">
      <span class="entity-icon" :class="`tone-${look.tone}`">
        <component :is="look.icon" :size="18" />
      </span>
      <div class="entity-title">
        <h3>{{ row.name }}</h3>
        <p>{{ subtitle }}</p>
      </div>
    </header>

    <div class="entity-value">
      <p class="k">当前价值</p>
      <p class="v">
        <MoneyText
          v-if="Number(row.current_value) > 0"
          :value="row.current_value" :currency="row.currency" show-currency
        />
        <span v-else class="muted">待估值</span>
      </p>
    </div>

    <div v-if="pending" class="entity-note warn">
      <Clock :size="13" />
      <span>还没有有效估值</span>
      <strong>净资产按 0 计</strong>
    </div>

    <div class="entity-meta">
      <span v-for="item in extras" :key="item">{{ item }}</span>
      <span>购入 {{ formatAmount(row.acquire_cost, ui.amountPrecision) }}</span>
      <span>最近估值 {{ row.last_valuation_date ? fmtDate(row.last_valuation_date) : '—' }}</span>
      <span>{{ row.status_label }}</span>
    </div>

    <footer class="entity-actions" @click.stop>
      <button class="act" type="button" @click="emit('open')">详情</button>
      <button
        v-if="canEdit && !['disposed', 'scrapped', 'archived'].includes(row.status)"
        class="act" type="button" @click="emit('edit')"
      >
        编辑
      </button>
    </footer>
  </article>
</template>
