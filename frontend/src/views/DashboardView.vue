<script setup lang="ts">
/**
 * 脉枢台 /dashboard（05 §2、18 §4 M1 基础统计）。
 * 所有金额与图表数据均来自后端确定性统计接口，前端不自行计算。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight, Building2, ClipboardList, Coins, CreditCard, Landmark, RefreshCw, Sparkles,
  TrendingUp, Upload, Wallet,
} from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { formatAmount } from '@/utils/format'
import { useUiStore } from '@/stores/ui'
import { useThemeStore } from '@/stores/theme'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppHint from '@/components/ui/AppHint.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import EChart from '@/components/ui/EChart.vue'

interface CurrencyTotal { currency: string; total: string; in_net_worth: string; count: number }
interface AssetStructurePoint { asset_type: string; asset_type_label: string; value: string; currency: string }
interface SeriesPoint { month: string; label: string; value?: string; income?: string; expense?: string; balance?: string }
interface TimelineItem {
  id: string; event_no: string; event_type: string; title: string
  amount: string; currency: string; status: string; occurred_at: string | null
}

interface Snapshot {
  period: string
  period_label: string
  period_range: string
  base_currency: string
  currency_badge: string
  multi_currency: boolean
  caliber: Record<string, string | boolean>
  counts: Record<string, number>
  net_worth: {
    total_assets: string; total_liabilities: string; net_worth: string
    accounts?: string; assets?: string; investments?: string; investment_included?: boolean
    change_since_previous: string; included_accounts: number; included_assets?: number
    included_liabilities?: number; included_investments?: number
    debt_ratio?: string; debt_ratio_level?: 'ok' | 'warn' | 'bad'; debt_ratio_note?: string
    by_currency: CurrencyTotal[]; assets_by_currency?: { currency: string; total: string; count: number }[]
    note: string
  }
  metrics: Record<string, string | number>
  liquidity?: {
    value: string; cash_accounts?: string; account_cash: string; invest_cash: string; note: string
    runway_months: string | null; monthly_expense: string
    runway_months_dynamic?: number | null
    runway_assumptions?: { invest_return: string; inflation: string; max_months: number }
    runway_level: 'ok' | 'warn' | 'bad' | 'unknown'; runway_note: string
  }
  /** 偿债能力：流动资金不扣待还，用覆盖倍数回答「够不够还」 */
  debt_service?: {
    credit_card_due: string; credit_card_outstanding?: string; credit_card_unbilled?: string
    credit_card_billed_count?: number; credit_card_estimated_count?: number
    liability_due_30d: string; total_due: string
    coverage_value: string; coverage_ratio: string | null
    coverage_level: 'ok' | 'warn' | 'bad' | 'none'; note: string
  }
  charts: {
    net_worth_trend: SeriesPoint[]
    asset_structure: AssetStructurePoint[]
    income_expense: SeriesPoint[]
    liability_trend?: SeriesPoint[]
    liability_structure?: { liability_type: string; liability_type_label: string; outstanding: string; currency: string }[]
    investment_structure?: { symbol: string; symbol_name: string | null; value: string; currency: string }[]
  }
  accounts: { count: number; by_currency: CurrencyTotal[] }
  assets?: {
    count: number; total_value: string; current_value: string; non_current_value: string
    pending_valuation_count: number; by_currency: { currency: string; total: string; count: number }[]
  }
  summaries?: {
    liabilities: Record<string, any>
    investments: Record<string, any>
    leases: Record<string, any>
  }
  pending: Record<string, number>
  timeline: TimelineItem[]
  data_quality: { note: string }
  data_updated_at: string | null
  is_empty: boolean
  is_partial: boolean
}

const router = useRouter()
const ui = useUiStore()
const theme = useThemeStore()

const loading = ref(true)
const error = ref('')
const data = ref<Snapshot | null>(null)
const period = ref('current_month')

const periods = [
  { value: 'current_month', label: '本月' },
  { value: 'last_month', label: '上月' },
  { value: 'last_3_months', label: '近三个月' },
  { value: 'last_6_months', label: '近六个月' },
  { value: 'current_year', label: '本年' },
  { value: 'all', label: '全部' },
]

const pendingCards = computed(() => [
  { label: 'AI 建议', value: data.value?.pending.ai_suggestions ?? 0, to: '/insights' },
  { label: '未对账账户', value: data.value?.pending.unreconciled_accounts ?? 0, to: '/ledger/reconciliation' },
  { label: '临近账单', value: data.value?.pending.upcoming_bills ?? 0, to: '/ledger/liabilities' },
  { label: '租金逾期', value: data.value?.pending.overdue_rents ?? 0, to: '/insights/leases' },
])

const pendingTotal = computed(
  () => pendingCards.value.reduce((sum, p) => sum + Number(p.value || 0), 0),
)

/** 现金流右侧金额列（与图中三行一一对应） */
const flowValues = computed(() => [
  Number(data.value?.metrics.income || 0),
  Number(data.value?.metrics.expense || 0),
  Number(data.value?.metrics.balance || 0),
])

/** 流动资金口径提示：构成 + 静态口径 + 偿债覆盖 + 动态跑道（含收益与通胀假设） */
const liquidityHint = computed(() => {
  const l = data.value?.liquidity
  if (!l) return ''
  const d = data.value?.debt_service
  const parts = [
    `现金 ${formatAmount(l.cash_accounts ?? l.account_cash, ui.amountPrecision)}`
      + ` · 投资现金 ${formatAmount(l.invest_cash, ui.amountPrecision)}`,
    l.note,
  ]
  if (d) {
    const bits = [
      `本期应还 ${formatAmount(d.credit_card_due, ui.amountPrecision)}`,
      `负债月供 ${formatAmount(d.liability_due_30d, ui.amountPrecision)}`,
    ]
    if (d.credit_card_outstanding) {
      bits.push(`信用卡欠款总额 ${formatAmount(d.credit_card_outstanding, ui.amountPrecision)}`)
    }
    if (d.credit_card_unbilled && Number(d.credit_card_unbilled) > 0) {
      bits.push(`其中未出账 ${formatAmount(d.credit_card_unbilled, ui.amountPrecision)}`)
    }
    if (d.credit_card_estimated_count) {
      bits.push(`${d.credit_card_estimated_count} 张卡没有账单，按欠款估算`)
    }
    parts.push(bits.join(' · '))
    parts.push(d.note)
  }
  parts.push(l.runway_note)
  if (l.runway_months_dynamic != null && l.runway_assumptions) {
    parts.push(
      `按 ${l.runway_assumptions.invest_return} 年化收益 + ${l.runway_assumptions.inflation} 通胀逐月模拟：约 ${l.runway_months_dynamic} 个月`,
    )
  }
  return parts.filter(Boolean).join(' ｜ ')
})

