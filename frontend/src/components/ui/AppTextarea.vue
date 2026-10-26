<script setup lang="ts">
/** 统一文本域。 */
defineProps<{ modelValue: string | null | undefined; placeholder?: string; rows?: number; id?: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [string] }>()
</script>

<template>
  <textarea
    :id="id"
    class="area"
    :rows="rows || 3"
    :value="modelValue ?? ''"
    :placeholder="placeholder"
    :disabled="disabled"
    @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
  />
</template>

<style scoped>
.area {
  width: 100%;
  padding: 10px 12px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  font-size: 14px;
  font-family: inherit;
  resize: vertical;
}
.area:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(53, 109, 255, 0.18);
}
</style>
