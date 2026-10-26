<script setup lang="ts" generic="T extends Record<string, any>">
/**
 * 统一列表：表头固定、金额右对齐、支持空/错/加载状态；
 * 移动端自动切换为信息卡片（03 §5、05 §2、22 §6.2）。
 */
export interface Column {
  key: string
  label: string
  align?: 'left' | 'right' | 'center'
  width?: string
  primary?: boolean
}

withDefaults(
  defineProps<{
    columns: Column[]
    rows: T[]
    rowKey?: string
    loading?: boolean
    mobileTitleKey?: string
  }>(),
  { rowKey: 'id', loading: false },
)
</script>

<template>
  <div class="table-wrap">
    <table class="app-table">
      <thead>
        <tr>
          <th
            v-for="c in columns"
            :key="c.key"
            :style="{ textAlign: c.align || 'left', width: c.width }"
            :class="`ta-${c.align || 'left'}`"
          >
            {{ c.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading">
          <td :colspan="columns.length" class="state-cell">正在加载…</td>
        </tr>
        <tr v-else-if="!rows.length">
          <td :colspan="columns.length" class="state-cell">暂无数据</td>
        </tr>
        <tr v-for="row in rows" v-else :key="String(row[rowKey])">
          <td v-for="c in columns" :key="c.key" :class="`ta-${c.align || 'left'}`">
            <slot :name="c.key" :row="row">{{ row[c.key] ?? '—' }}</slot>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 移动端卡片视图 -->
    <div class="cards">
      <p v-if="!rows.length" class="state-cell">暂无数据</p>
      <article v-for="row in rows" v-else :key="String(row[rowKey])" class="mini-card">
        <header>{{ row[mobileTitleKey || columns[0]?.key] }}</header>
        <dl>
          <div v-for="c in columns.slice(1)" :key="c.key">
            <dt>{{ c.label }}</dt>
            <dd><slot :name="c.key" :row="row">{{ row[c.key] ?? '—' }}</slot></dd>
          </div>
        </dl>
      </article>
    </div>
  </div>
</template>

<style scoped>
.table-wrap { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.app-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.app-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--glass-3);
  backdrop-filter: blur(var(--glass-blur));
  color: var(--ink);
  font-weight: 600;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.app-table tbody td {
  padding: 11px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--ink);
}
.app-table tbody tr:hover { background: rgba(53, 109, 255, 0.06); }
.ta-right { text-align: right; }
.ta-center { text-align: center; }
.state-cell { text-align: center; color: var(--ink-muted); padding: var(--space-6); }

.cards { display: none; }
@media (max-width: 768px) {
  .app-table { display: none; }
  .cards { display: flex; flex-direction: column; gap: var(--space-3); }
  .mini-card {
    border: 1px solid var(--border);
    border-radius: var(--glass-radius-card);
    background: var(--glass-1);
    padding: var(--space-3) var(--space-4);
  }
  .mini-card header { font-weight: 600; color: var(--ink-strong); margin-bottom: 6px; }
  .mini-card dl { display: flex; flex-direction: column; gap: 4px; margin: 0; }
  .mini-card dl > div { display: flex; justify-content: space-between; gap: var(--space-3); }
  .mini-card dt { color: var(--ink-muted); font-size: 12.5px; }
  .mini-card dd { margin: 0; color: var(--ink-strong); text-align: right; }
  .mini-card dl > div { min-height: 28px; align-items: center; }
  .mini-card dd :deep(button), .mini-card dd :deep(a), .mini-card dd :deep(.act) {
    min-height: 34px; padding: 0 12px; display: inline-flex; align-items: center;
  }
}
</style>