/** 本期结余率：结余 ÷ 收入，仅用于展示，不参与任何金额计算 */
const savingsRate = computed(() => {
  const income = Number(data.value?.metrics.income || 0)
  const balance = Number(data.value?.metrics.balance || 0)
  if (!income) return '—'
  return `${((balance / income) * 100).toFixed(1)}%`
})

const textColor = computed(() => (theme.resolved === 'dark' ? '#C3CCDE' : '#445069'))
const inkStrong = computed(() => (theme.resolved === 'dark' ? '#EEF3FF' : '#172033'))
const splitColor = computed(() => (theme.resolved === 'dark' ? 'rgba(255,255,255,.10)' : 'rgba(120,140,170,.18)'))
const axisColor = computed(() => (theme.resolved === 'dark' ? 'rgba(255,255,255,.16)' : '#E7EBF2'))

/** 数据面板统一配色：蓝紫青为主，和顶部统计卡的图标色调一致 */
const CHART_COLORS = ['#356DFF', '#7C5CFC', '#26D6D6', '#F4A62A', '#19B77A', '#E85D75', '#5B8BFF', '#B07CFF']

/** 深色玻璃 tooltip：深浅主题各自适配，浮层也像大屏面板 */
const tooltipStyle = computed(() => ({
  backgroundColor: theme.resolved === 'dark' ? 'rgba(12,20,36,.94)' : 'rgba(255,255,255,.97)',
  borderColor: theme.resolved === 'dark' ? 'rgba(255,255,255,.14)' : 'rgba(23,32,51,.08)',
  borderWidth: 1,
  padding: [9, 12],
  textStyle: { color: theme.resolved === 'dark' ? '#EEF3FF' : '#172033', fontSize: 12 },
  extraCssText: 'border-radius:12px;box-shadow:0 18px 44px rgba(12,27,58,.2);backdrop-filter:blur(14px);',
}))

