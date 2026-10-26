<script setup lang="ts">
/**
 * 悬浮说明：一个小问号，悬停/聚焦/点击时显示解释文字。
 *
 * 气泡用 Teleport 挂到 body 并按按钮位置 fixed 定位：
 * 卡片自带 overflow:hidden 与 backdrop-filter（会创建层叠上下文），
 * 气泡放在卡片里会被裁掉一半、或被相邻卡片盖住。挂到 body 后永远在最上层。
 */
import { onBeforeUnmount, ref } from 'vue'
import { CircleHelp } from 'lucide-vue-next'

withDefaults(
  defineProps<{
    text: string
    /** 无障碍标签，例如「净资产口径」 */
    label?: string
    size?: number
  }>(),
  { label: '说明', size: 14 },
)

const open = ref(false)
const anchor = ref<HTMLElement | null>(null)
const pos = ref({ top: 0, left: 0 })
const BUBBLE_MAX = 300
const GUTTER = 12

function place() {
  const el = anchor.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const half = Math.min(BUBBLE_MAX, window.innerWidth - GUTTER * 2) / 2
  const centered = rect.left + rect.width / 2
  pos.value = {
    top: rect.bottom + 8,
    // 贴边时夹住，避免气泡被视口裁掉
    left: Math.min(Math.max(centered, half + GUTTER), window.innerWidth - half - GUTTER),
  }
}

function show() {
  place()
  open.value = true
}

function hide() {
  open.value = false
}

/** 滚动或改变窗口时直接收起，避免气泡「飘」在原处 */
function onViewportChange() {
  if (open.value) open.value = false
}

if (typeof window !== 'undefined') {
  window.addEventListener('scroll', onViewportChange, true)
  window.addEventListener('resize', onViewportChange)
}
onBeforeUnmount(() => {
  window.removeEventListener('scroll', onViewportChange, true)
  window.removeEventListener('resize', onViewportChange)
})
</script>

<template>
  <span class="hint" @mouseenter="show" @mouseleave="hide">
    <button
      ref="anchor"
      type="button"
      class="q"
      :aria-label="label"
      :aria-expanded="open"
      @focus="show"
      @blur="hide"
      @click.stop.prevent="open ? hide() : show()"
    >
      <CircleHelp :size="size" />
    </button>
  </span>

  <Teleport to="body">
    <span
      v-if="open && text"
      class="hint-bubble"
      role="tooltip"
      :style="{ top: `${pos.top}px`, left: `${pos.left}px` }"
    >{{ text }}</span>
  </Teleport>
</template>

<style scoped>
.hint { display: inline-flex; align-items: center; vertical-align: middle; }
.q {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 0; border: 0; background: none; cursor: help;
  color: var(--ink-muted); opacity: 0.8; transition: opacity 0.15s ease, color 0.15s ease;
}
.q:hover, .q[aria-expanded='true'] { opacity: 1; color: var(--primary); }
</style>

