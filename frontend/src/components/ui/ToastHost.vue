<script setup lang="ts">
/** Toast 宿主（03 §5：统一反馈组件）。 */
import { useToastStore } from '@/stores/toast'
const toast = useToastStore()
const icons: Record<string, string> = { success: '✓', error: '!', warning: '△', info: 'i' }
</script>

<template>
  <div class="toast-host" aria-live="polite">
    <TransitionGroup name="pop">
      <div v-for="t in toast.items" :key="t.id" class="toast" :class="`is-${t.kind}`" role="status">
        <span class="ic" aria-hidden="true">{{ icons[t.kind] }}</span>
        <span class="msg">{{ t.message }}</span>
        <button class="x" type="button" aria-label="关闭提示" @click="toast.dismiss(t.id)">×</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  top: calc(var(--space-4) + var(--safe-top));
  right: var(--space-4);
  z-index: 1400;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: min(380px, calc(100vw - 32px));
}
.toast {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: 10px 12px;
  border-radius: var(--glass-radius-control);
  background: var(--glass-3);
  border: var(--glass-border-line);
  box-shadow: var(--glass-shadow);
  backdrop-filter: blur(var(--glass-blur));
  font-size: 13.5px;
  color: var(--ink-strong);
}
.ic { font-weight: 700; line-height: 1.4; }
.is-success .ic { color: var(--success); }
.is-error .ic { color: var(--danger); }
.is-warning .ic { color: var(--warning); }
.is-info .ic { color: var(--primary); }
.msg { flex: 1; }
.x { border: none; background: transparent; color: var(--ink-muted); cursor: pointer; font-size: 16px; line-height: 1; }
@media (max-width: 768px) {
  .toast-host { left: var(--space-4); right: var(--space-4); max-width: none; }
}
</style>