/** 轴标签用「万 / 亿」紧凑写法：大屏上不出现一长串数字 */
function compactAmount(v: number) {
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(abs >= 1e9 ? 0 : 1)}亿`
  if (abs >= 1e4) return `${Math.round(v / 1e4)}万`
  return String(Math.round(v))
}

/** 资产结构：环形图 + 右侧清单共用一份数据（占比仅用于可视化） */
const assetLegend = computed(() => {
  const items = data.value?.charts.asset_structure || []
  const total = items.reduce((sum, i) => sum + Number(i.value || 0), 0)
  return items.map((i, idx) => ({
    label: i.asset_type_label,
    value: i.value,
    percent: total ? (Number(i.value) / total) * 100 : 0,
    color: CHART_COLORS[idx % CHART_COLORS.length],
  }))
})
const assetTotal = computed(() => assetLegend.value.reduce((sum, i) => sum + Number(i.value || 0), 0))

/** 投资合计（持仓市值 + 账户现金）：优先用后端 total_value，缺省回退到构成求和 */
const investmentTotal = computed(() => {
  const total = Number(data.value?.summaries?.investments?.total_value ?? NaN)
  if (Number.isFinite(total)) return total
  return (data.value?.charts.investment_structure || []).reduce((sum, i) => sum + Number(i.value || 0), 0)
})

/** 租赁收租率（后端给整数百分比），用于环心与右侧标签 */
const leaseRate = computed(() => {
  const raw = Number(data.value?.summaries?.leases?.collection_rate ?? 0)
  return Number.isFinite(raw) ? Math.round(raw) : 0
})

/** 负债 / 投资 / 租赁全为空时，整个「负债与运营」面板收成一块空态 */
const opsTotal = computed(() => {
  const s = data.value?.summaries
  return Number(s?.liabilities?.count || 0)
    + Number(s?.investments?.count || 0)
    + Number(s?.leases?.count || 0)
})

/** 账户余额趋势（面积发光折线，末端标注当期值） */
const trendOption = computed(() => {
  const series = data.value?.charts.net_worth_trend || []
  const values = series.map((p) => Number(p.value || 0))
  return {
    animationDuration: 820,
    animationEasing: 'cubicOut',
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle.value,
      axisPointer: {
        type: 'line',
        lineStyle: { color: 'rgba(53,109,255,.45)', type: 'dashed', width: 1 },
      },
      valueFormatter: (v: number) => `${Number(v).toFixed(ui.amountPrecision)} ${data.value?.base_currency || ''}`,
    },
    grid: { left: 2, right: 46, top: 22, bottom: 2, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: series.map((p) => p.label),
      axisLine: { lineStyle: { color: axisColor.value } },
      axisTick: { show: false },
      axisLabel: { color: textColor.value, fontSize: 11, margin: 12 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: splitColor.value, type: 'dashed' } },
      axisLabel: { color: textColor.value, fontSize: 11, formatter: compactAmount },
    },
    series: [{
      type: 'line',
      smooth: 0.4,
      showSymbol: false,
      symbol: 'circle',
      symbolSize: 8,
      data: values,
      lineStyle: {
        width: 3,
        color: {
          type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
          colorStops: [{ offset: 0, color: '#356DFF' }, { offset: 1, color: '#7C5CFC' }],
        },
        shadowColor: 'rgba(53,109,255,.45)',
        shadowBlur: 18,
        shadowOffsetY: 8,
      },
      itemStyle: { color: '#356DFF', borderColor: '#fff', borderWidth: 2 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(53,109,255,.34)' },
            { offset: 0.6, color: 'rgba(53,109,255,.10)' },
            { offset: 1, color: 'rgba(53,109,255,0)' },
          ],
        },
      },
      emphasis: { scale: 1.6 },
      endLabel: {
        show: series.length > 0,
        color: inkStrong.value,
        fontSize: 11,
        fontWeight: 600,
        distance: 8,
        formatter: (p: { value: number }) => compactAmount(Number(p.value)),
      },
    }],
  }
})

/**
 * 本期现金流：收入 / 支出 / 结余横向条形。
 * 横向对「数值差异大」更友好（支出只占收入 7% 也看得见），金额统一右对齐。
 */
const flowOption = computed(() => {
  const currency = data.value?.base_currency || ''
  const items = [
    { name: '收入', value: Number(data.value?.metrics.income || 0), from: '#2ED3A0', to: '#19B77A' },
    { name: '支出', value: Number(data.value?.metrics.expense || 0), from: '#F7B45A', to: '#E2962A' },
    { name: '结余', value: Number(data.value?.metrics.balance || 0), from: '#6F97FF', to: '#356DFF' },
  ]
  const max = Math.max(...items.map((i) => i.value), 0)
  return {
    animationDuration: 760,
    animationEasing: 'cubicOut',
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle.value,
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(53,109,255,.06)' } },
      valueFormatter: (v: number) => `${formatAmount(v, ui.amountPrecision)} ${currency}`,
    },
    grid: { left: 44, right: 96, top: 10, bottom: 6 },
    xAxis: { type: 'value', show: false, max: max || 1 },
    yAxis: {
      type: 'category',
      inverse: true,
      data: items.map((i) => i.name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: textColor.value, fontSize: 12, margin: 12 },
    },
    series: [{
      type: 'bar',
      barWidth: 18,
      showBackground: true,
      backgroundStyle: { color: 'rgba(120,140,170,.10)', borderRadius: 6 },
      data: items.map((i) => ({
        value: i.value,
        itemStyle: {
          borderRadius: 6,
          color: {
            type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
            colorStops: [{ offset: 0, color: i.from }, { offset: 1, color: i.to }],
          },
        },
        label: { position: i.value >= 0 ? 'right' : 'left' },
      })),
    }],
  }
})

/** 收支对比：收入/支出双柱，圆角 + 纵向渐变 */
const incomeExpenseOption = computed(() => {
  const series = data.value?.charts.income_expense || []
  return {
    animationDuration: 700,
    animationEasing: 'cubicOut',
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle.value,
      axisPointer: {
        type: 'shadow',
        shadowStyle: { color: theme.resolved === 'dark' ? 'rgba(255,255,255,.05)' : 'rgba(53,109,255,.06)' },
      },
      valueFormatter: (v: number) => `${Number(v).toFixed(ui.amountPrecision)} ${data.value?.base_currency || ''}`,
    },
    legend: {
      top: 0, right: 0, icon: 'roundRect', itemWidth: 9, itemHeight: 9, itemGap: 16,
      textStyle: { color: textColor.value, fontSize: 11.5 },
    },
    grid: { left: 2, right: 10, top: 36, bottom: 2, containLabel: true },
    xAxis: {
      type: 'category',
      data: series.map((p) => p.label),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: textColor.value, fontSize: 11, margin: 12 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: splitColor.value, type: 'dashed' } },
      axisLabel: { color: textColor.value, fontSize: 11, formatter: compactAmount },
    },
    series: [
      {
        name: '收入',
        type: 'bar',
        barMaxWidth: 18,
        barGap: '22%',
        data: series.map((p) => Number(p.income || 0)),
        itemStyle: {
          borderRadius: [6, 6, 3, 3],
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: '#2ED3A0' }, { offset: 1, color: 'rgba(46,211,160,.28)' }],
          },
        },
      },
      {
        name: '支出',
        type: 'bar',
        barMaxWidth: 18,
        data: series.map((p) => Number(p.expense || 0)),
        itemStyle: {
          borderRadius: [6, 6, 3, 3],
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: '#F7B45A' }, { offset: 1, color: 'rgba(247,180,90,.26)' }],
          },
        },
      },
    ],
  }
})

/** 资产结构环形图（07 §2）：环内显示总额，明细交给右侧清单，避免环形被文字挤满 */
const assetStructureOption = computed(() => {
  const items = assetLegend.value
  return {
    animationDuration: 700,
    tooltip: {
      trigger: 'item',
      ...tooltipStyle.value,
      valueFormatter: (v: number) => `${Number(v).toFixed(ui.amountPrecision)} ${data.value?.base_currency || ''}`,
    },
    series: [{
      type: 'pie',
      radius: ['60%', '84%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: false,
      label: { show: false },
      labelLine: { show: false },
      itemStyle: {
        borderRadius: 6,
        borderWidth: 3,
        borderColor: theme.resolved === 'dark' ? '#0A1222' : '#F4F7FC',
      },
      data: items.map((i) => ({ name: i.label, value: Number(i.value), itemStyle: { color: i.color } })),
    }],
  }
})

/** 负债余额趋势（07 §5）：柱状 + 圆角渐变，作为「负债与投资」面板的左半边 */
const liabilityTrendOption = computed(() => {
  const series = data.value?.charts.liability_trend || []
  return {
    animationDuration: 700,
    animationEasing: 'cubicOut',
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle.value,
      axisPointer: {
        type: 'shadow',
        shadowStyle: { color: theme.resolved === 'dark' ? 'rgba(255,255,255,.05)' : 'rgba(232,93,117,.06)' },
      },
      valueFormatter: (v: number) => `${Number(v).toFixed(ui.amountPrecision)} ${data.value?.base_currency || ''}`,
    },
    grid: { left: 0, right: 4, top: 12, bottom: 0, containLabel: true },
    xAxis: {
      type: 'category',
      data: series.map((p) => p.label),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: textColor.value, fontSize: 10.5, margin: 8 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: splitColor.value, type: 'dashed' } },
      axisLabel: { color: textColor.value, fontSize: 10.5, formatter: compactAmount },
    },
    series: [{
      type: 'bar',
      barMaxWidth: 16,
      data: series.map((p) => Number(p.value || 0)),
      itemStyle: {
        borderRadius: [5, 5, 2, 2],
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: '#F58CA0' }, { offset: 1, color: 'rgba(245,140,160,.24)' }],
        },
      },
    }],
  }
})

/** 投资持仓占比（07 §6）：环内显示市值 */
const investmentStructureOption = computed(() => {
  const items = data.value?.charts.investment_structure || []
  const total = items.reduce((sum, i) => sum + Number(i.value || 0), 0)
  return {
    animationDuration: 700,
    tooltip: {
      trigger: 'item',
      ...tooltipStyle.value,
      valueFormatter: (v: number) => `${Number(v).toFixed(ui.amountPrecision)} ${data.value?.base_currency || ''}`,
    },
    series: [{
      type: 'pie',
      radius: ['60%', '84%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: false,
      label: { show: false },
      labelLine: { show: false },
      itemStyle: {
        borderRadius: 6,
        borderWidth: 3,
        borderColor: theme.resolved === 'dark' ? '#0A1222' : '#F4F7FC',
      },
      data: items.map((i) => ({ name: i.symbol_name || i.symbol, value: Number(i.value) })),
    }],
  }
})

/** 租赁收租率进度环：绿色渐变表示已收，剩下一圈留灰底 */
const leaseRateOption = computed(() => ({
  animationDuration: 800,
  animationEasing: 'cubicOut',
  tooltip: { show: false },
  series: [{
    type: 'pie',
    radius: ['66%', '86%'],
    center: ['50%', '50%'],
    startAngle: 90,
    label: { show: false },
    labelLine: { show: false },
    itemStyle: {
      borderRadius: 8,
      borderWidth: 3,
      borderColor: theme.resolved === 'dark' ? '#0A1222' : '#F4F7FC',
    },
    data: [
      {
        name: '已收',
        value: leaseRate.value,
        itemStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 1, y2: 1,
            colorStops: [{ offset: 0, color: '#5ee0b4' }, { offset: 1, color: '#17a86f' }],
          },
        },
      },
      {
        name: '未收',
        value: Math.max(0, 100 - leaseRate.value),
        itemStyle: { color: 'rgba(120,140,170,.16)' },
      },
    ],
  }],
}))

let loadSeq = 0
async function load() {
  // 快速切换期间时，只让最后一次请求写 data，避免旧期间的数字盖住新选择
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const snapshot = await api.get<Snapshot>('/dashboard/snapshot', { period: period.value })
    if (seq !== loadSeq) return
    data.value = snapshot
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function fmtDate(v: string | null) {
  return v ? new Date(v).toLocaleDateString('zh-CN') : '—'
}

/** 图表点击下钻（07 §8、05 §2）：点击图表点进入对应明细页面 */
function drillFromChart(chartKey: string, payload: { seriesName?: string }) {
  const bySeries: Record<string, string> = {
    净资产: '/insights/assets',
    收入: '/insights/income',
    支出: '/insights/expenses',
    剩余本金: '/insights/liabilities',
  }
  if (payload?.seriesName && bySeries[payload.seriesName]) {
    router.push(bySeries[payload.seriesName])
    return
  }
  const routes: Record<string, string> = {
    net_worth_trend: '/insights/assets',
    income_expense: '/insights/expenses',
    currency: '/ledger/accounts',
    asset_structure: '/insights/assets',
    liability_trend: '/insights/liabilities',
    investment_structure: '/insights/investments',
  }
  const to = routes[chartKey]
  if (to) router.push(to)
}

onMounted(load)
</script>

<template>
  <div class="dashboard">
    <header class="head">
      <div>
        <h1>脉枢台</h1>
        <p class="sub">
          一个页面回答：家庭现在有多少净资产、钱从哪里来、花到哪里去、负债压力如何、哪些事项需要处理。
        </p>
      </div>
      <div class="actions">
        <AppSelect v-model="period" :options="periods" class="period" @update:model-value="load" />
        <span class="ccy">币种：{{ data?.currency_badge || 'CNY' }}</span>
        <AppButton variant="secondary" :loading="loading" @click="load">
          <RefreshCw :size="15" /> 刷新
        </AppButton>
        <AppButton variant="primary" @click="ui.openAiAnalysis('dashboard', '脉枢台 · AI 摘要', period)">
          <Sparkles :size="15" /> AI 摘要
        </AppButton>
      </div>
    </header>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard v-if="data.is_empty" level="3" aurora>
        <StateBlock
          state="empty"
          title="还没有任何财务记录"
          description="先配置一个账户，或录入第一笔收入支出，脉枢台就会开始汇总。"
        >
          <template #actions>
            <AppButton variant="primary" @click="router.push('/ledger/accounts')">
              <Wallet :size="15" /> 配置账户
            </AppButton>
            <AppButton variant="secondary" @click="router.push('/ledger/transactions?new=1')">
              <Upload :size="15" /> 新增第一笔记录
            </AppButton>
          </template>
        </StateBlock>
      </AppCard>

      <p v-else-if="data.is_partial" class="partial">
        统计不完整：存在未确认的草稿，草稿不计入任何统计。
      </p>

      <!-- 顶部统计：与资产页同一套等宽统计卡，第一张是家庭净资产 -->
      <section class="hero-row">
        <StatCard
          :icon="Landmark" tone="primary" label="家庭净资产"
          :value="data.net_worth.net_worth" :currency="data.base_currency"
          class="hero-lead"
        >
          <template #label-extra>
            <AppHint :text="data.net_worth.note" label="净资产口径" />
          </template>
          <template #footer>
            <p class="hero-delta">
              <span class="dk">较上一期间</span>
              <MoneyText
                :value="data.net_worth.change_since_previous" :currency="data.base_currency"
                show-currency signed
              />
            </p>
            <p v-if="data.net_worth.debt_ratio" class="debt-ratio" :class="data.net_worth.debt_ratio_level">
              负债率 {{ data.net_worth.debt_ratio }}%
              <AppHint :text="data.net_worth.debt_ratio_note || ''" label="负债率口径" />
            </p>
          </template>
        </StatCard>
        <StatCard
          :icon="Wallet" tone="primary" label="账户余额"
          :value="data.net_worth.accounts || '0'" :currency="data.base_currency"
          :hint="`${data.net_worth.included_accounts} 个账户计入`"
          clickable @click="router.push('/ledger/accounts')"
        />
        <StatCard
          :icon="Coins" tone="success" label="流动资金"
          :value="data.liquidity?.value || '0'" :currency="data.base_currency"
          clickable @click="router.push('/ledger/accounts')"
        >
          <template #label-extra>
            <AppHint :text="liquidityHint" label="流动资金与跑道口径" />
          </template>
          <template #footer>
            <p class="liquidity-runway" :class="data.liquidity?.runway_level">
              <template v-if="data.liquidity?.runway_months">
                可支撑 {{ data.liquidity.runway_months }} 个月支出
              </template>
              <template v-else>跑道待测算</template>
            </p>
            <!-- 流动资金不扣待还；够不够还看这两行（卡片窄，拆成两行避免折行） -->
            <p class="liquidity-debt" :class="data.debt_service?.coverage_level">
              <template v-if="data.debt_service?.coverage_level && data.debt_service.coverage_level !== 'none'">
                本期应还 {{ formatAmount(data.debt_service.total_due, ui.amountPrecision) }}<br />
                覆盖 {{ data.debt_service.coverage_ratio }} 倍
              </template>
              <template v-else>无待还</template>
            </p>
          </template>
        </StatCard>
        <StatCard
          :icon="Building2" tone="ai" label="资产价值"
          :value="data.net_worth.assets || '0'" :currency="data.base_currency"
          :hint="`${data.assets?.count ?? 0} 项 · ${data.net_worth.included_assets ?? 0} 项计入`"
          clickable @click="router.push('/ledger/assets')"
        />
        <StatCard
          :icon="TrendingUp" tone="success" label="投资总额"
          :value="data.net_worth.investments || '0'" :currency="data.base_currency"
          :hint="`${data.net_worth.included_investments ?? 0} 个账户`"
          clickable @click="router.push('/ledger/investments')"
        />
        <StatCard
          :icon="CreditCard" tone="danger" label="负债剩余" class="hero-liability"
          :value="data.net_worth.total_liabilities" :currency="data.base_currency"
          :hint="`${data.summaries?.liabilities?.count ?? 0} 笔 · ${data.net_worth.included_liabilities ?? 0} 笔计入`"
          clickable @click="router.push('/ledger/liabilities')"
        />
      </section>

      <div v-if="data.net_worth.by_currency.length > 1" class="hero-meta">
        <div class="by-ccy">
          <span v-for="c in data.net_worth.by_currency" :key="c.currency" class="ccy-pill">
            {{ c.currency }} <b>{{ c.total }}</b>
          </span>
        </div>
      </div>
      <p v-if="data.multi_currency" class="multi-note">存在多币种：按币种分组展示，未做跨币种相加。</p>

      <!-- 本期现金流：收入 / 支出 / 结余合成一张图，待处理事项放右侧 -->
      <section class="flow-row">
        <section class="flow-card">
          <header class="flow-head">
            <div>
              <h3>本期现金流</h3>
              <p>{{ data.period_label }} · 收入 − 支出 = 结余</p>
            </div>
            <span class="flow-rate">结余率 {{ savingsRate }}</span>
          </header>
          <div class="flow-plot">
            <EChart :option="flowOption" :height="168"
                    @click="router.push('/ledger/transactions')" />
            <div class="flow-values">
              <span v-for="(v, i) in flowValues" :key="i">
                <MoneyText :value="v" :currency="data.base_currency" show-currency />
              </span>
            </div>
          </div>
        </section>

        <StatCard
          :icon="ClipboardList" tone="warning" label="待处理事项"
          :value="pendingTotal" plain unit="项"
          :hint="pendingTotal ? '点下方标签直达对应页面' : '当前没有需要处理的事项'"
        >
          <template #footer>
            <div class="chips">
              <button
                v-for="p in pendingCards" :key="p.label" class="chip" type="button"
                :class="{ zero: !p.value }" @click="router.push(p.to)"
              >
                {{ p.label }} <b>{{ p.value }}</b>
              </button>
            </div>
          </template>
        </StatCard>
      </section>

      <!-- 数据面板：6 张散图整合成「财务全景」+「负债与运营」两块 -->
      <section class="panel">
        <header class="panel-head">
          <div class="ph-left">
            <span class="ph-bar" />
            <div>
              <h3>财务全景</h3>
              <p>净值走势 · 资产构成 · 收支对比</p>
            </div>
          </div>
          <div class="ph-right">
            <span class="ph-tag">{{ data.period_label }}</span>
            <span class="ph-tag is-ghost">{{ data.base_currency }}</span>
          </div>
        </header>

        <div class="triple">
          <div class="blk">
            <div class="blk-head">
              <span>净资产走势 · 近 6 个月</span>
              <b><MoneyText :value="data.net_worth.net_worth" :currency="data.base_currency" show-currency /></b>
            </div>
            <EChart v-if="data.charts.net_worth_trend.length" :option="trendOption" :height="262"
                    @click="drillFromChart('net_worth_trend', $event)" />
            <StateBlock v-else state="empty" title="暂无趋势数据"
                        description="创建账户并确认流水后，趋势图会按月绘制。" />
          </div>

          <div class="blk">
            <div class="blk-head">
              <span>资产构成</span>
              <b>{{ data.net_worth.included_assets ?? 0 }} 项资产</b>
            </div>
            <template v-if="assetLegend.length">
              <div class="ring-wrap">
                <EChart :option="assetStructureOption" :height="168"
                        @click="drillFromChart('asset_structure', $event)" />
                <div class="ring-center">
                  <b>¥{{ compactAmount(assetTotal) }}</b>
                  <span>资产总额</span>
                </div>
              </div>
              <ul class="struct-list">
                <li v-for="a in assetLegend" :key="a.label">
                  <span class="dot" :style="{ background: a.color }" />
                  <span class="nm">{{ a.label }}</span>
                  <span class="track"><i :style="{ width: `${a.percent}%`, background: a.color }" /></span>
                  <span class="pct">{{ a.percent.toFixed(1) }}%</span>
                </li>
              </ul>
            </template>
            <div v-else class="blk-empty">登记资产后，这里按类型展示构成与占比。</div>
          </div>

          <div class="blk">
            <div class="blk-head">
              <span>收支对比</span>
              <b>结余 <MoneyText :value="data.metrics.balance" :currency="data.base_currency" signed show-currency /></b>
            </div>
            <EChart v-if="data.charts.income_expense.length" :option="incomeExpenseOption" :height="230"
                    @click="drillFromChart('income_expense', $event)" />
            <StateBlock v-else state="empty" title="暂无收支数据"
                        description="确认流水后按月对比收入与支出。" />
            <div class="blk-foot">
              <span>收入 <b><MoneyText :value="data.metrics.income" :currency="data.base_currency" show-currency /></b></span>
              <span>支出 <b><MoneyText :value="data.metrics.expense" :currency="data.base_currency" show-currency /></b></span>
            </div>
          </div>
        </div>
      </section>

      <section v-if="opsTotal" class="panel ops">
        <header class="panel-head">
          <div class="ph-left">
            <span class="ph-bar is-success" />
            <div>
              <h3>负债与运营</h3>
              <p>剩余本金 · 投资持仓 · 租赁收租</p>
            </div>
          </div>
          <div class="ph-right">
            <span class="ph-tag is-ghost">{{ data.base_currency }}</span>
          </div>
        </header>

        <div class="triple">
          <div class="blk">
            <div class="blk-head">
              <span>负债剩余本金</span>
              <b><MoneyText :value="data.summaries?.liabilities?.total_outstanding || 0" :currency="data.base_currency" show-currency /></b>
            </div>
            <EChart v-if="(data.charts.liability_trend || []).length" :option="liabilityTrendOption"
                    :height="196" @click="drillFromChart('liability_trend', $event)" />
            <div v-else class="blk-empty">登记负债后，这里展示剩余本金走势。</div>
            <div class="blk-foot">
              <span>月供 <b><MoneyText :value="data.summaries?.liabilities?.monthly_due || 0" :currency="data.base_currency" show-currency /></b></span>
              <span>逾期 <b>{{ data.summaries?.liabilities?.overdue_count ?? 0 }} 笔</b></span>
              <span>计入净资产 <b><MoneyText :value="data.net_worth.total_liabilities" :currency="data.base_currency" show-currency /></b></span>
            </div>
          </div>

          <div class="blk">
            <div class="blk-head">
              <span>投资持仓</span>
              <b>{{ data.summaries?.investments?.count ?? 0 }} 个账户</b>
            </div>
            <div v-if="investmentTotal" class="ring-wrap">
              <EChart :option="investmentStructureOption" :height="168"
                      @click="drillFromChart('investment_structure', $event)" />
              <div class="ring-center">
                <b>¥{{ compactAmount(investmentTotal) }}</b>
                <span>投资总额</span>
              </div>
            </div>
            <div v-else class="blk-empty">登记投资后，这里展示持仓构成。</div>
            <div class="blk-foot">
              <span>成本 <b><MoneyText :value="data.summaries?.investments?.cost || 0" :currency="data.base_currency" show-currency /></b></span>
              <span>损益 <b><MoneyText :value="data.summaries?.investments?.total_pnl || 0" :currency="data.base_currency" signed show-currency /></b></span>
            </div>
          </div>

          <div class="blk">
            <div class="blk-head">
              <span>租赁收租率</span>
              <b>{{ data.summaries?.leases?.count ?? 0 }} 份租约</b>
            </div>
            <div v-if="data.summaries?.leases?.count" class="ring-wrap">
              <EChart :option="leaseRateOption" :height="168" @click="router.push('/ledger/leases')" />
              <div class="ring-center">
                <b>{{ leaseRate }}%</b>
                <span>本期收租率</span>
              </div>
            </div>
            <div v-else class="blk-empty">登记租约后，这里展示收租进度。</div>
            <div class="blk-foot">
              <span>逾期 <b>{{ data.summaries?.leases?.overdue_count ?? 0 }} 笔</b></span>
              <span>押金 <b><MoneyText :value="data.summaries?.leases?.deposit_held || 0" :currency="data.base_currency" show-currency /></b></span>
            </div>
          </div>
        </div>
      </section>

      <section v-else class="panel ops">
        <header class="panel-head">
          <div class="ph-left">
            <span class="ph-bar is-success" />
            <div>
              <h3>负债与运营</h3>
              <p>剩余本金 · 投资持仓 · 租赁收租</p>
            </div>
          </div>
        </header>
        <StateBlock state="empty" title="暂无负债、投资与租赁"
                    description="登记负债、投资账户或租约后，这一块会显示剩余本金、持仓构成与收租进度。">
          <template #actions>
            <AppButton variant="primary" @click="router.push('/ledger/liabilities')">登记负债</AppButton>
            <AppButton variant="secondary" @click="router.push('/ledger/investments')">登记投资</AppButton>
          </template>
        </StateBlock>
      </section>

      <section class="panel">
        <header class="panel-head">
          <div class="ph-left">
            <span class="ph-bar is-neutral" />
            <div>
              <h3>最近变化</h3>
              <p>最新确认的业务事件</p>
            </div>
          </div>
          <div class="ph-right">
            <button class="ph-link" type="button" @click="router.push('/ledger/transactions')">
              全部流水 <ArrowRight :size="13" />
            </button>
          </div>
        </header>
        <StateBlock v-if="!data.timeline.length" state="empty" title="暂无变化记录"
                    description="确认业务事件后，这里会显示最近的变化。" />
        <div v-else class="recent-row">
          <div v-for="t in data.timeline.slice(0, 4)" :key="t.id" class="recent-item"
               @click="router.push('/ledger/transactions')">
            <div class="ri-top">
              <strong>{{ t.title }}</strong>
              <MoneyText class="ri-amt" :value="t.amount" :currency="t.currency" show-currency />
            </div>
            <small>{{ fmtDate(t.occurred_at) }} · {{ t.status === 'confirmed' ? '已确认' : t.status === 'draft' ? '草稿' : '已冲销' }}</small>
          </div>
        </div>
      </section>

      <AppCard title="统计口径" level="1">
        <ul class="caliber">
          <li v-for="(v, k) in data.caliber" :key="k">
            <span class="ck">
              {{ {
                net_worth: '净资产', account_balance: '账户余额', income: '收入', expense: '支出',
                transfer: '内部转账', multi_currency: '多币种', excludes_draft: '草稿处理',
              }[k as string] || k }}
            </span>
            <span class="cv">{{ v === true ? '草稿不计入统计' : v }}</span>
          </li>
        </ul>
        <p class="multi-note">{{ data.data_quality.note }}</p>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: var(--space-5); }
.head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--space-4); flex-wrap: wrap; }
.sub { font-size: 13px; color: var(--ink-muted); margin-top: 4px; max-width: 720px; }
.actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.period { width: 150px; }
.ccy { font-size: 12.5px; color: var(--ink-muted); }
.partial { font-size: 13px; color: var(--warning); }

/* 顶部统计：与资产页一致的一排统计卡（净资产是主角，但仍处在同一栅格内） */
.hero-row {
  display: grid; grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: var(--space-3); align-items: stretch;
}
.hero-row :deep(.stat-card .sc-v) { font-size: clamp(16px, 1.4vw, 20px); letter-spacing: -0.01em; }
.hero-row .hero-lead {
  border-color: rgba(53, 109, 255, 0.26);
  background:
    linear-gradient(150deg, rgba(53, 109, 255, 0.10), rgba(124, 92, 252, 0.05) 58%, transparent 100%),
    var(--glass-1);
}
.hero-row :deep(.hero-lead .sc-ico) { width: 32px; height: 32px; border-radius: 11px; }
.hero-row :deep(.hero-lead .sc-v) { font-size: clamp(19px, 1.6vw, 23px); }
.hero-delta {
  display: inline-flex; align-items: center; gap: 6px;
  margin: 2px 0 0; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--glass-2);
  font-size: 11.5px; color: var(--ink-muted); align-self: flex-start;
}
.liquidity-break {
  margin: 2px 0 0; font-size: 11px; color: var(--ink-muted);
  line-height: 1.5; font-variant-numeric: tabular-nums;
}
.liquidity-runway {
  margin: 0; font-size: 11.5px; font-weight: 550; color: var(--ink-strong);
  line-height: 1.5; font-variant-numeric: tabular-nums;
}
.liquidity-runway.ok { color: var(--success); }
.liquidity-runway.warn { color: var(--warning); }
.liquidity-runway.bad { color: var(--danger); }
.liquidity-runway.unknown { color: var(--ink-muted); font-weight: 400; }
/* 偿债覆盖：流动资金不扣待还，这一行回答「够不够还」 */
.liquidity-debt {
  margin: 2px 0 0; font-size: 11px; font-weight: 550;
  line-height: 1.5; color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}
.liquidity-debt.ok { color: var(--success); }
.liquidity-debt.warn { color: var(--warning); }
.liquidity-debt.bad { color: var(--danger); }
.liquidity-debt.none { color: var(--ink-muted); font-weight: 400; }
.debt-ratio {
  display: inline-flex; align-items: center; gap: 4px;
  margin: 4px 0 0; font-size: 11px; color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}
.debt-ratio.ok { color: var(--success); }
.debt-ratio.warn { color: var(--warning); }
.debt-ratio.bad { color: var(--danger); }
.hero-delta .dk { color: var(--ink-muted); }
.hero-meta {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--space-3); flex-wrap: wrap; margin-top: -6px;
}
.by-ccy { display: flex; flex-wrap: wrap; gap: 8px; }
.ccy-pill {
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
  border-radius: 999px; background: var(--glass-2); border: 1px solid var(--border);
  font-size: 12px; color: var(--ink-muted);
}
.ccy-pill b { color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.multi-note { margin-top: var(--space-2); font-size: 12px; color: var(--ink-muted); }

.flow-row { display: grid; grid-template-columns: minmax(0, 2.1fr) minmax(0, 1fr); gap: var(--space-3); align-items: stretch; }
.flow-card {
  display: flex; flex-direction: column;
  padding: 13px 15px 6px;
  border-radius: var(--glass-radius-card);
  border: var(--glass-border-line);
  background: var(--glass-1);
}
.flow-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.flow-head h3 { margin: 0; font-size: 13px; font-weight: 650; color: var(--ink-strong); }
.flow-head p { margin: 3px 0 0; font-size: 11.5px; color: var(--ink-muted); }
.flow-row :deep(.stat-card) { height: 100%; }
.flow-row :deep(.stat-card > div:last-child) { margin-top: auto; }
.flow-plot { position: relative; }
.flow-values {
  position: absolute; top: 10px; bottom: 6px; right: 6px; width: 90px;
  display: grid; grid-template-rows: repeat(3, 1fr); align-items: center; justify-items: end;
  pointer-events: none;
}
.flow-values :deep(.money) { font-size: 12.5px; font-weight: 600; }
.flow-rate {
  flex: none; padding: 3px 10px; border-radius: 999px;
  font-size: 11px; color: var(--primary);
  background: rgba(53, 109, 255, 0.10); border: 1px solid rgba(53, 109, 255, 0.22);
  font-variant-numeric: tabular-nums;
}
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 999px; font-size: 11.5px;
  border: 1px solid var(--border); background: var(--glass-1);
  color: var(--ink-strong); cursor: pointer;
}
.chip b { font-variant-numeric: tabular-nums; }
.chip:hover { border-color: var(--primary); color: var(--primary); }
.chip.zero { color: var(--ink-muted); opacity: 0.7; }

/* ===== 数据面板：2 块大面板，内部三栏 ===== */
.panel {
  position: relative; overflow: hidden;
  padding: 16px 18px 18px;
  border-radius: var(--glass-radius-card);
  border: var(--glass-border-line);
  background: var(--glass-1);
  box-shadow: var(--glass-shadow);
  backdrop-filter: blur(var(--glass-blur));
}
.panel::before {
  content: ''; position: absolute; pointer-events: none;
  width: 540px; height: 540px; left: -180px; top: -320px;
  background: radial-gradient(closest-side, rgba(53, 109, 255, 0.28), transparent 72%);
}
.panel.ops::before {
  left: auto; right: -140px; top: -300px;
  background: radial-gradient(closest-side, rgba(124, 92, 252, 0.24), transparent 72%);
}
.panel::after {
  content: ''; position: absolute; inset: 0 0 auto 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(120, 160, 255, 0.75), rgba(124, 92, 252, 0.35), transparent);
}

.panel-head {
  position: relative; display: flex; align-items: flex-start; justify-content: space-between;
  gap: var(--space-3); margin-bottom: 12px;
}
.ph-left { display: flex; align-items: center; gap: 10px; }
.ph-bar {
  flex: none; width: 3px; height: 28px; border-radius: 2px;
  background: linear-gradient(180deg, #6f97ff, #356dff);
  box-shadow: 0 0 12px rgba(53, 109, 255, 0.6);
}
.ph-bar.is-success { background: linear-gradient(180deg, #5ee0b4, #17a86f); box-shadow: 0 0 12px rgba(23, 168, 111, 0.5); }
.ph-bar.is-neutral { background: linear-gradient(180deg, #9fb0ca, #64748b); box-shadow: none; }
.ph-left h3 { margin: 0; font-size: 15px; font-weight: 650; color: var(--ink-strong); letter-spacing: -0.01em; }
.ph-left p { margin: 3px 0 0; font-size: 11.5px; color: var(--ink-muted); }
.ph-right { display: flex; align-items: center; gap: 8px; }
.ph-tag {
  padding: 3px 9px; border-radius: 999px; font-size: 11px; color: var(--ink);
  background: var(--glass-2); border: 1px solid var(--border);
}
.ph-tag.is-ghost { color: var(--ink-muted); background: transparent; }
.ph-link {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 0;
  border: 0; background: none; color: var(--primary); font-size: 12px; cursor: pointer;
}

.triple { position: relative; display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr) minmax(0, 1fr); gap: 18px; }
.panel.ops .triple { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.blk { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.blk + .blk { padding-left: 18px; border-left: 1px dashed var(--border); }
.blk-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.blk-head span { font-size: 11.5px; color: var(--ink-muted); }
.blk-head b { font-size: 13px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.blk-foot {
  display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: auto; padding-top: 9px;
  border-top: 1px dashed var(--border); font-size: 11px; color: var(--ink-muted);
}
.blk-foot b { color: var(--ink-strong); font-variant-numeric: tabular-nums; margin-left: 2px; }
.blk-foot b :deep(.money) { font-weight: 600; }
.blk-empty {
  flex: 1; display: grid; place-items: center; min-height: 130px; padding: 0 12px;
  text-align: center; font-size: 11.5px; line-height: 1.6; color: var(--ink-muted);
}

.ring-wrap { position: relative; }
.ring-center {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 2px; pointer-events: none;
}
.ring-center b { font-size: 17px; font-weight: 700; color: var(--ink-strong); letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.ring-center span { font-size: 10.5px; color: var(--ink-muted); }

.struct-list { list-style: none; margin: 2px 0 0; padding: 0; display: flex; flex-direction: column; gap: 7px; }
.struct-list li { display: grid; grid-template-columns: 8px 52px minmax(0, 1fr) 40px; align-items: center; gap: 7px; font-size: 11.5px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.nm { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.track { height: 4px; border-radius: 2px; background: rgba(120, 140, 170, 0.16); overflow: hidden; }
.track i { display: block; height: 100%; border-radius: 2px; }
.pct { text-align: right; color: var(--ink-muted); font-variant-numeric: tabular-nums; }

.recent-row { position: relative; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.recent-item {
  padding: 9px 11px; border-radius: var(--glass-radius-control);
  border: 1px solid var(--border); background: var(--glass-1); cursor: pointer;
  transition: border-color var(--motion-fast) var(--ease-out);
}
.recent-item:hover { border-color: var(--primary); }
.ri-top { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; min-width: 0; }
.recent-item strong {
  min-width: 0; font-size: 12.5px; font-weight: 600; color: var(--ink-strong);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; line-height: 1.35;
}
.ri-amt { font-size: 12.5px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.recent-item small { display: block; margin-top: 3px; font-size: 10.5px; color: var(--ink-muted); }

.caliber { display: flex; flex-direction: column; gap: 6px; }
.caliber li { display: flex; gap: var(--space-3); font-size: 12.5px; }
.ck { flex: 0 0 88px; color: var(--ink-muted); }
.cv { color: var(--ink); }

@media (max-width: 1180px) {
  .triple, .panel.ops .triple { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .triple > .blk:first-child { grid-column: span 2; }
  .triple > .blk + .blk { padding-left: 0; border-left: 0; }
  .blk + .blk:nth-child(n + 3) { padding-top: 12px; border-top: 1px dashed var(--border); }
  .recent-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .flow-row { grid-template-columns: minmax(0, 1.8fr) minmax(0, 1fr); }
  .hero-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hero-lead { grid-column: span 2; }
  .hero-liability { grid-column: span 2; }
}
@media (max-width: 640px) {
  .flow-row { grid-template-columns: 1fr; }
  .flow-card { padding: 12px 13px 4px; }
  .hero-row { grid-template-columns: 1fr; gap: 9px; }
  .hero-lead { grid-column: span 1; }
  /* 必须一并重置：否则 1 列栅格里残留 span 2 会生成隐式第二列，
     导致其他卡片只剩半宽、负债剩余跨列独占一行（移动端错版） */
  .hero-liability { grid-column: span 1; }
  .hero-row :deep(.sc-v) { font-size: 21px; }
  .hero-lead :deep(.sc-v) { font-size: 24px; }
  .hero-delta { font-size: 11px; }
  .panel { padding: 13px 14px 14px; }
  .triple, .panel.ops .triple { grid-template-columns: 1fr; gap: 14px; }
  .triple > .blk:first-child { grid-column: span 1; }
  .blk + .blk:nth-child(n + 2) { padding-top: 12px; border-top: 1px dashed var(--border); }
  .blk + .blk:nth-child(n + 3) { padding-top: 12px; }
  .recent-row { grid-template-columns: 1fr; }
  .period { width: 132px; }
  .actions { width: 100%; }
}
</style>
