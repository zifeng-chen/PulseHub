<script setup lang="ts">
/**
 * 家庭台账 · 总览（仪表盘版：环形健康度 + 时间轴待办 + 分层布局）。
 *
 * 与「脉枢台」的分工是硬约束：
 * - 脉枢台回答「家里有多少钱」——净资产、账户余额、资产价值、投资总额、
 *   负债剩余、收支趋势与结构占比，全是金额与趋势；
 * - 本页回答「这账记得对不对、还差什么、下一步做什么」——**整页不出现合计金额**，
 *   只有计数、天数、状态三档与缺口描述。
 *
 * 视觉锚点：顶部一个环形健康度仪表盘（五维度分段着色），与脉枢台的
 * 「净资产大数字」形成对称——脉枢台看钱，这里看账的健康度。
 * 所有数字来自后端 /dashboard/workbench，前端不自行计算。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertTriangle, BellRing, CheckCircle2, ChevronRight, Clock,
  Plus, RefreshCw, Sparkles,
} from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

type Tone = 'ok' | 'warn' | 'bad'

interface Dim { key: string; label: string; state: Tone; count: number; detail: string; to: string }
interface Gap { key: string; title: string; affects: string; count: number; to: string; severity: Tone }
interface QueueItem {
  key: string; title: string; type: string; type_label: string
  due_date: string | null; days_left: number | null; overdue: boolean
  to: string; hint: string
}
interface Workbench {
  period_label: string
  health: { state: Tone; state_label: string; gap_total: number; summary: string; dims: Dim[] }
  gaps: Gap[]
  queue: {
    today: QueueItem[]; week: QueueItem[]; later: QueueItem[]
    counts: { today: number; week: number; later: number }
  }
  updated_at: string
}

const router = useRouter()
const ui = useUiStore()
const loading = ref(true)
const error = ref('')
const data = ref<Workbench | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Workbench>('/dashboard/workbench')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

const totalGaps = computed(() => (data.value?.gaps || []).reduce((sum, g) => sum + g.count, 0))

// ---------- 环形健康度仪表盘 ----------
const RING_R = 74
const RING_C = 2 * Math.PI * RING_R

/** 状态 -> 颜色（读 CSS 变量时需在模板里动态绑定，这里返回 token 名，模板用 var 取色） */
function toneColor(t: Tone): string {
  return t === 'bad' ? 'var(--danger)' : t === 'warn' ? 'var(--warning)' : 'var(--success)'
}

/** 五个维度分段：每段长度相等，颜色按各自状态。返回 SVG stroke-dasharray 描述 */
const ringSegments = computed(() => {
  const dims = data.value?.health.dims || []
  const n = Math.max(dims.length, 1)
  const seg = RING_C / n
  const gap = 3 // 段间距（弧长）
  const segLen = seg - gap
  return dims.map((d, i) => {
    const offset = -(i * seg) // 负偏移让段从顶部顺时针排开
    return {
      key: d.key,
      color: toneColor(d.state),
      dasharray: `${segLen} ${RING_C - segLen}`,
      dashoffset: offset,
    }
  })
})

/** 圆心主状态：缺 0 显示「齐全」，否则显示缺口数 */
const ringCenter = computed(() => {
  const h = data.value?.health
  if (!h) return { num: '—', unit: '' }
  if (h.gap_total === 0) return { num: '齐全', unit: '' }
  return { num: String(h.gap_total), unit: '处待补' }
})

/** 到期标签：逾期优先，其次「今天 / N 天后」 */function dueLabel(q: QueueItem): string {
  if (q.type === 'data_gap') return '顺手做'
  if (q.days_left == null) return '待安排'
  if (q.overdue || q.days_left < 0) return `逾期 ${Math.abs(q.days_left)} 天`
  if (q.days_left === 0) return '今天'
  return `${q.days_left} 天后`
}

/** 时间轴节点的紧迫度档位：逾期=bad，今天=warn，本周/待安排=默认 */
function nodeTone(q: QueueItem): Tone {
  if (q.overdue || (q.days_left != null && q.days_left < 0)) return 'bad'
  if (q.days_left === 0) return 'warn'
  return 'ok'
}

