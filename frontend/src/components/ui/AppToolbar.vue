<script setup lang="ts">
/**
 * 筛选工具条（移动端优先）。
 *
 * 桌面端：所有筛选控件自动换行，行为与旧 `.toolbar` 一致。
 * 移动端（≤760px）：
 * - 搜索框独占一行；
 * - 其余筛选控件压缩成一行内的紧凑控件（高度 34px、最小宽度 104px），
 *   不再「一个筛选一张行」把首屏挤满；
 * - 传了 `#advanced` 时，低频筛选收进底部弹层，由「筛选 n」按钮打开，
 *   高频条件仍留在外面，手机上也能一眼看到。
 */
import { ref } from 'vue'
import { SlidersHorizontal, X } from 'lucide-vue-next'
import AppButton from '@/components/ui/AppButton.vue'

const props = withDefaults(defineProps<{
  /** 当前生效的筛选条件数量，用于按钮高亮与角标 */
  activeCount?: number
  /** 弹层标题 */
  sheetTitle?: string
  /** 刷新按钮文案（无则显示「刷新」） */
  resetText?: string
}>(), {
  activeCount: 0,
  sheetTitle: '筛选条件',
  resetText: '重置',
})

const emit = defineEmits<{ reset: []; apply: [] }>()
const open = ref(false)

function close() {
  open.value = false
}

function reset() {
  emit('reset')
  // 重置不关闭弹层：用户可能只想清空后重新挑条件
}

function apply() {
  emit('apply')
  open.value = false
}
</script>

<template>
  <div class="app-toolbar">
    <div class="tb-main">
      <slot />
      <button
        v-if="$slots.advanced"
        class="tb-filter" :class="{ on: props.activeCount > 0 }"
        type="button" @click="open = true"
      >
        <SlidersHorizontal :size="14" />
        筛选
        <span v-if="props.activeCount" class="count">{{ props.activeCount }}</span>
      </button>
    </div>

    <div v-if="$slots.advanced" class="tb-adv"><slot name="advanced" /></div>
  </div>

  <Teleport to="body">
    <div v-if="open" class="fs-wrap" role="dialog" aria-modal="true" :aria-label="sheetTitle">
      <div class="fs-mask" @click="close" />
      <section class="fs-panel">
        <header class="fs-head">
          <strong>{{ sheetTitle }}</strong>
          <button class="fs-close" type="button" aria-label="关闭" @click="close">
            <X :size="18" />
          </button>
        </header>
        <div class="fs-body"><slot name="advanced" /></div>
        <footer class="fs-foot">
          <AppButton variant="secondary" @click="reset">{{ resetText }}</AppButton>
          <AppButton variant="primary" @click="apply">完成</AppButton>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.app-toolbar {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}
.tb-main {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  width: 100%;
  min-width: 0;
}
/* 桌面端：高级筛选单独一行，主筛选自己换行，互不挤压 */
.tb-adv {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  width: 100%;
}
.tb-filter { display: none; }

/* ---------------- 移动端：一行装下筛选，不再一人一行 ---------------- */
@media (max-width: 760px) {
  .app-toolbar { gap: 8px; }
  .tb-main { gap: 8px; }

  /* 搜索框独占一行，其余控件紧凑排列 */
  .tb-main :deep(.search) {
    flex: 1 1 100%;
    height: 36px;
  }
  .tb-main :deep(.select) {
    width: auto;
    min-width: 0;
    height: 34px;
    padding: 0 8px;
    font-size: 13px;
  }
  .tb-main :deep(.sel),
  .tb-main :deep(.sel2) {
    flex: 1 1 0;
    width: auto;
    min-width: 88px;
  }
  /* 文本输入（金额 / 关键词）与选择器同高 */
  .tb-main :deep(.wrap) {
    flex: 1 1 110px;
    min-width: 96px;
  }
  .tb-main :deep(.wrap .input) { height: 34px; font-size: 13px; }
  /* 原生搜索输入（租赁页）：与分段控件同排，剩余宽度自适应 */
  .tb-main :deep(.search-input) {
    flex: 1 1 140px;
    min-width: 0;
    height: 34px;
    font-size: 13px;
  }
  /* 分段切换（租约 / 房产）压缩，别和新版筛选抢空间 */
  .tb-main :deep(.seg),
  .tb-main :deep(.seg2) { flex: 0 0 auto; }
  .tb-main :deep(.seg button),
  .tb-main :deep(.seg2 button) { height: 28px; padding: 0 12px; font-size: 12.5px; }

  /* 日期区间：整行或半行，不占满屏 */
  .tb-main :deep(.dr) { flex: 1 1 100%; }
  .tb-main :deep(.period) { flex: 1 1 100%; width: auto; }
  /* 工具条内的按钮不拉伸 */
  .tb-main :deep(.app-btn) { flex: 0 0 auto; height: 34px; padding: 0 12px; }
  /* 「查询」在移动端不出现：筛选条件改动即查询，关键词回车即查询，
     高级条件在弹层里点「完成」也会重新查询，留着只会多占一行 */
  .tb-main :deep(.app-btn.tb-query) { display: none; }

  /* 移动端隐藏内联的高级筛选，改由弹层承载 */
  .tb-adv { display: none; }

  .tb-filter {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex: 0 0 auto;
    height: 34px;
    padding: 0 12px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--glass-1);
    color: var(--ink-strong);
    font-size: 13px;
    cursor: pointer;
  }
  .tb-filter.on { border-color: var(--primary); color: var(--primary); }
  .tb-filter .count {
    display: inline-grid;
    place-items: center;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 999px;
    background: var(--primary);
    color: #fff;
    font-size: 10.5px;
    font-weight: 600;
  }
}

/* ---------------- 底部筛选弹层 ---------------- */
.fs-wrap { position: fixed; inset: 0; z-index: 60; }
.fs-mask {
  position: absolute;
  inset: 0;
  background: rgba(8, 13, 24, 0.48);
  backdrop-filter: blur(2px);
  animation: fs-fade 160ms ease-out;
}
.fs-panel {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  max-height: 82vh;
  border-radius: 18px 18px 0 0;
  border-top: 1px solid var(--border);
  background: var(--surface);
  box-shadow: 0 -18px 48px rgba(8, 13, 24, 0.32);
  animation: fs-up 220ms var(--ease-out, ease-out);
}
.fs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--border);
}
.fs-head strong { font-size: 15px; color: var(--ink-strong); }
.fs-close {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
}
.fs-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  overflow-y: auto;
}
.fs-body :deep(.field) { gap: 6px; }
/* 弹层底部已有「重置 / 完成」，正文里的按钮（桌面端用的）在弹层里不再重复出现 */
.fs-body :deep(.app-btn) { display: none; }
.fs-body :deep(.select),
.fs-body :deep(.wrap .input) { height: 40px; }
.fs-foot {
  display: flex;
  gap: 10px;
  padding: 10px 16px calc(14px + var(--safe-bottom, 0px));
  border-top: 1px solid var(--border);
}
.fs-foot :deep(.app-btn) { flex: 1; height: 42px; }

@keyframes fs-fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes fs-up { from { transform: translateY(18px); } to { transform: translateY(0); } }
@media (prefers-reduced-motion: reduce) {
  .fs-mask, .fs-panel { animation: none; }
}
</style>
