<script setup lang="ts">
/** 页签（03 §6：下划线滑动 180ms；移动端横向滚动并显示滚动提示）。 */
import { useRoute, useRouter } from 'vue-router'
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{ tabs: { label: string; path: string }[]; sticky?: boolean }>()
const route = useRoute()
const router = useRouter()
const indicator = ref({ left: 0, width: 0 })
const listRef = ref<HTMLElement | null>(null)

function updateIndicator() {
  const el = listRef.value?.querySelector<HTMLElement>('.tab.is-active')
  if (el && listRef.value) {
    const parent = listRef.value.getBoundingClientRect()
    const rect = el.getBoundingClientRect()
    indicator.value = { left: rect.left - parent.left + listRef.value.scrollLeft, width: rect.width }
  }
}

watch(() => route.path, () => nextTick(updateIndicator), { immediate: true })
</script>

<template>
  <div class="tabs" :class="{ sticky }">
    <div ref="listRef" class="tab-list" role="tablist">
      <button
        v-for="t in tabs"
        :key="t.path"
        class="tab"
        :class="{ 'is-active': route.path === t.path }"
        type="button"
        role="tab"
        :aria-selected="route.path === t.path ? 'true' : 'false'"
        @click="router.push(t.path)"
      >
        {{ t.label }}
      </button>
      <span class="indicator" :style="{ left: `${indicator.left}px`, width: `${indicator.width}px` }" />
    </div>
    <p class="scroll-hint" aria-hidden="true">← 左右滑动查看更多</p>
  </div>
</template>

<style scoped>
.tabs { border-bottom: 1px solid var(--border); }
.sticky { position: sticky; top: var(--topbar-height); z-index: 5; background: var(--glass-2); backdrop-filter: blur(var(--glass-blur)); }
.tab-list { position: relative; display: flex; gap: var(--space-1); overflow-x: auto; padding: 0 var(--space-2); scrollbar-width: none; }
.tab-list::-webkit-scrollbar { display: none; }
.tab {
  position: relative;
  flex: 0 0 auto;
  height: 42px;
  padding: 0 14px;
  border: none;
  background: transparent;
  color: var(--ink-muted);
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
}
.tab:hover { color: var(--ink-strong); }
.tab.is-active { color: var(--primary); font-weight: 600; }
.indicator {
  position: absolute;
  bottom: 0;
  height: 2px;
  background: var(--primary);
  border-radius: 2px;
  transition: left 180ms var(--ease-out), width 180ms var(--ease-out);
}
.scroll-hint { display: none; }
@media (max-width: 768px) {
  /* 只留一行极小的提示，别再单独吃掉一行高度 */
  .scroll-hint {
    display: block; font-size: 10px; line-height: 1.3; color: var(--ink-muted);
    padding: 0 12px 2px; text-align: right; opacity: 0.8;
  }
}
@media (prefers-reduced-motion: reduce) {
  .indicator { transition: none; }
}
</style>
