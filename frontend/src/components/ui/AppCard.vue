<script setup lang="ts">
/** 玻璃卡片（17 §6.1）。level 控制玻璃层级：1 弱 / 2 中 / 3 强。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ title?: string; level?: number | string; aurora?: boolean; padded?: boolean }>(),
  { level: 2, aurora: false, padded: true },
)

const levelClass = computed(() => {
  const n = Number(props.level)
  return `glass-${[1, 2, 3].includes(n) ? n : 2}`
})
</script>

<template>
  <section class="app-card" :class="[levelClass, { 'no-pad': !padded }]">
    <div v-if="aurora" class="aurora-layer" />
    <header v-if="title || $slots.header || $slots.extra" class="card-head">
      <div class="head-main">
        <slot name="header"><h3>{{ title }}</h3></slot>
      </div>
      <div class="head-extra"><slot name="extra" /></div>
    </header>
    <div class="card-body"><slot /></div>
    <footer v-if="$slots.footer" class="card-foot"><slot name="footer" /></footer>
  </section>
</template>

<style scoped>
.app-card {
  position: relative;
  border-radius: var(--glass-radius-card);
  border: var(--glass-border-line);
  box-shadow: var(--glass-shadow), var(--glass-inner-highlight);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  overflow: hidden;
}
.glass-1 { background: var(--glass-1); }
.glass-2 { background: var(--glass-2); }
.glass-3 { background: var(--glass-3); }

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5) 0;
}
.card-body { padding: var(--space-4) var(--space-5) var(--space-5); }
.no-pad .card-body { padding: 0; }
.card-foot {
  padding: var(--space-3) var(--space-5);
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
.head-extra { display: flex; align-items: center; gap: var(--space-2); }

/* 移动端：卡片内边距收紧，同样的内容能少滚一屏 */
@media (max-width: 640px) {
  .card-head { padding: 13px 16px 0; }
  .card-body { padding: 12px 16px 14px; }
  .card-foot { padding: 10px 16px; }
}
</style>
