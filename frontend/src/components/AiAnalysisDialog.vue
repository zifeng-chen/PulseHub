<script setup lang="ts">
/**
 * 页面级 AI 分析弹窗。
 *
 * 用户反馈：点「AI 摘要」「AI 现金流分析」这类按钮，之前只是打开 AI 助手，
 * 等于把「点按钮 → 再问一遍」推回给用户。这里点开即生成该页面的分析：
 * - 指标来自后端确定性统计（模型不可用时照样有数）
 * - 三条解读「结论 / 关注 / 建议」由模型生成，失败时说明原因并可重试
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { MessageSquare, RefreshCw, Settings2, Sparkles } from 'lucide-vue-next'
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
const source = computed(() => {
  const d = data.value
  if (!d || d.state !== 'ready') return ''
  const who = d.model_type === 'third_party' ? '第三方 AI' : '本地 AI'
  return `由${who}生成 · 指标来自确定性统计 · ${d.generated_at.slice(11, 16)}`
})

async function run() {
  if (!ui.aiAnalysisOpen) return
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
      <p v-if="data" class="focus">分析范围：{{ data.focus }}</p>

      <StateBlock v-if="loading" state="loading" />

      <StateBlock
        v-else-if="error"
        state="error"
        :description="error"
        retryable
        @retry="run"
      />

      <template v-else-if="data">
        <!-- 通俗总结：先用一句人话说清楚，再给指标 -->
        <p v-if="data.plain_summary" class="plain">{{ data.plain_summary }}</p>

        <!-- 指标：确定性统计，模型挂了也在 -->
        <div v-if="data.facts.length" class="facts">
          <div v-for="f in data.facts" :key="f.label" class="fact">
            <span class="k">{{ f.label }}</span>
            <strong class="v">{{ f.value }}</strong>
          </div>
        </div>

        <!-- 规则检查：缺项与条款问题，模型不可用时也照常展示 -->
        <div v-if="data.checks && data.checks.length" class="checks">
          <p class="checks-title">体检发现 {{ data.checks.length }} 项</p>
          <div v-for="(c, i) in data.checks" :key="i" class="check" :class="`lv-${c.level}`">
            <span class="dot" />
            <div>
              <strong>{{ c.title }}</strong>
              <p>{{ c.detail }}</p>
            </div>
          </div>
        </div>
        <p v-else-if="data.scope === 'lease'" class="all-clear">
          关键信息与常见条款均已覆盖，未发现明显缺项。
        </p>

        <div v-if="data.state === 'ready' && data.bullets.length" class="bullets">
          <div v-for="b in data.bullets" :key="b.label" class="bullet" :class="`b-${b.label}`">
            <span class="tag">{{ b.label }}</span>
            <p>{{ b.text }}</p>
          </div>
        </div>
        <p v-else-if="data.state === 'ready' && data.summary" class="summary">{{ data.summary }}</p>

        <StateBlock
          v-else
          state="pending"
          title="AI 解读暂不可用"
          :description="data.hint || '模型未连接'"
        >
          <template #actions>
            <AppButton variant="secondary" @click="run">重试</AppButton>
            <AppButton variant="primary" @click="goSettings">前往配置本地 AI</AppButton>
          </template>
        </StateBlock>

        <p v-if="source" class="source">{{ source }}</p>
      </template>

      <div class="ops">
        <AppButton variant="ghost" :loading="loading" @click="run">
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
.checks { display: flex; flex-direction: column; gap: 7px; }
.checks-title { margin: 0 0 2px; font-size: 12px; color: var(--ink-muted); }
.check {
  display: flex; gap: 9px; align-items: flex-start;
  padding: 9px 11px; border-radius: 10px;
  border: 1px solid var(--border); background: var(--glass-1);
}
.check .dot {
  width: 7px; height: 7px; border-radius: 50%; margin-top: 6px; flex: none;
  background: var(--ink-muted);
}
.check strong { font-size: 13px; color: var(--ink-strong); }
.check p { margin: 2px 0 0; font-size: 12px; line-height: 1.6; color: var(--ink-muted); }
.check.lv-error .dot { background: var(--danger, #d64545); }
.check.lv-error { border-color: color-mix(in srgb, var(--danger, #d64545) 35%, var(--border)); }
.check.lv-warn .dot { background: var(--warning, #b26a00); }
.check.lv-info .dot { background: var(--primary, #356dff); }
.all-clear {
  margin: 0; padding: 10px 12px; border-radius: 10px; font-size: 12.5px;
  color: var(--success, #19875a); background: var(--glass-1); border: 1px solid var(--border);
}
.ai-analysis { display: flex; flex-direction: column; gap: 12px; }
.focus { margin: 0; font-size: 12px; color: var(--ink-muted); line-height: 1.5; }
.plain {
  margin: 0; padding: 10px 13px; border-radius: 10px;
  font-size: 12.5px; line-height: 1.7; color: var(--ink-strong);
  background: var(--glass-2); border-left: 3px solid var(--primary);
}

.facts {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 8px;
}
.fact {
  padding: 9px 11px; border-radius: 10px; background: var(--glass-1);
  border: 1px solid var(--border); display: flex; flex-direction: column; gap: 3px;
}
.fact .k { font-size: 11px; color: var(--ink-muted); }
.fact .v { font-size: 14px; color: var(--ink-strong); font-weight: 600; letter-spacing: 0.01em; }

.bullets { display: flex; flex-direction: column; gap: 8px; }
.bullet {
  display: flex; gap: 9px; align-items: flex-start;
  padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border);
  background: var(--glass-1);
}
.bullet .tag {
  flex: none; padding: 1px 7px; border-radius: 6px; font-size: 11px; font-weight: 600;
  background: var(--glass-2); color: var(--ink-muted);
}
.bullet p { margin: 0; font-size: 13px; line-height: 1.62; color: var(--ink-strong); }
.b-结论 { border-left: 3px solid var(--primary); }
.b-关注 { border-left: 3px solid var(--warning, #b26a00); }
.b-建议 { border-left: 3px solid #15803d; }
.summary { margin: 0; font-size: 13px; line-height: 1.7; color: var(--ink-strong); white-space: pre-wrap; }
.source { margin: 0; font-size: 11px; color: var(--ink-muted); }

.ops { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; margin-top: 2px; }
</style>
