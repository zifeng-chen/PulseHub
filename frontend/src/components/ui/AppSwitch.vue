<script setup lang="ts">
/** 统一开关。 */
defineProps<{ modelValue: boolean; disabled?: boolean; label?: string; id?: string }>()
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()
</script>

<template>
  <button
    :id="id"
    class="switch"
    :class="{ on: modelValue }"
    type="button"
    role="switch"
    :aria-checked="modelValue ? 'true' : 'false'"
    :aria-label="label"
    :disabled="disabled"
    @click="emit('update:modelValue', !modelValue)"
  >
    <span class="track"><span class="thumb" /></span>
    <span v-if="label" class="txt">{{ label }}</span>
  </button>
</template>

<style scoped>
.switch {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--ink);
  font-size: 13px;
  padding: 0;
}
.track {
  width: 40px;
  height: 22px;
  border-radius: 999px;
  background: var(--border);
  position: relative;
  transition: background-color var(--motion-fast) var(--ease-out);
}
.thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.28);
  transition: transform var(--motion-fast) var(--ease-out);
}
.switch.on .track { background: var(--primary); }
.switch.on .thumb { transform: translateX(18px); }
.switch:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
