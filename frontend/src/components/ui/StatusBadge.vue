<script setup lang="ts">
/** 状态徽标：颜色 + 文字双表达，不允许只用颜色（03 §5、17 §12）。 */
withDefaults(
  defineProps<{ label: string; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'ai' }>(),
  { tone: 'neutral' },
)
</script>

<template>
  <span class="badge" :class="`is-${tone}`">
    <span class="dot" aria-hidden="true" />
    <span class="txt">{{ label }}</span>
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink);
  white-space: nowrap;
}
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.is-neutral { color: var(--ink-muted); }
.is-success { color: var(--success); border-color: rgba(25, 183, 122, 0.4); }
.is-warning { color: var(--warning); border-color: rgba(244, 166, 42, 0.4); }
.is-danger { color: var(--danger); border-color: rgba(232, 93, 117, 0.4); }
.is-info { color: var(--primary); border-color: rgba(53, 109, 255, 0.4); }
.is-ai { color: var(--accent-ai); border-color: rgba(124, 92, 252, 0.4); }

/* 移动端：徽标收小，避免一行只放得下两三个标签把列表撑高 */
@media (max-width: 640px) {
  .badge { height: 20px; padding: 0 7px; font-size: 11px; gap: 4px; }
  .dot { width: 5px; height: 5px; }
}
</style>
