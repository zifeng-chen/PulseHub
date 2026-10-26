<script setup lang="ts">
/** 统一按钮（03 §5、17 §6.2）：主操作一个、次操作不抢视觉、危险操作红色且二次确认。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    disabled?: boolean
    block?: boolean
    type?: 'button' | 'submit'
  }>(),
  { variant: 'secondary', size: 'md', loading: false, disabled: false, block: false, type: 'button' },
)

const classes = computed(() => [
  'app-btn',
  `is-${props.variant}`,
  `is-${props.size}`,
  { 'is-block': props.block, 'is-loading': props.loading },
])
</script>

<template>
  <button
    :class="classes"
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading ? 'true' : 'false'"
  >
    <span v-if="loading" class="spinner" aria-hidden="true" />
    <span class="label"><slot /></span>
  </button>
</template>

<style scoped>
.app-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  border-radius: var(--glass-radius-control);
  border: 1px solid transparent;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: transform var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) var(--ease-out),
    background-color var(--motion-fast) var(--ease-out), border-color var(--motion-fast) var(--ease-out);
}
.app-btn.is-sm { height: 32px; padding: 0 12px; font-size: 13px; }
.app-btn.is-md { height: 38px; padding: 0 16px; font-size: 14px; }
.app-btn.is-lg { height: 46px; padding: 0 22px; font-size: 15px; }
.app-btn.is-block { width: 100%; }

.app-btn.is-primary {
  color: #fff;
  background: linear-gradient(135deg, var(--primary), var(--accent-ai));
  box-shadow: 0 6px 18px rgba(53, 109, 255, 0.28), var(--glass-inner-highlight);
}
.app-btn.is-primary:hover:not(:disabled) { box-shadow: 0 10px 26px rgba(53, 109, 255, 0.36); }

.app-btn.is-secondary {
  color: var(--ink-strong);
  background: var(--glass-2);
  border-color: var(--border);
  backdrop-filter: blur(var(--glass-blur));
}
.app-btn.is-secondary:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }

.app-btn.is-ghost { color: var(--ink); background: transparent; }
.app-btn.is-ghost:hover:not(:disabled) { background: var(--glass-1); color: var(--ink-strong); }

.app-btn.is-danger {
  color: var(--danger);
  background: transparent;
  border-color: var(--danger);
}
.app-btn.is-danger:hover:not(:disabled) { background: rgba(232, 93, 117, 0.1); }

.app-btn:active:not(:disabled) { transform: scale(0.99); }
.app-btn:disabled { opacity: 0.56; cursor: not-allowed; }

.spinner {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  animation: btnspin 700ms linear infinite;
}
@keyframes btnspin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .spinner { animation-duration: 1.6s; }
}
</style>
