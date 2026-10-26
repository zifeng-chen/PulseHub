<script setup lang="ts">
/** 不动产专属字段（15 §3.1）。证件号默认掩码展示，只有审计查看权限可看完整值。 */
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import { zh } from '@/i18n'

defineProps<{ form: Record<string, any>; errors: Record<string, string> }>()

const propertyOptions = ['residence', 'shop', 'office', 'garage', 'other'].map((v) => ({
  value: v, label: zh('property_type', v),
}))
</script>

<template>
  <!-- 房产类型不再让用户选：由所选分类决定（住宅 / 车位），避免与分类重复录入 -->
  <AppField label="房产证编号" :error="errors['extension.title_no']" hint="敏感字段，默认掩码">
    <AppInput v-model="form.title_no" placeholder="例：京(2020)东不动产权第 0001 号" />
  </AppField>
  <AppField label="不动产权证号" :error="errors['extension.ownership_no']" hint="敏感字段，默认掩码">
    <AppInput v-model="form.ownership_no" />
  </AppField>
  <AppField label="房屋坐落地址" :error="errors['extension.address']" hint="仅作为资料文本，不做位置管理">
    <AppInput v-model="form.address" placeholder="省市区 + 路名 + 门牌" />
  </AppField>
  <AppField label="楼栋 / 单元 / 楼层 / 房号">
    <div class="grid-4">
      <AppInput v-model="form.building" placeholder="楼栋" />
      <AppInput v-model="form.unit" placeholder="单元" />
      <AppInput v-model="form.floor" placeholder="楼层" />
      <AppInput v-model="form.room_no" placeholder="房号" />
    </div>
  </AppField>
  <AppField label="建筑面积 / 套内面积 / 土地面积（㎡）">
    <div class="grid-3">
      <AppInput v-model="form.building_area" type="number" placeholder="建筑面积" />
      <AppInput v-model="form.inner_area" type="number" placeholder="套内面积" />
      <AppInput v-model="form.land_area" type="number" placeholder="土地面积" />
    </div>
  </AppField>
  <AppField label="朝向 / 户型 / 建成年份">
    <div class="grid-3">
      <AppInput v-model="form.orientation" placeholder="朝向" />
      <AppInput v-model="form.layout" placeholder="户型，如 3室2厅" />
      <AppInput v-model="form.built_year" type="number" placeholder="建成年份" min="1900" max="2100" />
    </div>
  </AppField>
  <AppField label="交付日期">
    <AppDatePicker v-model="form.delivery_date" placeholder="选择交付日期" />
  </AppField>
  <AppField label="装修金额 / 税费 / 中介费">
    <div class="grid-3">
      <AppInput v-model="form.renovation_cost" type="number" placeholder="装修金额" />
      <AppInput v-model="form.tax_cost" type="number" placeholder="税费" />
      <AppInput v-model="form.agency_fee" type="number" placeholder="中介费" />
    </div>
  </AppField>
  <AppField label="共有比例" hint="0-1 之间，例如 0.5 表示占一半产权">
    <AppInput v-model="form.ownership_ratio" type="number" placeholder="0.5" />
  </AppField>
  <AppField label="房贷">
    <AppSwitch v-model="form.has_mortgage" label="有房贷" />
    <p class="hint">房贷的余额、利率与还款由负债模块统一管理，此处只标记并建立关联入口。</p>
  </AppField>
  <AppField label="备注">
    <AppInput v-model="form.note" />
  </AppField>
</template>

<style scoped>
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.hint { margin-top: 6px; font-size: 12px; color: var(--ink-muted); line-height: 1.5; }
@media (max-width: 700px) {
  .grid-3, .grid-4 { grid-template-columns: 1fr; }
}
</style>