/** 时间标签：当天显示时分，昨天显示「昨天」，更早显示月-日 */
function timeLabel(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  const yest = new Date(now)
  yest.setDate(now.getDate() - 1)
  if (d.toDateString() === yest.toDateString()) return '昨天'
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`
}

onMounted(load)
</script>

<template>
  <div class="wb">
    <PageHeader
      title="家庭台账"
      subtitle="这里看的是「账记得对不对、还差什么、今天做什么」；净资产、收支与结构分析请到「脉枢台」。"
      :breadcrumb="['家庭台账', '总览']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="14" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiPanel('家庭台账')">
        <Sparkles :size="15" /> 问 AI
      </AppButton>
      <AppButton variant="primary" @click="router.push('/ledger/transactions?new=1')">
        <Plus :size="15" /> 记一笔
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <!-- 顶部总览带：环形健康度（主角）+ 五维图例 + 一句话 -->
      <AppCard class="hero" level="3" aurora>
        <div class="hero-grid">
          <!-- 环形仪表盘 -->
          <div class="ring-wrap">
            <svg class="ring" :viewBox="`0 0 ${RING_R * 2 + 24} ${RING_R * 2 + 24}`" role="img"
                 :aria-label="`台账健康度：${data.health.state_label}`">
              <circle
                class="ring-track"
                :cx="RING_R + 12" :cy="RING_R + 12" :r="RING_R"
                fill="none" stroke="var(--glass-2)" stroke-width="14"
              />
              <circle
                v-for="s in ringSegments" :key="s.key"
                class="ring-seg"
                :cx="RING_R + 12" :cy="RING_R + 12" :r="RING_R"
                fill="none" :stroke="s.color" stroke-width="14" stroke-linecap="butt"
                :stroke-dasharray="s.dasharray" :stroke-dashoffset="s.dashoffset"
                :style="{ transform: `rotate(-90deg)`, transformOrigin: `${RING_R + 12}px ${RING_R + 12}px` }"
              />
              <text class="ring-num" :x="RING_R + 12" :y="RING_R + 8" text-anchor="middle">
                {{ ringCenter.num }}
              </text>
              <text v-if="ringCenter.unit" class="ring-unit" :x="RING_R + 12" :y="RING_R + 26" text-anchor="middle">
                {{ ringCenter.unit }}
              </text>
            </svg>
          </div>

          <!-- 维度图例 + 摘要 -->
          <div class="hero-body">
            <div class="hero-title">
              <span class="pulse" :class="data.health.state" />
              <span class="hl">台账健康度</span>
              <span class="hv" :class="data.health.state">{{ data.health.state_label }}</span>
            </div>
            <ul class="legend">
              <li v-for="d in data.health.dims" :key="d.key">
                <button type="button" class="lg" @click="router.push(d.to)">
                  <span class="lg-dot" :class="d.state" />
                  <span class="lg-name">{{ d.label }}</span>
                  <span class="lg-count" :class="d.state">{{ d.count ? `${d.count} 处` : '齐全' }}</span>
                </button>
              </li>
            </ul>
            <p class="hero-summary">{{ data.health.summary }}</p>
          </div>
        </div>
      </AppCard>

      <!-- 主区：左宽（时间轴待办）+ 右窄（待补齐 + 最近改动） -->
      <div class="grid">
        <!-- 左列：时间轴待办 -->
        <div class="col col-main">
          <AppCard class="pane" level="2">
            <header class="pane-head">
              <h3><Clock :size="15" /> 待办时间轴</h3>
              <span class="pill" :class="data.queue.counts.today ? 'bad' : 'ok'">
                今天 {{ data.queue.counts.today }} 件
              </span>
            </header>
            <p class="pane-sub">逾期 → 今天 → 本周 → 待安排，越靠上越紧急</p>

            <div class="timeline">
              <!-- 今天 -->
              <section class="tl-group">
                <div class="tl-label">
                  <span class="tl-tag" :class="data.queue.counts.today ? 'bad' : 'ok'">今天</span>
                  <span class="tl-sub">{{ data.queue.counts.today ? `${data.queue.counts.today} 件` : '无' }}</span>
                </div>
                <div v-if="data.queue.today.length" class="tl-items">
                  <button
                    v-for="q in data.queue.today" :key="q.key" type="button" class="tl-item"
                    @click="q.to && router.push(q.to)"
                  >
                    <span class="tl-node" :class="nodeTone(q)" />
                    <span class="tl-body">
                      <span class="tl-title">{{ q.title }}</span>
                      <span class="tl-hint">{{ q.hint }}</span>
                    </span>
                    <span class="tl-due" :class="nodeTone(q)">{{ dueLabel(q) }}</span>
                    <ChevronRight :size="13" class="rgo" />
                  </button>
                </div>
                <p v-else class="allgood"><CheckCircle2 :size="14" /> 今天没有到期事项</p>
              </section>

              <!-- 本周 -->
              <section class="tl-group">
                <div class="tl-label">
                  <span class="tl-tag" :class="data.queue.counts.week ? 'warn' : 'ok'">本周</span>
                  <span class="tl-sub">{{ data.queue.counts.week ? `${data.queue.counts.week} 件` : '无' }}</span>
                </div>
                <div v-if="data.queue.week.length" class="tl-items">
                  <button
                    v-for="q in data.queue.week" :key="q.key" type="button" class="tl-item"
                    @click="q.to && router.push(q.to)"
                  >
                    <span class="tl-node" :class="nodeTone(q)" />
                    <span class="tl-body">
                      <span class="tl-title">{{ q.title }}</span>
                      <span class="tl-hint">{{ q.type_label }}</span>
                    </span>
                    <span class="tl-due">{{ dueLabel(q) }}</span>
                    <ChevronRight :size="13" class="rgo" />
                  </button>
                </div>
                <p v-else class="allgood"><CheckCircle2 :size="14" /> 本周没有到期事项</p>
              </section>

              <!-- 待安排（折叠） -->
              <section v-if="data.queue.later.length" class="tl-group">
                <details class="tl-later">
                  <summary class="tl-label">
                    <span class="tl-tag muted">待安排</span>
                    <span class="tl-sub">{{ data.queue.counts.later }} 件</span>
                  </summary>
                  <div class="tl-items">
                    <button
                      v-for="q in data.queue.later" :key="q.key" type="button" class="tl-item"
                      @click="q.to && router.push(q.to)"
                    >
                      <span class="tl-node" :class="nodeTone(q)" />
                      <span class="tl-body">
                        <span class="tl-title">{{ q.title }}</span>
                        <span class="tl-hint">{{ q.type_label }}</span>
                      </span>
                      <span class="tl-due">{{ dueLabel(q) }}</span>
                      <ChevronRight :size="13" class="rgo" />
                    </button>
                  </div>
                </details>
              </section>
            </div>
          </AppCard>
        </div>

        <!-- 右列：待补齐 -->
        <div class="col col-side">
          <AppCard class="pane" level="2">
            <header class="pane-head">
              <h3><AlertTriangle :size="15" /> 待补齐</h3>
              <span class="pill" :class="totalGaps ? 'warn' : 'ok'">{{ totalGaps }} 处</span>
            </header>
            <p class="pane-sub">补齐这些，脉枢台的数字才会更接近真实</p>
            <ul v-if="data.gaps.length" class="rows">
              <li v-for="g in data.gaps" :key="g.key" role="button" tabindex="0"
                  @click="router.push(g.to)" @keyup.enter="router.push(g.to)">
                <span class="dot" :class="g.severity" />
                <span class="rt">{{ g.title }}</span>
                <span class="ra">影响 {{ g.affects }}</span>
                <ChevronRight :size="13" class="rgo" />
              </li>
            </ul>
            <p v-else class="allgood"><CheckCircle2 :size="14" /> 没有缺口，台账要素齐全</p>
          </AppCard>
        </div>
      </div>

      <p class="foot">
        统计于 {{ timeLabel(data.updated_at) }} · 期间 {{ data.period_label }} ·
        金额与趋势请到「脉枢台」，本页不做金额汇总
      </p>
    </template>
  </div>
</template>

<style scoped>
.wb { display: flex; flex-direction: column; gap: var(--space-4); }

/* ---------- 状态点 / 色调 ---------- */
.dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--ink-muted); }
.dot.ok { background: var(--success); }
.dot.warn { background: var(--warning); }
.dot.bad { background: var(--danger); }

/* ---------- 顶部总览带 ---------- */
.hero :deep(.card-body) { padding: var(--space-5); }
.hero-grid {
  display: grid; grid-template-columns: auto 1fr; gap: var(--space-6);
  align-items: center;
}

.ring-wrap { display: flex; align-items: center; justify-content: center; }
.ring { width: 190px; height: 190px; }
.ring-track { stroke-width: 14; }
.ring-seg { transition: stroke var(--motion-fast) var(--ease-out); }
.ring-num {
  font-size: 30px; font-weight: 750; fill: var(--ink-strong);
  font-variant-numeric: tabular-nums; letter-spacing: -0.5px;
}
.ring-unit { font-size: 12px; fill: var(--ink-muted); }

.hero-body { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.hero-title { display: flex; align-items: center; gap: 9px; }
.pulse {
  width: 9px; height: 9px; border-radius: 50%; flex: none;
  background: var(--success);
}
.pulse.warn { background: var(--warning); box-shadow: 0 0 0 4px rgba(214, 158, 46, 0.14); }
.pulse.bad { background: var(--danger); box-shadow: 0 0 0 4px rgba(232, 93, 117, 0.14); }
.hero-title .hl { font-size: 13px; color: var(--ink-muted); }
.hero-title .hv { font-size: 15px; font-weight: 650; }
.hero-title .hv.warn { color: var(--warning); }
.hero-title .hv.bad { color: var(--danger); }
.hero-title .hv.ok { color: var(--success); }

.legend {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 18px; margin: 0; padding: 0; list-style: none;
}
@media (max-width: 1100px) {
  .legend { grid-template-columns: 1fr; }
}
.legend .lg {
  display: flex; align-items: center; gap: 9px; width: 100%;
  padding: 7px 9px; border: none; background: none; border-radius: 8px;
  cursor: pointer; text-align: left;
  transition: background var(--motion-fast) var(--ease-out);
}
.legend .lg:hover { background: var(--glass-2); }
.legend .lg:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.lg-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--success); }
.lg-dot.warn { background: var(--warning); }
.lg-dot.bad { background: var(--danger); }
.lg-name { font-size: 12.5px; color: var(--ink-strong); }
.lg-count {
  margin-left: auto; font-size: 11.5px; color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}
.lg-count.warn { color: var(--warning); }
.lg-count.bad { color: var(--danger); }
.lg-count.ok { color: var(--success); }

.hero-summary {
  margin: 0; font-size: 12px; color: var(--ink-muted); line-height: 1.6;
  padding-top: 12px; border-top: 1px dashed var(--border);
}

/* ---------- 主区两列 ---------- */
.grid {
  display: grid; grid-template-columns: 1.35fr 1fr; gap: var(--space-4);
  align-items: stretch;
}
.col { display: flex; flex-direction: column; gap: var(--space-4); min-width: 0; }
/* 右列：待补齐卡撑满剩余高度，目录贴底，两列底部对齐 */
.col-side .pane { flex: 1; display: flex; flex-direction: column; }
.col-side .pane :deep(.card-body) {
  flex: 1; display: flex; flex-direction: column;
}
.col-side .rows { flex: 1; align-content: space-evenly; }

.pane :deep(.card-body) { padding: var(--space-4); }
.pane-head { display: flex; align-items: center; gap: 8px; }
.pane-head h3 {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 13.5px; font-weight: 600; color: var(--ink-strong);
}
.pane-head h3 :deep(svg) { color: var(--ink-muted); }
.pill {
  margin-left: auto; font-size: 11px; padding: 2px 8px; border-radius: 999px;
  background: var(--glass-2); color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}
.pill.warn { background: rgba(214, 158, 46, 0.14); color: var(--warning); }
.pill.bad { background: rgba(232, 93, 117, 0.14); color: var(--danger); }
.pill.ok { background: rgba(25, 183, 122, 0.13); color: var(--success); }
.pane-sub { font-size: 11.5px; color: var(--ink-muted); margin-top: 5px; }

/* ---------- 时间轴 ---------- */
.timeline { display: flex; flex-direction: column; margin-top: 12px; }
.tl-group { position: relative; padding-left: 0; }
.tl-label {
  display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
}
.tl-later > summary { list-style: none; cursor: pointer; }
.tl-later > summary::-webkit-details-marker { display: none; }
.tl-tag {
  font-size: 11px; font-weight: 600; padding: 2px 9px; border-radius: 6px;
  background: var(--glass-2); color: var(--ink-muted);
}
.tl-tag.bad { background: rgba(232, 93, 117, 0.14); color: var(--danger); }
.tl-tag.warn { background: rgba(214, 158, 46, 0.14); color: var(--warning); }
.tl-tag.ok { background: rgba(25, 183, 122, 0.13); color: var(--success); }
.tl-tag.muted { opacity: 0.75; }
.tl-sub { font-size: 11px; color: var(--ink-muted); }

.tl-items {
  display: flex; flex-direction: column; position: relative;
  margin-left: 5px; padding-left: 16px;
}
/* 竖线 */
.tl-items::before {
  content: ''; position: absolute; left: 0; top: 4px; bottom: 4px;
  width: 2px; background: var(--border); border-radius: 1px;
}
.tl-item {
  position: relative; display: flex; align-items: center; gap: 10px;
  padding: 9px 4px; border: none; background: none; border-radius: 8px;
  cursor: pointer; text-align: left; width: 100%;
  transition: background var(--motion-fast) var(--ease-out);
}
.tl-item:hover { background: var(--glass-2); }
.tl-item:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.tl-node {
  position: absolute; left: -16px; top: 50%; transform: translate(-50%, -50%);
  width: 9px; height: 9px; border-radius: 50%; background: var(--ink-muted);
  box-shadow: 0 0 0 3px var(--bg, var(--color-background-primary));
}
.tl-node.bad { background: var(--danger); }
.tl-node.warn { background: var(--warning); }
.tl-node.ok { background: var(--primary); }
.tl-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.tl-title {
  font-size: 12.5px; color: var(--ink-strong);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tl-hint { font-size: 11px; color: var(--ink-muted); }
.tl-due {
  font-size: 11px; color: var(--ink-muted); flex: none;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.tl-due.bad { color: var(--danger); }
.tl-due.warn { color: var(--warning); }
.rgo { color: var(--ink-muted); flex: none; opacity: 0.7; }

.tl-later .tl-items { margin-top: 6px; }

/* ---------- 待补齐 / 最近改动 ---------- */
.rows { display: flex; flex-direction: column; margin-top: 8px; }
.rows li {
  display: flex; align-items: center; gap: 9px;
  padding: 9px 2px; border-top: 1px solid var(--border);
  cursor: pointer; transition: background var(--motion-fast) var(--ease-out);
  border-radius: 6px;
}
.rows li:hover { background: var(--glass-2); }
.rows li:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.rows .rt {
  font-size: 12.5px; color: var(--ink-strong); flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rows .ra { font-size: 11px; color: var(--ink-muted); flex: none; }
.rows .rgo { color: var(--ink-muted); flex: none; opacity: 0.7; }

.allgood {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--success); margin-top: 12px;
}

.foot { font-size: 11px; color: var(--ink-muted); }

/* ---------- 响应式 ---------- */
@media (max-width: 1100px) {
  .hero-grid { grid-template-columns: 1fr; gap: var(--space-4); }
  .ring-wrap { order: -1; }
  .grid { grid-template-columns: 1fr; }
}

@media (max-width: 640px) {
  .hero :deep(.card-body) { padding: 14px; }
  .ring { width: 150px; height: 150px; }
  .ring-num { font-size: 25px; }
  .pane :deep(.card-body) { padding: 12px 13px; }
  .rows li { padding: 8px 0; gap: 7px; }
  .rows .rt { font-size: 12px; }
  .rows .ra { display: none; }
  .tl-items { padding-left: 14px; }
  .tl-node { left: -14px; }
  .tl-due { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .lg, .tl-item { transition: none; }
}
</style>
