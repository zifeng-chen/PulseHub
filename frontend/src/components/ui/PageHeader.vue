<script setup lang="ts">
/** 页面头部：标题、面包屑、期间/币种提示与右侧操作区（05 §2）。 */
defineProps<{ title: string; subtitle?: string; breadcrumb?: string[] }>()
</script>

<template>
  <header class="page-head">
    <div class="left">
      <nav v-if="breadcrumb?.length" class="crumbs" aria-label="面包屑">
        <span v-for="(b, i) in breadcrumb" :key="i">
          {{ b }}<span v-if="i < breadcrumb.length - 1" class="sep">/</span>
        </span>
      </nav>
      <h1>{{ title }}</h1>
      <p v-if="subtitle" class="sub">{{ subtitle }}</p>
    </div>
    <div class="right"><slot /></div>
  </header>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
  flex-wrap: wrap;
}
.crumbs { font-size: 12px; color: var(--ink-muted); margin-bottom: 2px; }
.sep { margin: 0 6px; }
.sub { font-size: 13px; color: var(--ink-muted); margin-top: 4px; }
.right { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }

/* 移动 / 平板：顶部栏已经显示页面名，隐藏重复的大标题与面包屑，只留说明与操作 */
@media (max-width: 900px) {
  h1, .crumbs { display: none; }
}
/* 移动端：说明更紧凑，操作按钮变小并自动换行（不再一个按钮占一大块） */
@media (max-width: 760px) {
  .page-head { align-items: flex-start; gap: 10px; margin-bottom: var(--space-4); }
  h1 { font-size: 21px; line-height: 1.25; }
  .sub {
    font-size: 12.5px;
    line-height: 1.55;
    margin-top: 3px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .right { width: 100%; gap: 8px; }
  .right :deep(.app-btn) {
    height: 34px;
    padding: 0 12px;
    font-size: 12.5px;
  }
  .right :deep(.app-btn .label) { gap: 5px; }
}
</style>
