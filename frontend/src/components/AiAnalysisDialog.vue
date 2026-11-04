<script setup lang="ts">
/**
 * 页面级 AI 分析弹窗。
 *
 * 用户反馈两轮：
 * 1. 点「AI 现金流分析」后只是打开 AI 助手，等于把「点按钮 → 再问一遍」推回用户；
 * 2. 加载态丑（三条骨架 + 大字「正在加载」+ 副标题三层重复，按钮还挂在下面），
 *    结果太专业（一屏指标卡片，先看到术语才看到结论）。
 *
 * 所以这里的信息层级是「人话优先」：
 *   一句结论 → 要注意什么 → 今天能做什么 →（折叠的）明细数据 → 数据来源
 * 指标是证据，不是主角；模型不可用时也照样有一句人话 + 折叠的明细。
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronDown, MessageSquare, RefreshCw, Settings2 } from 'lucide-vue-next'
import AppModal from '@/components/ui/AppModal.vue'
import AppButton from '@/components/ui/AppButton.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import { api, errorMessage } from '@/api/client'
import { useUiStore } from '@/stores/ui'

interface AnalysisFact {
  label: string
  value: string
  hint?: string
}

interface AnalysisBullet {
  label: string
  text: string
}

interface AnalysisCheck {
  level: 'error' | 'warn' | 'info'
  title: string
  detail: string
}

interface AnalysisResult {
  scope: string
  scope_label: string
  focus: string
  state: 'ready' | 'unavailable'
  summary: string | null
  bullets: AnalysisBullet[]
  facts: AnalysisFact[]
  plain_summary?: string | null
  checks?: AnalysisCheck[]
  hint: string
  model_type: string
  provider?: string
  generated_at: string
}

const ui = useUiStore()
const router = useRouter()

const loading = ref(false)
const error = ref('')
const data = ref<AnalysisResult | null>(null)

const open = computed(() => ui.aiAnalysisOpen)
const title = computed(() => {
  const base = data.value?.scope_label || '分析'
  return ui.aiAnalysisTitle ? `${ui.aiAnalysisTitle}` : `AI ${base}分析`
})

function bullet(label: string): string {
  return data.value?.bullets.find((b) => b.label === label)?.text?.trim() || ''
}

/** 第一句必须是结论：模型分行成功就用「结论」行；没分行但整段回来了就用整段；
 *  模型完全不可用时才退回确定性那句（好歹有内容，不至于空白）。 */
const headline = computed(() => {
  if (!data.value) return ''
  return bullet('结论') || data.value.summary || data.value.plain_summary || ''
})
const attention = computed(() => bullet('关注'))
const advice = computed(() => bullet('建议'))

/** 模型没解读但规则检查发现了问题，也让它顶到「要注意」的位置 */
const checkAttention = computed(() => {
  const first = data.value?.checks?.[0]
  return first ? `${first.title}：${first.detail}` : ''
})

/** 已经拿第一条去当「要注意」时，列表里就不再重复它 */
const checkList = computed(() => {
  const all = data.value?.checks || []
  return attention.value ? all : all.slice(1)
})

const hasIssue = computed(() => Boolean(attention.value || checkAttention.value))
const ready = computed(() => data.value?.state === 'ready')
/** 没拿到 AI 解读时，明细就是唯一有用的东西，直接展开 */
const factsOpen = computed(() => !ready.value)

const source = computed(() => {
  const d = data.value
  if (!d) return ''
  const parts = [`分析范围：${d.focus}`]
  if (d.state === 'ready') {
    const who = d.model_type === 'third_party' ? '第三方 AI' : '本地 AI'
    parts.push(`由${who}解读`, d.generated_at.slice(11, 16))
  } else {
    parts.push('仅为系统统计结果')
  }
  return parts.join(' · ')
})

