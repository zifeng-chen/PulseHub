<script setup lang="ts" generic="T extends Record<string, any>">
/**
 * 统一列表：表头固定、金额右对齐、支持空/错/加载状态；
 * 移动端自动切换为信息卡片（03 §5、05 §2、22 §6.2）。
 *
 * 传入 groupBy 后按维度分组，分组条可点击折叠——流水这类「一天多条」的列表
 * 折叠后能快速跳到想看的那一天，不用一路滚。
 */
import { computed, ref } from 'vue'

export interface Column {
  key: string
  label: string
  align?: 'left' | 'right' | 'center'
  width?: string
  primary?: boolean
}

const props = withDefaults(
  defineProps<{
    columns: Column[]
    rows: T[]
    rowKey?: string
    loading?: boolean
    mobileTitleKey?: string
    /** 行级样式：深链定位（?focus=）时给目标行加 .focus-hit */
    rowClass?: (row: T) => string
    /** 按某维度分组：返回相同 key 的相邻行会归到同一组，组前插入一行小标题 */
    groupBy?: (row: T) => string
    /** 组标题文案（可带当日合计等） */
    groupLabel?: (key: string, rows: T[]) => string
  }>(),
  { rowKey: 'id', loading: false },
)

/** 模板里对可选 prop 做非空收窄，避免 TS2722 */
const groupOf = (row: T): string => (props.groupBy ? props.groupBy(row) : '')

/** 已折叠的分组 key，默认全部展开 */
const collapsedGroups = ref<Set<string>>(new Set())

/** 预分组：组标题要显示笔数，模板里就不必反复 filter */
const groupRows = computed(() => {
  const map = new Map<string, T[]>()
  const groupBy = props.groupBy
  if (!groupBy) return map
  for (const row of props.rows) {
    const key = groupBy(row)
    const list = map.get(key)
    if (list) list.push(row)
    else map.set(key, [row])
  }
  return map
})

const rowsOfGroup = (key: string): T[] => groupRows.value.get(key) ?? []
const isCollapsed = (key: string): boolean => collapsedGroups.value.has(key)
const isGroupStart = (row: T, index: number): boolean =>
  index === 0 || groupOf(props.rows[index - 1]!) !== groupOf(row)
const labelOfGroup = (key: string): string => props.groupLabel?.(key, rowsOfGroup(key)) ?? key

