<script setup lang="ts">
/**
 * 统一抽屉（03 §5、17 §6.4）：详情与编辑优先使用右侧抽屉；
 * 移动端改为底部全屏面板（22 §6.2）。
 */
import { onBeforeUnmount, watch } from 'vue'

import { isTopOverlay, popOverlay, pushOverlay } from '@/composables/overlayStack'

const props = withDefaults(
  defineProps<{ open: boolean; title: string; width?: number; maskClosable?: boolean }>(),
  { width: 480, maskClosable: true },
)
const emit = defineEmits<{ close: [] }>()

const overlayId = Symbol('drawer')

function onKeydown(e: KeyboardEvent) {
  // 只有最上层响应 Esc：抽屉里开着弹窗时，Esc 先关弹窗而不是连抽屉一起关
  if (e.key === 'Escape' && props.open && isTopOverlay(overlayId)) emit('close')
}

watch(
  () => props.open,
  (v) => {
    if (v) pushOverlay(overlayId)
    else popOverlay(overlayId)
  },
  { immediate: true },
)

document.addEventListener('keydown', onKeydown)
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  popOverlay(overlayId)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="drawer-mask" :style="{ background: 'var(--scrim)' }" @click.self="maskClosable && emit('close')" />
    </Transition>
    <Transition name="slide">
      <aside
        v-if="open"
        class="drawer-panel"
        :style="{ '--drawer-width': `${width}px` }"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
      >
        <header class="drawer-head">
          <h3>{{ title }}</h3>
          <button class="close" type="button" aria-label="关闭" @click="emit('close')">×</button>
        </header>
        <div class="drawer-body"><slot /></div>
        <footer v-if="$slots.footer" class="drawer-foot"><slot name="footer" /></footer>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-mask { position: fixed; inset: 0; z-index: 1150; }
.drawer-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 1160;
  width: min(var(--drawer-width), 100vw);
  display: flex;
  flex-direction: column;
  background: var(--glass-3);
  border-left: var(--glass-border-line);
  box-shadow: var(--glass-shadow-heavy), var(--glass-inner-highlight);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
}
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border);
}
.close {
  width: 30px; height: 30px;
  border: none; background: transparent; color: var(--ink-muted);
  font-size: 20px; cursor: pointer; border-radius: var(--radius-sm);
}
.close:hover { color: var(--ink-strong); background: var(--glass-1); }
.drawer-body { flex: 1; overflow: auto; padding: var(--space-5); }
.drawer-foot {
  padding: var(--space-4) var(--space-5);
  padding-bottom: calc(var(--space-4) + var(--safe-bottom));
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

/* 移动端：底部全屏面板 */
@media (max-width: 768px) {
  .drawer-panel {
    top: auto;
    left: 0;
    width: 100%;
    height: 92vh;
    border-left: none;
    border-top: var(--glass-border-line);
    border-radius: var(--glass-radius-panel) var(--glass-radius-panel) 0 0;
  }
  .drawer-body { padding-bottom: calc(var(--space-5) + var(--safe-bottom)); }
  /* 底部操作按钮平分整行，拇指可达 */
  .drawer-foot { padding: 10px 16px calc(12px + var(--safe-bottom)); }
  .drawer-foot :deep(.app-btn) { flex: 1; height: 42px; }
}
</style>
