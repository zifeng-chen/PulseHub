<script setup lang="ts">
/**
 * 消费习惯分析 /insights/spending-habits（消费深度脉诊）。
 * 确定性统计：趋势环比、分类排行、大额消费、异常波动、周期性支出；不依赖模型。
 * 图表可配文字图例与数值；结论含口径、期间、来源与更新时间；不提供导出。
 */
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import {
  Coins, RefreshCw, Scale, Sparkles, TrendingUp, Wallet,
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

interface Panel { key: string; title: string; kind: 'pie' | 'line' | 'bar'; data: any }
interface LargeItem { title: string; amount: string; currency: string; occurred_at: string | null; category: string; counterparty: string | null }
interface Anomaly { name: string; current: string; previous: string; change_pct: number | null; kind: string; currency: string }
interface Recurring { name: string; appear_months: number; avg_monthly: string; currency: string; months: string[] }
interface Frequent { name: string; count: number }
interface HabitsData {
  period: string
  period_label: string
  base_currency: string
  data_updated_at: string
  source_count: number
  months: number
  caliber: string
  stats: { label: string; value: string }[]
  panels: Panel[]
  habits: {
    large_expenses: LargeItem[]
    anomalies: Anomaly[]
    recurring: Recurring[]
    frequent: Frequent[]
  }
  conclusions: string[]
}

const ui = useUiStore()
const theme = useThemeStore()
/** 指标卡图标轮换：洞察页的指标是动态的，按顺序套用统一视觉 */
const STAT_ICONS: Component[] = [Wallet, Scale, TrendingUp, Coins]
const STAT_TONES = ['primary', 'success', 'ai', 'warning'] as const

const loading = ref(true)
const error = ref('')
const data = ref<HabitsData | null>(null)

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
      data: items.map((i, idx) => ({
        name: i.name, value: Number(i.value),
        itemStyle: { color: PALETTE[idx % PALETTE.length] },
      })),
      label: { color: textColor.value, formatter: '{b}\n{d}%' },
      animationDuration: 500,
    }],
  }
}

function lineOption(panel: Panel) {
  const d = panel.data as { labels: string[]; series: { name: string; values: string[] }[] }
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { color: textColor.value } },
    grid: { left: 8, right: 14, top: 32, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category', data: d.labels || [],
      axisLine: { lineStyle: { color: axisColor.value } },
      axisLabel: { color: textColor.value },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: splitColor.value } },
      axisLabel: { color: textColor.value },
    },
    series: (d.series || []).map((s, idx) => ({
      name: s.name, type: 'line', smooth: true,
      data: (s.values || []).map((v) => Number(v)),
      itemStyle: { color: PALETTE[idx % PALETTE.length] },
      animationDuration: 500,
    })),
  }
}

function chartOption(panel: Panel) {
  if (panel.kind === 'pie') return pieOption(panel)
  return lineOption(panel)
}

