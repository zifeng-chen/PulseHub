<script setup lang="ts">
/** 脉诊主题页（07 §2–§7）：通用渲染后端 panels；图表可切表格视图；不提供导出。 */
import { computed, onMounted, ref, watch } from 'vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChartColumn, Coins, Scale, Sparkles, Table2, TrendingUp, Wallet,
} from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useUiStore } from '@/stores/ui'
import { useThemeStore } from '@/stores/theme'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import EChart from '@/components/ui/EChart.vue'

interface Panel {
  key: string
  title: string
  kind: 'pie' | 'line' | 'bar' | 'stat' | 'table'
  data: any
}
interface TopicData {
  topic: string
  title: string
  period: string
  period_label: string
  period_range: string
  base_currency: string
  data_updated_at: string
  source_count: number
  panels: Panel[]
  stats: { label: string; value: string }[]
  conclusions: string[]
}

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const theme = useThemeStore()
/** 指标卡图标轮换：洞察页的指标是动态的，按顺序套用统一视觉 */
const STAT_ICONS: Component[] = [Wallet, Scale, TrendingUp, Coins]
const STAT_TONES = ['primary', 'success', 'ai', 'warning'] as const

const loading = ref(true)
const error = ref('')
const data = ref<TopicData | null>(null)
const view = ref<'chart' | 'table'>('chart')

const topicKey = computed(() => {
  const m = route.name as string
  return m.replace('insights-', '')
})
const textColor = computed(() => (theme.resolved === 'dark' ? '#C3CCDE' : '#445069'))
const splitColor = computed(() => (theme.resolved === 'dark' ? 'rgba(255,255,255,.10)' : '#E7EBF2'))
const axisColor = computed(() => (theme.resolved === 'dark' ? 'rgba(255,255,255,.16)' : '#E7EBF2'))
const PALETTE = ['#356DFF', '#19B77A', '#F4A62A', '#E1574B', '#8A6CFF', '#19A7C7', '#5B8DEF', '#C98A2D']

function pieOption(panel: Panel) {
  const items = (panel.data as { name: string; value: string }[]) || []
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { color: textColor.value } },
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      center: ['50%', '44%'],
      data: items.map((i, idx) => ({ name: i.name, value: Number(i.value), itemStyle: { color: PALETTE[idx % PALETTE.length] } })),
      label: { color: textColor.value, formatter: '{b}\n{d}%' },
      animationDuration: 500,
    }],
  }
}

function axisOption(panel: Panel, seriesType: 'line' | 'bar') {
  const d = panel.data as { labels: string[]; series: { name: string; values: string[] }[] }
  const labels = d.labels || []
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { color: textColor.value } },
    grid: { left: 8, right: 14, top: 32, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category', data: labels,
      axisLine: { lineStyle: { color: axisColor.value } },
      axisLabel: { color: textColor.value },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: splitColor.value } },
      axisLabel: { color: textColor.value },
    },
    series: (d.series || []).map((s, idx) => ({
      name: s.name, type: seriesType, barMaxWidth: 26,
      data: (s.values || []).map((v) => Number(v)),
      itemStyle: seriesType === 'line' ? { color: PALETTE[idx % PALETTE.length] } : { color: PALETTE[idx % PALETTE.length], borderRadius: [4, 4, 0, 0] },
      smooth: seriesType === 'line',
      animationDuration: 500,
    })),
  }
}

function chartOption(panel: Panel) {
  if (panel.kind === 'pie') return pieOption(panel)
  if (panel.kind === 'line') return axisOption(panel, 'line')
  if (panel.kind === 'bar') return axisOption(panel, 'bar')
  return {}
}

function tableRows(panel: Panel): { columns: string[]; rows: (string | null)[][] } {
  const d = panel.data as { columns: string[]; rows: (string | null)[][] }
  return { columns: d.columns || [], rows: d.rows || [] }
}

