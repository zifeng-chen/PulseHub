<script setup lang="ts">
/** 表单字段容器：统一标签、必填标记、字段级错误（03 §5）。 */
defineProps<{
  label: string
  required?: boolean
  error?: string
  hint?: string
  forId?: string
}>()
</script>

<template>
  <div class="field" :class="{ 'has-error': Boolean(error) }">
    <label :for="forId" class="lbl">
      {{ label }}
      <span v-if="required" class="req" aria-hidden="true">*</span>
    </label>
    <div class="control"><slot /></div>
    <p v-if="hint && !error" class="hint">{{ hint }}</p>
    <p v-if="error" class="err" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: 6px; }
.lbl { font-size: 13px; color: var(--ink); font-weight: 500; }
.req { color: var(--danger); margin-left: 3px; }
.hint { font-size: 12px; color: var(--ink-muted); }
.err { font-size: 12px; color: var(--danger); }
</style>
