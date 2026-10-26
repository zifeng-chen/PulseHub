<script setup lang="ts">
/** 综合脉诊 /insights（07 §1）：统一筛选 + 结论列表（每条含口径/期间/更新时间/来源数量/查看明细/让AI继续分析）。 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Sparkles, ArrowRight, Bot } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Conclusion {
  key: string
  title: string
  type: string
  type_label: string
  caliber: string
  period: string
  period_label: string
  data_updated_at: string
  source_count: number
  detail_to: string
  summary: string
  highlight: string | null
}

interface Insight {
  period: string
  period_label: string
  period_range: string
  base_currency: string
  currency_badge: string
  members_scope: string
  include_investment_valuation: boolean
  module_enabled: boolean
  data_completeness: { complete: boolean; missing: string[]; note: string }
  ai_summary: string | null
  ai_summary_state: string
  ai_summary_hint: string
  conclusions: Conclusion[]
  data_updated_at: string
  conclusion_types: Record<string, string>
}

const router = useRouter()
const ui = useUiStore()
const loading = ref(true)
const error = ref('')
const data = ref<Insight | null>(null)
const period = ref('current_month')

const periods = [
  { value: 'current_month', label: '本月' },
  { value: 'last_month', label: '上月' },
  { value: 'last_6_months', label: '近六个月' },
  { value: 'current_year', label: '本年' },
  { value: 'all', label: '全部' },
]

function fmtUpdated(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  return `${d.toLocaleDateString('zh-CN')} ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Insight>('/insights/overview', { period: period.value })
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

/** AI 摘要单独异步加载：不阻塞结论列表，模型慢或不可用都不影响页面 */
interface AiSummary { state: string; summary: string | null; hint: string; model_type: string }
const aiSummary = ref<AiSummary | null>(null)
const aiLoading = ref(true)

async function loadAiSummary() {
  aiLoading.value = true
  try {
    aiSummary.value = await api.get<AiSummary>('/insights/ai-summary')
  } catch (err) {
    aiSummary.value = { state: 'unavailable', summary: null, hint: errorMessage(err), model_type: '' }
  } finally {
    aiLoading.value = false
  }
}

onMounted(async () => {
  await load()
  loadAiSummary()
})
</script>

<template>
  <div class="insights">
    <PageHeader title="综合脉诊" subtitle="每个结论都包含口径、期间、数据更新时间、来源数量与查看明细入口。">
      <AppSelect v-model="period" :options="periods" class="period" @update:model-value="load" />
      <AppButton variant="primary" :loading="aiLoading" @click="loadAiSummary">
        <Sparkles :size="15" /> 重新生成摘要
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="筛选与口径" level="1">
        <ul class="filters">
          <li><span class="k">分析期间</span><span class="v">{{ data.period_label }}（{{ data.period_range }}）</span></li>
          <li><span class="k">基础币种</span><span class="v">{{ data.currency_badge }}</span></li>
          <li><span class="k">成员范围</span><span class="v">{{ data.members_scope }}</span></li>
          <li>
            <span class="k">是否包含投资估值</span>
            <span class="v">{{ data.include_investment_valuation ? '包含' : '不包含' }}</span>
          </li>
          <li><span class="k">数据完整性</span><span class="v">{{ data.data_completeness.note }}</span></li>
        </ul>
      </AppCard>

      <AppCard title="AI 综合摘要" level="2" aurora>
        <StateBlock v-if="aiLoading" state="loading" />
        <StateBlock
          v-else-if="!aiSummary || aiSummary.state !== 'ready'"
          state="pending"
          title="AI 摘要暂不可用"
          :description="aiSummary?.hint || '模型未连接'"
        >
          <template #actions>
            <AppButton variant="secondary" @click="loadAiSummary">重试</AppButton>
            <AppButton variant="primary" @click="router.push('/settings/local-ai')">前往配置本地 AI</AppButton>
          </template>
        </StateBlock>
        <template v-else>
          <p class="summary">{{ aiSummary.summary }}</p>
          <p class="ai-source">由{{ aiSummary.model_type === 'third_party' ? '第三方 AI' : '本地 AI' }}生成 · 数据来自当期确定性统计</p>
        </template>
        <div class="types">
          <span v-for="(label, key) in data.conclusion_types" :key="key" class="type-chip">
            {{ label }}（{{ key }}）
          </span>
        </div>
      </AppCard>

      <section class="conclusions">
        <AppCard v-for="c in data.conclusions" :key="c.key" class="conclusion">
          <div class="c-head">
            <strong>{{ c.title }}</strong>
            <span class="type-tag" :class="c.type">{{ c.type_label }}</span>
          </div>
          <p v-if="c.highlight" class="c-highlight">{{ c.highlight }}</p>
          <p class="c-summary">{{ c.summary }}</p>
          <p class="c-caliber">口径：{{ c.caliber }}</p>
          <p class="c-meta">
            期间：{{ c.period_label }} · 数据更新时间：{{ fmtUpdated(c.data_updated_at) }} · 来源数量：{{ c.source_count }}
          </p>
          <div class="c-actions">
            <button class="act" type="button" @click="router.push(c.detail_to)">
              查看明细 <ArrowRight :size="14" />
            </button>
            <button class="act ai" type="button" @click="ui.openAiPanel(c.title)">
              <Bot :size="14" /> 让 AI 继续分析
            </button>
          </div>
        </AppCard>
      </section>
    </template>
  </div>
</template>

<style scoped>
.insights { display: flex; flex-direction: column; gap: var(--space-4); }
.period { width: 160px; }
.filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--space-3); }
@media (max-width: 640px) {
  .filters { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; }
  .filters li:last-child { grid-column: 1 / -1; }
  .filters .v { font-size: 12.5px; }
}
.filters li { display: flex; flex-direction: column; gap: 2px; }
.filters .k { font-size: 12px; color: var(--ink-muted); }
.filters .v { font-size: 13.5px; color: var(--ink-strong); }
.summary { font-size: 13.5px; color: var(--ink-strong); }
.ai-source { margin-top: var(--space-2); font-size: 11.5px; color: var(--ink-muted); }
.types { display: flex; flex-wrap: wrap; gap: 6px; margin-top: var(--space-3); }
.type-chip {
  font-size: 12px; padding: 3px 9px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--glass-1); color: var(--ink);
}
.conclusions { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: var(--space-4); }
.conclusion { display: flex; flex-direction: column; gap: 6px; }
.c-head { display: flex; align-items: center; justify-content: space-between; }
.c-head strong { font-size: 15px; color: var(--ink-strong); }
.type-tag { font-size: 11.5px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); background: var(--glass-1); color: var(--ink-muted); }
.type-tag.fact { color: var(--primary); border-color: var(--primary); }
.type-tag.computed { color: var(--success); border-color: var(--success); }
.c-highlight { font-size: 22px; font-weight: 700; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.c-summary { font-size: 12.5px; color: var(--ink); }
.c-caliber { font-size: 12px; color: var(--ink-muted); }
.c-meta { font-size: 11.5px; color: var(--ink-muted); }
.c-actions { display: flex; gap: var(--space-3); margin-top: 4px; }
.act {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12.5px; color: var(--primary); background: none; border: none; cursor: pointer; padding: 0;
}
.act.ai { color: var(--ink-muted); }
.act:hover { text-decoration: underline; }
</style>
