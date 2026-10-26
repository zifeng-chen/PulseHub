<script setup lang="ts">
/** 车位专属字段（15 §4.1）：一个车位最多关联一套不动产。 */
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import { zh } from '@/i18n'

const props = defineProps<{
  form: Record<string, any>
  errors: Record<string, string>
  realEstates?: { id: string; name: string }[]
}>()

const parkingOptions = ['ownership', 'usage_right', 'lease'].map((v) => ({
  value: v, label: zh('parking_type', v),
}))
const cycleOptions = [
  { value: '', label: '未设置' },
  ...['monthly', 'quarterly', 'half_yearly', 'yearly', 'one_time'].map((v) => ({
    value: v, label: zh('billing_cycle', v),
  })),
]
const linkOptions = () => [
  { value: '', label: '不关联' },
  ...(props.realEstates || []).map((r) => ({ value: r.id, label: r.name })),
]
</script>

<template>
  <AppField label="车位类型" required :error="errors['extension.parking_type']">
    <AppSelect v-model="form.parking_type" :options="parkingOptions" />
  </AppField>
  <AppField label="车位编号" required :error="errors['extension.parking_no']">
    <AppInput v-model="form.parking_no" placeholder="例如：B1-088" />
  </AppField>
  <AppField label="产权证号 / 使用权编号" :error="errors['extension.certificate_no']" hint="敏感字段，默认掩码">
    <AppInput v-model="form.certificate_no" />
  </AppField>
  <AppField label="区域 / 楼栋">
    <AppInput v-model="form.area_name" placeholder="例如：地下二层 A 区" />
  </AppField>
  <AppField label="面积（㎡）">
    <AppInput v-model="form.area" type="number" placeholder="0.00" />
  </AppField>
  <AppField label="管理费 / 周期">
    <div class="grid-2">
      <AppInput v-model="form.management_fee" type="number" placeholder="管理费" />
      <AppSelect v-model="form.management_fee_cycle" :options="cycleOptions" />
    </div>
  </AppField>
  <AppField label="所属不动产" :error="errors['extension.linked_real_estate_id']"
            hint="一个车位最多关联一套不动产；解除关联会保留审计历史">
    <AppSelect v-model="form.linked_real_estate_id" :options="linkOptions()" />
  </AppField>
  <AppField label="备注">
    <AppInput v-model="form.note" />
  </AppField>
</template>

<style scoped>
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }
</style>
