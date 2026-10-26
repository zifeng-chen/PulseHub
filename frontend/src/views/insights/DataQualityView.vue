<script setup lang="ts">
/**
 * 数据质量 /insights/data-quality（07 §10）：
 * 不是盘点，不统计实物数量，不创建后台任务；修复只能打开业务页面或生成待确认建议。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import AppCard from '@/components/ui/AppCard.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

const route = useRoute()
const router = useRouter()

interface QualityItem {
  level: string
  level_label: string
  module: string
  title: string
  description: string
  action_to: string
  count: number
}
interface Quality {
  checked: boolean
  module_enabled: boolean
  score: number | null
  critical: number
  warning: number
  info: number
  last_checked_at: string | null
  is_inventory: boolean
  note: string
  items: QualityItem[]
}

const loading = ref(true)
const error = ref('')
const data = ref<Quality | null>(null)
const levelFilter = ref<'all' | 'critical' | 'warning' | 'info'>('all')

const rules = [
  { k: '完整性', v: '必填字段是否为空' },
  { k: '合法性', v: '金额、日期、币种、利率、数量是否符合范围' },
  { k: '一致性', v: '账户余额、资金变动、负债余额和净资产是否能对上' },
  { k: '关联性', v: '房贷是否关联不动产、车贷是否关联车辆、车位是否关联房产、租金是否关联租约' },
  { k: '及时性', v: '估值、保险、合同和账单是否过期' },
  { k: '重复性', v: '流水、账单、OCR 材料是否疑似重复' },
]

const filteredItems = computed(() => {
  const items = data.value?.items || []
  if (levelFilter.value === 'all') return items
  return items.filter((i) => i.level === levelFilter.value)
})

function fmtUpdated(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  return `${d.toLocaleDateString('zh-CN')} ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
}

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const result = await api.get<Quality>('/insights/data-quality')
    if (seq !== loadSeq) return
    data.value = result
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="dq">
    <PageHeader
      title="数据质量"
      subtitle="只检查财务数据是否完整、可关联、可用于统计与 AI 分析。不属于盘点。"
      :breadcrumb="(route.meta.breadcrumb as string[]) || ['脉诊', '数据质量']"
    />

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <section class="scores">
        <AppCard class="score-main" level="3">
          <p class="k">数据质量总分</p>
          <p class="n" :class="{ low: (data.score ?? 100) < 80 }">{{ data.score }}</p>
        </AppCard>
        <AppCard class="score"><p class="k">严重问题</p><p class="n danger">{{ data.critical }}</p></AppCard>
        <AppCard class="score"><p class="k">警告</p><p class="n warn">{{ data.warning }}</p></AppCard>
        <AppCard class="score"><p class="k">可忽略提示</p><p class="n">{{ data.info }}</p></AppCard>
      </section>

      <p class="note">
        {{ data.note }}
        <span class="checked">最近检查时间：{{ fmtUpdated(data.last_checked_at) }}</span>
      </p>

      <AppCard title="问题列表">
        <div class="filters">
          <button
            v-for="f in [{ v: 'all', l: '全部' }, { v: 'critical', l: '严重' }, { v: 'warning', l: '警告' }, { v: 'info', l: '提示' }]"
            :key="f.v" class="flt" :class="{ on: levelFilter === f.v }" type="button"
            @click="levelFilter = f.v as any"
          >
            {{ f.l }}
          </button>
        </div>

        <StateBlock
          v-if="!filteredItems.length"
          state="empty"
          title="当前没有发现数据质量问题"
          description="随着业务数据录入，未分类流水、缺币种、估值过期、关联缺失、重复流水与对账差异会在这里列出。"
          hint="修复按钮只能打开对应业务页面或生成待确认建议，质量中心不会直接修改正式数据。"
        />
        <ul v-else class="items">
          <li v-for="(item, idx) in filteredItems" :key="idx">
            <div class="i-head">
              <span class="level" :class="item.level">{{ item.level_label }}</span>
              <strong>{{ item.title }}</strong>
              <span class="module">{{ item.module }}</span>
              <span class="count">{{ item.count }} 项</span>
            </div>
            <p class="desc">{{ item.description }}</p>
            <button class="fix" type="button" @click="router.push(item.action_to)">
              去处理 <ArrowRight :size="13" />
            </button>
          </li>
        </ul>
      </AppCard>

      <AppCard title="质量规则">
        <ul class="rules">
          <li v-for="r in rules" :key="r.k">
            <strong>{{ r.k }}</strong>
            <span>{{ r.v }}</span>
          </li>
        </ul>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.dq { display: flex; flex-direction: column; gap: var(--space-4); }
.scores { display: grid; grid-template-columns: 1.4fr 1fr 1fr 1fr; gap: var(--space-4); }
.score-main .n { font-size: 32px; font-weight: 700; color: var(--ink-strong); }
.score-main .n.low { color: var(--warning); }
.score .k { font-size: 12.5px; color: var(--ink-muted); }
.score .n { font-size: 24px; font-weight: 600; color: var(--ink-strong); }
.n.danger { color: var(--danger); }
.n.warn { color: var(--warning); }
.note { font-size: 12.5px; color: var(--ink-muted); display: flex; flex-direction: column; gap: 4px; }
.note .checked { color: var(--ink-muted); }
.filters { display: flex; gap: 6px; margin-bottom: var(--space-3); }
.flt {
  font-size: 12px; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--glass-1); color: var(--ink-muted); cursor: pointer;
}
.flt.on { color: var(--ink-strong); border-color: var(--primary); }
.items { display: flex; flex-direction: column; gap: var(--space-3); }
.items li { padding: var(--space-3) var(--space-4); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); }
.i-head { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.level { font-size: 11.5px; padding: 1px 8px; border-radius: 999px; }
.level.critical { color: var(--danger); background: rgba(225, 87, 75, .12); }
.level.warning { color: var(--warning); background: rgba(244, 166, 42, .14); }
.level.info { color: var(--primary); background: rgba(53, 109, 255, .12); }
.i-head strong { font-size: 13.5px; color: var(--ink-strong); }
.module { font-size: 11.5px; color: var(--ink-muted); }
.count { margin-left: auto; font-size: 12px; color: var(--ink-muted); }
.desc { margin-top: 4px; font-size: 12.5px; color: var(--ink); }
.fix { margin-top: 8px; display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; color: var(--primary); background: none; border: none; cursor: pointer; padding: 0; }
.fix:hover { text-decoration: underline; }
.rules { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: var(--space-3); }
.rules li { display: flex; flex-direction: column; gap: 2px; padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); }
.rules strong { font-size: 13px; color: var(--ink-strong); }
.rules span { font-size: 12.5px; color: var(--ink-muted); }
@media (max-width: 900px) { .scores { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