function fmtUpdated(v: string) {
  if (!v) return '—'
  const d = new Date(v)
  return `${d.toLocaleDateString('zh-CN')} ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
}

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  const topic = topicKey.value
  try {
    const result = await api.get<TopicData>(`/insights/${topic}`)
    // 主题已切换：丢弃过期响应，避免「消费分析」页显示「收入分析」的数据（M-1）
    if (seq !== loadSeq || topic !== topicKey.value) return
    data.value = result
  } catch (err) {
    if (seq === loadSeq && topic === topicKey.value) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(load)

// 资产分析 / 收入分析 / 消费分析等主题共用本组件：切换路由时组件复用、onMounted 不重跑，
// 必须监听主题变化重新拉取，否则三个页面切换没有任何界面变化。
watch(topicKey, () => {
  if (!topicKey.value) return
  data.value = null
  load()
})

// 无数据时的空状态提示与跳转入口（投资/租赁等未录入数据的主题，避免渲染成一片全零卡片、像「没变化」）
const EMPTY_HINT: Record<string, { title: string; desc: string; to: string; btn: string }> = {
  investments: {
    title: '还没有投资数据',
    desc: '录入投资账户与持仓后，这里会展示持仓占比、成本市值与已实现/未实现损益。',
    to: '/ledger/investments', btn: '去录入投资',
  },
  leases: {
    title: '还没有租赁数据',
    desc: '建立租约并登记租金实收后，这里会展示收租率、逾期金额与押金余额。',
    to: '/ledger/leases', btn: '去录入租约',
  },
}
const emptyHint = computed(() => {
  if (!data.value || data.value.source_count > 0) return null
  return EMPTY_HINT[topicKey.value] || null
})
</script>

<template>
  <div class="topic-page">
    <PageHeader
      :title="(route.meta.title as string)"
      subtitle="图表支持加载、空数据、部分数据、接口错误与无权限状态；颜色必须有文字图例与数值；可切换表格视图，不提供导出。"
      :breadcrumb="(route.meta.breadcrumb as string[]) || []"
    >
      <div class="view-switch">
        <button :class="{ on: view === 'chart' }" type="button" @click="view = 'chart'">
          <ChartColumn :size="14" /> 图表
        </button>
        <button :class="{ on: view === 'table' }" type="button" @click="view = 'table'">
          <Table2 :size="14" /> 表格
        </button>
      </div>
      <AppButton variant="primary" @click="ui.openAiAnalysis('insights', `AI 分析：${route.meta.title}`)">
        <Sparkles :size="15" /> 生成分析
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
    <StateBlock
      v-else-if="emptyHint"
      state="empty"
      :title="emptyHint.title"
      :description="emptyHint.desc"
    >
      <template #actions>
        <AppButton variant="primary" @click="router.push(emptyHint.to)">{{ emptyHint.btn }}</AppButton>
      </template>
    </StateBlock>

    <template v-else-if="data">
      <!-- 顶部统计 -->
      <section v-if="data.stats.length" class="stats">

        <StatCard

          v-for="(s, i) in data.stats" :key="s.label"

          :icon="STAT_ICONS[i % STAT_ICONS.length]" :tone="STAT_TONES[i % STAT_TONES.length]"

          :label="s.label" :value="s.value" plain

        />

      </section>

      <!-- 图表面板 -->
      <AppCard v-for="p in data.panels" :key="p.key" :title="p.title">
        <template v-if="view === 'chart'">
          <EChart v-if="['pie', 'line', 'bar'].includes(p.kind)" :option="chartOption(p)" />
          <template v-else-if="p.kind === 'stat'">
            <div class="stat-list">
              <div v-for="(v, k) in p.data" :key="k">
                <span class="k">{{ k }}</span><span class="v">{{ v }}</span>
              </div>
            </div>
          </template>
          <table v-else class="table">
            <thead><tr><th v-for="c in tableRows(p).columns" :key="c">{{ c }}</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in tableRows(p).rows" :key="i">
                <td v-for="(cell, j) in row" :key="j">{{ cell ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <template v-else>
          <table class="table">
            <tbody>
              <tr v-for="(v, k) in p.data" :key="k">
                <td class="th">{{ k }}</td>
                <td>{{ Array.isArray(v) ? JSON.stringify(v) : v }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </AppCard>

      <!-- 结论 -->
      <AppCard title="分析结论">
        <ul class="conclusions">
          <li v-for="(c, i) in data.conclusions" :key="i">{{ c }}</li>
        </ul>
      </AppCard>

      <p class="caliber">
        口径与更新时间：基础币种 {{ data.base_currency }} · 期间 {{ data.period_label }}（{{ data.period_range }}） ·
        来源数量 {{ data.source_count }} · 数据更新时间 {{ fmtUpdated(data.data_updated_at) }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.topic-page { display: flex; flex-direction: column; gap: var(--space-4); }
.view-switch {
  display: inline-flex; gap: 2px; padding: 2px;
  border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1);
}
.view-switch button {
  display: inline-flex; align-items: center; gap: 5px;
  height: 28px; padding: 0 10px; border: none; border-radius: 9px;
  background: transparent; color: var(--ink-muted); font-size: 12.5px; cursor: pointer;
}
.view-switch button.on { color: var(--ink-strong); background: var(--glass-3); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(196px, 1fr)); gap: var(--space-3); }
/* 标签与数值要上下两行：卡片的根节点是 AppCard，纵向排列要落在 body 上 */
.stat :deep(.card-body) { display: flex; flex-direction: column; gap: 3px; }
.stat .k, .stat .v { display: block; }
@media (max-width: 640px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .stat :deep(.card-body) { padding: 10px 11px 11px; }
  .stat .k { font-size: 11px; }
  .stat .v { font-size: 14.5px; }
}
.stat .k { font-size: 12px; color: var(--ink-muted); }
.stat .v { font-size: 16px; font-weight: 600; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.stat-list { display: flex; flex-direction: column; gap: 8px; }
.stat-list > div { display: flex; justify-content: space-between; font-size: 13px; }
.stat-list .k { color: var(--ink-muted); }
.stat-list .v { color: var(--ink-strong); }
.table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.table th, .table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
.table th { color: var(--ink-muted); font-weight: 600; }
.table td.th { color: var(--ink-muted); width: 40%; }
.conclusions { display: flex; flex-direction: column; gap: 8px; }
.conclusions li { font-size: 13px; color: var(--ink); line-height: 1.7; list-style: disc; margin-left: 18px; }
.caliber { font-size: 12px; color: var(--ink-muted); }
</style>
