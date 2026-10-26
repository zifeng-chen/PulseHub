<script setup lang="ts">
/** 车辆专属字段（15 §5.1）。VIN、车牌、发动机号默认掩码。 */
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import { zh } from '@/i18n'

defineProps<{
  form: Record<string, any>
  errors: Record<string, string>
  parkingSpaces?: { id: string; name: string }[]
}>()

const energyOptions = ['fuel', 'electric', 'phev', 'hybrid', 'other'].map((v) => ({
  value: v, label: zh('vehicle_energy_type', v),
}))
const usageOptions = [
  { value: '', label: '未设置' },
  ...['in_use', 'shared', 'rented', 'idle'].map((v) => ({
    value: v, label: zh('vehicle_usage_status', v),
  })),
]
</script>

<template>
  <AppField label="品牌 / 车系 / 车型">
    <div class="grid-3">
      <AppInput v-model="form.brand" placeholder="品牌" />
      <AppInput v-model="form.series" placeholder="车系" />
      <AppInput v-model="form.model" placeholder="车型" />
    </div>
  </AppField>
  <AppField label="车辆识别代号 VIN" :error="errors['extension.vin']" hint="敏感字段，默认掩码">
    <AppInput v-model="form.vin" placeholder="17 位 VIN" />
  </AppField>
  <AppField label="车牌号" :error="errors['extension.plate_no']" hint="敏感字段，默认掩码">
    <AppInput v-model="form.plate_no" placeholder="例如：沪A12345" />
  </AppField>
  <AppField label="发动机号（可选）" :error="errors['extension.engine_no']" hint="敏感字段，默认掩码">
    <AppInput v-model="form.engine_no" />
  </AppField>
  <AppField label="车辆类型">
    <AppInput v-model="form.vehicle_type" placeholder="例如：轿车 / SUV / MPV" />
  </AppField>
  <AppField label="能源类型">
    <AppSelect v-model="form.energy_type" :options="energyOptions" />
  </AppField>
  <AppField label="上牌日期">
    <AppDatePicker v-model="form.register_date" placeholder="选择上牌日期" />
  </AppField>
  <AppField label="里程（公里）">
    <AppInput v-model="form.mileage" type="number" placeholder="0" />
  </AppField>
  <AppField label="颜色">
    <AppInput v-model="form.color" />
  </AppField>
  <AppField label="使用状态">
    <AppSelect v-model="form.usage_status" :options="usageOptions" />
  </AppField>
  <AppField label="关联车位">
    <AppSelect
      v-model="form.parking_space_id"
      :options="[{ value: '', label: '不关联' }, ...(parkingSpaces || []).map((p) => ({ value: p.id, label: p.name }))]"
    />
  </AppField>
  <AppField label="车辆贷款">
    <AppSwitch v-model="form.has_loan" label="这辆车有贷款" />
    <p class="hint">勾选后，保存资产会自动跳到「负债 → 新增车贷」，并带上关联车辆。</p>
  </AppField>
  <AppField label="备注">
    <AppInput v-model="form.note" />
  </AppField>
</template>

<style scoped>
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.hint { margin-top: 6px; font-size: 12px; color: var(--ink-muted); line-height: 1.5; }
@media (max-width: 700px) { .grid-3 { grid-template-columns: 1fr; } }
</style>