function fmtDate(v: string | null) {
  return v ? new Date(v).toLocaleDateString('zh-CN') : '—'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<HabitsData>('/insights/spending-habits')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="habits-page">
    <PageHeader
      title="消费习惯分析"
      subtitle="确定性统计：趋势环比、分类排行、大额消费、异常波动、周期性支出；结论均含口径与数据更新时间。"
      :breadcrumb="['脉诊', '消费习惯分析']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="primary" @click="ui.openAiAnalysis('spending', 'AI 消费习惯解读')">
        <Sparkles :size="15" /> 让 AI 解读
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <section class="stats">

        <StatCard

          v-for="(s, i) in data.stats" :key="s.label"

          :icon="STAT_ICONS[i % STAT_ICONS.length]" :tone="STAT_TONES[i % STAT_TONES.length]"

          :label="s.label" :value="s.value" plain

        />

      </section>

      <AppCard v-for="p in data.panels" :key="p.key" :title="p.title">
        <EChart :option="chartOption(p)" />
      </AppCard>

      <div class="grid2">
        <AppCard :title="`大额消费（本期 Top ${data.habits.large_expenses.length}）`">
          <StateBlock v-if="!data.habits.large_expenses.length" state="empty" title="本期无支出" />
          <ul v-else class="list">
            <li v-for="(it, i) in data.habits.large_expenses" :key="i">
              <span class="li-t">{{ it.title }}</span>
              <span class="li-tag">{{ it.category }}</span>
              <span class="li-a">{{ it.amount }} {{ it.currency }}</span>
              <small>{{ fmtDate(it.occurred_at) }}</small>
            </li>
          </ul>
        </AppCard>

        <AppCard title="异常波动（环比明显上升）">
          <StateBlock v-if="!data.habits.anomalies.length" state="empty"
                      title="本期无异常波动" description="各分类支出环比均平稳。" />
          <ul v-else class="list">
            <li v-for="(a, i) in data.habits.anomalies" :key="i">
              <span class="li-t">{{ a.name }}</span>
              <span class="li-a">{{ a.current }} {{ a.currency }}</span>
              <small>{{ a.change_pct === null ? '上月无此项支出' : `较上月 +${a.change_pct}%（上月 ${a.previous}）` }}</small>
            </li>
          </ul>
        </AppCard>

        <AppCard title="周期性支出（每月出现）">
          <StateBlock v-if="!data.habits.recurring.length" state="empty"
                      title="暂无周期性支出" description="需要至少数月的支出数据。" />
          <ul v-else class="list">
            <li v-for="(r, i) in data.habits.recurring" :key="i">
              <span class="li-t">{{ r.name }}</span>
              <span class="li-tag">{{ r.appear_months }}/{{ data.months }} 月</span>
              <span class="li-a">月均 {{ r.avg_monthly }} {{ r.currency }}</span>
            </li>
          </ul>
        </AppCard>

        <AppCard title="高频分类（按笔数）">
          <StateBlock v-if="!data.habits.frequent.length" state="empty" title="本期无支出" />
          <ul v-else class="list">
            <li v-for="(f, i) in data.habits.frequent" :key="i">
              <span class="li-t">{{ f.name }}</span>
              <span class="li-a">{{ f.count }} 笔</span>
            </li>
          </ul>
        </AppCard>
      </div>

      <AppCard title="结论">
        <ul class="conclusions">
          <li v-for="(c, i) in data.conclusions" :key="i">{{ c }}</li>
        </ul>
        <p class="caliber">口径：{{ data.caliber }}</p>
        <p class="meta">
          期间：{{ data.period_label }} · 数据来源：{{ data.source_count }} 笔已确认支出 · 更新：{{ fmtDate(data.data_updated_at) }}
        </p>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.habits-page { display: flex; flex-direction: column; gap: var(--space-4); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(196px, 1fr)); gap: var(--space-3); }
.stat { display: flex; flex-direction: column; gap: 6px; }
.stat .k { font-size: 12.5px; color: var(--ink-muted); }
.stat .v { font-size: 20px; font-weight: 650; color: var(--ink-strong); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
@media (max-width: 860px) { .grid2 { grid-template-columns: 1fr; } }
.list { display: flex; flex-direction: column; gap: 6px; }
.list li { display: flex; align-items: center; gap: var(--space-2); padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1); flex-wrap: wrap; }
.li-t { font-size: 13px; color: var(--ink-strong); font-weight: 550; }
.li-tag { font-size: 11.5px; color: var(--ink-muted); border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; }
.li-a { margin-left: auto; font-size: 13px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.list small { font-size: 11.5px; color: var(--ink-muted); width: 100%; }
.conclusions { display: flex; flex-direction: column; gap: 6px; margin-bottom: var(--space-3); }
.conclusions li { font-size: 13.5px; color: var(--ink-strong); padding-left: 14px; position: relative; }
.conclusions li::before { content: '·'; position: absolute; left: 4px; color: var(--primary); font-weight: 700; }
.caliber { font-size: 12px; color: var(--ink-muted); }
.meta { font-size: 12px; color: var(--ink-muted); margin-top: 4px; }
</style>