function toggleGroup(key: string): void {
  const next = new Set(collapsedGroups.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedGroups.value = next
}

/** 移动端卡片：把「日期标题」与「卡片」拍平成一个列表，折叠时跳过该组卡片 */
interface CardEntry {
  key: string
  groupKey: string | null
  row: T | null
}

const cardEntries = computed<CardEntry[]>(() => {
  const out: CardEntry[] = []
  const groupBy = props.groupBy
  if (!groupBy) {
    for (const row of props.rows) out.push({ key: String(row[props.rowKey]), groupKey: null, row })
    return out
  }
  const seen = new Set<string>()
  for (const row of props.rows) {
    const key = groupBy(row)
    if (!seen.has(key)) {
      seen.add(key)
      out.push({ key: `g:${key}`, groupKey: key, row: null })
    }
    if (!collapsedGroups.value.has(key)) {
      out.push({ key: String(row[props.rowKey]), groupKey: null, row })
    }
  }
  return out
})
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
        <template v-if="groupBy && !loading && rows.length">
          <template v-for="(row, idx) in rows" :key="String(row[rowKey])">
            <tr v-if="isGroupStart(row, idx)" class="group-row">
              <td :colspan="columns.length">
                <button
                  type="button"
                  class="group-bar"
                  :class="{ 'is-collapsed': isCollapsed(groupOf(row)) }"
                  :aria-expanded="!isCollapsed(groupOf(row))"
                  @click="toggleGroup(groupOf(row))"
                >
                  <span class="chev" aria-hidden="true">▾</span>
                  <span class="g-label">{{ labelOfGroup(groupOf(row)) }}</span>
                  <span class="g-count">{{ rowsOfGroup(groupOf(row)).length }} 笔</span>
                </button>
              </td>
            </tr>
            <tr v-if="!isCollapsed(groupOf(row))" :class="rowClass?.(row)">
              <td v-for="c in columns" :key="c.key" :class="`ta-${c.align || 'left'}`">
                <slot :name="c.key" :row="row">{{ row[c.key] ?? '—' }}</slot>
              </td>
            </tr>
          </template>
        </template>
        <tr v-else-if="loading">
          <td :colspan="columns.length" class="state-cell">正在加载…</td>
        </tr>
        <tr v-else-if="!rows.length">
          <td :colspan="columns.length" class="state-cell">暂无数据</td>
        </tr>
        <tr v-for="row in rows" v-else :key="String(row[rowKey])" :class="rowClass?.(row)">
          <td v-for="c in columns" :key="c.key" :class="`ta-${c.align || 'left'}`">
            <slot :name="c.key" :row="row">{{ row[c.key] ?? '—' }}</slot>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 移动端卡片视图 -->
    <div class="cards">
      <p v-if="loading" class="state-cell">正在加载…</p>
      <p v-else-if="!rows.length" class="state-cell">暂无数据</p>
      <template v-else>
        <template v-for="entry in cardEntries" :key="entry.key">
          <button
            v-if="entry.groupKey"
            type="button"
            class="group-bar group-bar-card"
            :class="{ 'is-collapsed': isCollapsed(entry.groupKey) }"
            :aria-expanded="!isCollapsed(entry.groupKey)"
            @click="toggleGroup(entry.groupKey)"
          >
            <span class="chev" aria-hidden="true">▾</span>
            <span class="g-label">{{ labelOfGroup(entry.groupKey) }}</span>
            <span class="g-count">{{ rowsOfGroup(entry.groupKey).length }} 笔</span>
          </button>
          <article v-else class="mini-card" :class="rowClass?.(entry.row!)">
            <header>{{ entry.row![mobileTitleKey || columns[0]?.key] }}</header>
            <dl>
              <div v-for="c in columns.slice(1)" :key="c.key">
                <dt>{{ c.label }}</dt>
                <dd><slot :name="c.key" :row="entry.row!">{{ entry.row![c.key] ?? '—' }}</slot></dd>
              </div>
            </dl>
          </article>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.table-wrap { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }

/* 分组条：日期高亮 + 可折叠。原来只是浅灰小字，和明细几乎分不开 */
.group-row td {
  padding: 12px 8px 2px;
  border-bottom: none;
  background: transparent;
}
.app-table tbody tr.group-row:hover { background: transparent; }
.group-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  border-radius: 10px;
  background: linear-gradient(
    100deg,
    rgba(53, 109, 255, 0.18),
    rgba(53, 109, 255, 0.05) 58%,
    rgba(53, 109, 255, 0.02)
  );
  box-shadow: inset 3px 0 0 var(--primary);
  color: var(--ink-strong);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  transition: background var(--motion-fast) var(--ease-out);
}
.group-bar:hover {
  background: linear-gradient(
    100deg,
    rgba(53, 109, 255, 0.26),
    rgba(53, 109, 255, 0.09) 58%,
    rgba(53, 109, 255, 0.03)
  );
}
.group-bar:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.group-bar .chev {
  font-size: 11px;
  line-height: 1;
  color: var(--primary);
  transition: transform var(--motion-fast) var(--ease-out);
}
.group-bar.is-collapsed .chev { transform: rotate(-90deg); }
.group-bar.is-collapsed {
  background: linear-gradient(100deg, rgba(53, 109, 255, 0.1), rgba(53, 109, 255, 0.02) 58%, transparent);
  box-shadow: inset 3px 0 0 var(--ink-muted);
}
.group-bar .g-count {
  margin-left: auto;
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ink-muted);
}
.group-bar-card { margin-top: 2px; }

:root[data-theme='dark'] .group-bar {
  background: linear-gradient(100deg, rgba(53, 109, 255, 0.34), rgba(53, 109, 255, 0.1) 58%, transparent);
}
:root[data-theme='dark'] .group-bar.is-collapsed {
  background: linear-gradient(100deg, rgba(53, 109, 255, 0.18), rgba(53, 109, 255, 0.04) 58%, transparent);
}

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
