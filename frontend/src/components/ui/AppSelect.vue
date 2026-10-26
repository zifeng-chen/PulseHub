<script setup lang="ts">
/** 统一下拉选择。 */
defineProps<{
  modelValue: string | number | null | undefined
  options: { value: string | number; label: string }[]
  disabled?: boolean
  placeholder?: string
  id?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [string] }>()
</script>

<template>
  <select
    :id="id"
    class="select"
    :value="modelValue ?? ''"
    :disabled="disabled"
    @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <option v-if="placeholder" value="" disabled>{{ placeholder }}</option>
    <option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option>
  </select>
</template>

<style scoped>
.select {
  width: 100%;
  height: 38px;
  padding: 0 10px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  font-size: 14px;
  font-family: inherit;
}
.select:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(53, 109, 255, 0.18);
}
.select:disabled { opacity: 0.6; }
</style>
