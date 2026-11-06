<script setup lang="ts">
/**
 * 车位专属字段（精简版）。
 *
 * 只保留录入车位时真正会用到的信息：
 *   车库-车位号、车位管理费、是否关联房产。
 * 其余（车位类型/产权证号/区域/面积/备注）要么没有决策价值，要么属于「以后再说」，
 * 一律不再出现在表单里；历史数据仍然保留，编辑时不会被清空。
 */
import { computed } from 'vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'

const props = defineProps<{
  form: Record<string, any>
  errors: Record<string, string>
  realEstates?: { id: string; name: string }[]
}>()

const estateOptions = computed(() =>
  (props.realEstates || []).map((r) => ({ value: r.id, label: r.name })))

/** 是否关联房产：关掉就清空关联，避免留下一个看不见的悬空 id */
const linked = computed({
  get: () => !!props.form.linked_real_estate_id,
  set: (value: boolean) => {
    props.form.linked_real_estate_id = value ? (props.form.linked_real_estate_id || '') : ''
  },
})
</script>

<template>
  <AppField label="车库-车位号" required :error="errors['extension.parking_no']">
    <AppInput v-model="form.parking_no" placeholder="例如：B1-088" />
  </AppField>

  <AppField label="车位管理费（元/月）" :error="errors['extension.management_fee']"
            hint="物业每月收取的车位管理费；没有就留空">
    <AppInput v-model="form.management_fee" type="number" placeholder="0.00" />
  </AppField>

  <AppField label="关联房产">
    <AppSwitch v-model="linked" label="关联到一套房产" />
    <p v-if="linked && !estateOptions.length" class="hint">
      还没有房产可以关联。先到「不动产」里新增住宅，再回来选择。
    </p>
  </AppField>

  <AppField v-if="linked && estateOptions.length" label="选择房产"
            :error="errors['extension.linked_real_estate_id']"
            hint="一个车位最多关联一套不动产；解除关联会保留审计历史">
    <AppSelect v-model="form.linked_real_estate_id" :options="estateOptions" />
  </AppField>
</template>

<style scoped>
.hint { margin: 8px 0 0; font-size: 12px; color: var(--ink-muted); line-height: 1.6; }
</style>