async function run() {
  if (!ui.aiAnalysisOpen || loading.value) return
  loading.value = true
  error.value = ''
  data.value = null
  try {
    data.value = await api.get<AnalysisResult>('/insights/ai-analysis', {
      scope: ui.aiAnalysisScope,
      period: ui.aiAnalysisPeriod,
      target_id: ui.aiAnalysisTargetId || undefined,
    })
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

watch(
  () => [ui.aiAnalysisOpen, ui.aiAnalysisScope, ui.aiAnalysisPeriod] as const,
  ([isOpen]) => { if (isOpen) void run() },
  { immediate: true },
)

function close() {
  ui.closeAiAnalysis()
}

/** 深挖：转成对话继续问（保留真正的助手入口，但不再是默认动作） */
function askAssistant() {
  const ctx = data.value ? `${data.value.scope_label}：${data.value.focus}` : ''
  close()
  ui.openAiPanel(ctx)
}

function goSettings() {
  close()
  router.push('/settings/local-ai')
}
</script>

<template>
  <AppModal :open="open" :title="title" size="md" hide-footer @close="close">
    <div class="ai-analysis">
      <!-- 加载：一处呼吸，不再用「骨架 + 标题 + 副标题」三层重复 -->
      <div v-if="loading" class="thinking">
        <span class="pulse-dots" aria-hidden="true"><i /><i /><i /></span>
        <p>正在看你的账，通常几秒就好</p>
      </div>

      <StateBlock
        v-else-if="error"
        state="error"
        :title="'没能拿到分析'"
        :description="error"
        retryable
        @retry="run"
      />

      <template v-else-if="data">
        <!-- 1. 结论：一句人话，字号最大 -->
        <p v-if="headline" class="headline">{{ headline }}</p>

        <!-- 2. 要注意 -->
        <div v-if="hasIssue" class="callout warn">
          <span class="co-label">要注意</span>
          <p>{{ attention || checkAttention }}</p>
        </div>

        <!-- 3. 可以这样做 -->
        <div v-if="advice" class="callout tip">
          <span class="co-label">可以这样做</span>
          <p>{{ advice }}</p>
        </div>

        <!-- 4. 体检其余条目（租约体检这类规则检查，逐条列出） -->
        <ul v-if="checkList.length" class="checks">
          <li v-for="(c, i) in checkList" :key="i" :class="`lv-${c.level}`">
            <span class="dot" />
            <div>
              <strong>{{ c.title }}</strong>
              <p>{{ c.detail }}</p>
            </div>
          </li>
        </ul>

        <!-- 5. 明细默认折叠：指标是证据，不该一上来就糊一屏 -->
        <details v-if="data.facts.length" class="facts-wrap" :open="factsOpen">
          <summary>
            <ChevronDown :size="14" class="chev" />
            明细数据（{{ data.facts.length }} 项）
          </summary>
          <div class="facts">
            <div v-for="f in data.facts" :key="f.label" class="fact">
              <span class="k">{{ f.label }}</span>
              <strong class="v">{{ f.value }}</strong>
              <span v-if="f.hint" class="h">{{ f.hint }}</span>
            </div>
          </div>
        </details>

        <!-- 模型不可用：说清原因，给出下一步 -->
        <StateBlock
          v-if="!ready"
          state="pending"
          title="这段话暂时没生成出来"
          :description="data.hint || '本地 AI 未连接，上面的明细仍可参考。'"
        >
          <template #actions>
            <AppButton variant="secondary" @click="run">再试一次</AppButton>
            <AppButton variant="primary" @click="goSettings">
              <Settings2 :size="15" /> 去配置本地 AI
            </AppButton>
          </template>
        </StateBlock>

        <p class="source">{{ source }}</p>
      </template>

      <!-- 加载中不给操作：避免重复请求叠出两次 AI 调用 -->
      <div v-if="!loading" class="ops">
        <AppButton variant="ghost" @click="run">
          <RefreshCw :size="15" /> 重新生成
        </AppButton>
        <AppButton variant="ghost" @click="askAssistant">
          <MessageSquare :size="15" /> 接着问 AI 助手
        </AppButton>
      </div>
    </div>
  </AppModal>
</template>

<style scoped>
.ai-analysis { display: flex; flex-direction: column; gap: 10px; }

/* ---------- 加载 ---------- */
.thinking {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  /* 给个最小高度：加载中不留一个「纸条」弹窗，出结果时也不会突然撑开 */
  min-height: 150px; padding: 18px 4px;
  color: var(--ink-muted); font-size: 13px;
}
.thinking p { margin: 0; }
.pulse-dots { display: inline-flex; gap: 4px; }
.pulse-dots i {
  width: 6px; height: 6px; border-radius: 50%; background: var(--primary, #356dff);
  animation: dot-breathe 1.1s var(--ease-out, ease) infinite;
}
.pulse-dots i:nth-child(2) { animation-delay: 0.16s; }
.pulse-dots i:nth-child(3) { animation-delay: 0.32s; }
@keyframes dot-breathe {
  0%, 100% { opacity: 0.25; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-2px); }
}

/* ---------- 结论 ---------- */
.headline {
  margin: 0; padding: 13px 15px; border-radius: 12px;
  font-size: 15px; line-height: 1.75; color: var(--ink-strong);
  background: var(--glass-2); border-left: 3px solid var(--primary, #356dff);
}

/* ---------- 要注意 / 可以这样做 ---------- */
.callout {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 13px; border-radius: 11px;
  border: 1px solid var(--border); background: var(--glass-1);
}
.callout p { margin: 0; font-size: 13px; line-height: 1.7; color: var(--ink-strong); }
.co-label {
  flex: none; margin-top: 1px; padding: 1px 7px; border-radius: 6px;
  font-size: 11px; font-weight: 600; white-space: nowrap;
}
.callout.warn { border-left: 3px solid var(--warning, #b26a00); }
.callout.warn .co-label { color: var(--warning, #b26a00); background: color-mix(in srgb, var(--warning, #b26a00) 12%, transparent); }
.callout.tip { border-left: 3px solid var(--success, #19875a); }
.callout.tip .co-label { color: var(--success, #19875a); background: color-mix(in srgb, var(--success, #19875a) 12%, transparent); }

/* ---------- 体检其余条目 ---------- */
.checks { display: flex; flex-direction: column; gap: 7px; margin: 0; padding: 0; list-style: none; }
.checks li {
  display: flex; gap: 9px; align-items: flex-start;
  padding: 9px 11px; border-radius: 10px;
  border: 1px solid var(--border); background: var(--glass-1);
}
.checks .dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 6px; flex: none; background: var(--ink-muted); }
.checks strong { font-size: 13px; color: var(--ink-strong); }
.checks p { margin: 2px 0 0; font-size: 12px; line-height: 1.6; color: var(--ink-muted); }
.checks .lv-error .dot { background: var(--danger, #d64545); }
.checks .lv-warn .dot { background: var(--warning, #b26a00); }
.checks .lv-info .dot { background: var(--primary, #356dff); }

/* ---------- 明细（折叠） ---------- */
.facts-wrap { border-top: 1px dashed var(--border); padding-top: 8px; }
.facts-wrap > summary {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; color: var(--ink-muted); cursor: pointer; list-style: none;
  padding: 2px 0;
}
.facts-wrap > summary::-webkit-details-marker { display: none; }
.facts-wrap > summary:hover { color: var(--ink-strong); }
.facts-wrap .chev { transition: transform var(--motion-fast, 160ms) var(--ease-out, ease); }
.facts-wrap[open] .chev { transform: rotate(180deg); }
.facts {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 8px; margin-top: 8px;
}
.fact {
  padding: 9px 11px; border-radius: 10px; background: var(--glass-1);
  border: 1px solid var(--border); display: flex; flex-direction: column; gap: 3px;
}
.fact .k { font-size: 11px; color: var(--ink-muted); }
.fact .v { font-size: 14px; color: var(--ink-strong); font-weight: 600; letter-spacing: 0.01em; }
.fact .h { font-size: 10.5px; color: var(--ink-muted); line-height: 1.5; }

.source { margin: 0; font-size: 11px; color: var(--ink-muted); }

.ops { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; border-top: 1px solid var(--border); padding-top: 10px; }

@media (prefers-reduced-motion: reduce) {
  .pulse-dots i { animation: none; opacity: 0.7; }
  .facts-wrap .chev { transition: none; }
}
</style>
