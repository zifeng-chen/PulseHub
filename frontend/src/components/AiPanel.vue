<script setup lang="ts">
/**
 * 全局 AI 聊天面板（05 §3、09 §3）。
 * 消息流 + 历史对话 + 新建对话：发送后立即显示、思考中状态、可滚动查看历史。
 * 会话持久化到后端；模型未连接时保留确定性回答，并给出配置入口。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Sparkles, X, Send, Plus, History, Trash2, ChevronLeft, Copy, Check } from 'lucide-vue-next'
import { useUiStore } from '@/stores/ui'
import { api, errorMessage } from '@/api/client'

interface Conv {
  id: string
  title: string
  context: string | null
  message_count: number
  last_message_at: string | null
  created_at: string
}
interface Trust {
  model_type: string
  model_type_label: string
  base_currency: string
  period: string
  period_label: string
  source_count: number
  confirmed_count: number
  draft_count: number
  masked: boolean
  conclusion_types: Record<string, string>
  provider?: string | null
}
interface Msg {
  id: string
  role: 'user' | 'assistant'
  content: string
  state?: string | null
  model_type?: string | null
  sources?: string[]
  trust?: Trust | null
  created_at: string
}

const ui = useUiStore()
const router = useRouter()

const conversations = ref<Conv[]>([])
const currentId = ref('')
const messages = ref<Msg[]>([])
/** 模型选择（全局）：由右上角快捷键与面板下拉共同读写，auto=本地优先 / local=仅本地 / third_party=第三方 */
const modelType = computed({
  get: () => ui.aiModel,
  set: (v: 'auto' | 'local' | 'third_party') => ui.setAiModel(v),
})

const copiedId = ref('')
/** 真实连接状态（后端会探测 /models，不是"配置了就算连接"） */
interface ProviderState { key: string; status: string; status_label: string; detail: string }
const providerState = ref<Record<string, ProviderState>>({})

async function loadProviderStatus() {
  try {
    const res = await api.get<{ items: ProviderState[] }>('/ai/providers')
    providerState.value = Object.fromEntries((res.items || []).map((i) => [i.key, i]))
  } catch {
    providerState.value = {}
  }
}
/** 复制助手回复：优先 Clipboard API，非安全上下文退回 execCommand */
async function copyMessage(msg: Msg) {
  const text = msg.content || ''
  if (!text) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      document.body.removeChild(area)
    }
    copiedId.value = msg.id
    setTimeout(() => {
      if (copiedId.value === msg.id) copiedId.value = ''
    }, 1600)
  } catch {
    // 复制失败不打断对话，用户仍可手动选择文本
  }
}
const question = ref('')
const sending = ref(false)
const historyOpen = ref(false)
const bodyRef = ref<HTMLElement | null>(null)

const contextLine = computed(() => ui.aiPanelContext || '未指定页面上下文')
const recommendations = [
  '净资产为什么变化？',
  '本月消费增加在哪里？',
  '下月还款压力如何？',
  '哪些租金逾期了？',
]

async function scrollToBottom() {
  await nextTick()
  const el = bodyRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function loadConversations() {
  try {
    const res = await api.get<{ items: Conv[] }>('/ai/conversations')
    conversations.value = res.items
  } catch {
    conversations.value = []
  }
}

async function openConversation(id: string) {
  try {
    const res = await api.get<{ conversation: Conv; messages: Msg[] }>(`/ai/conversations/${id}`)
    currentId.value = id
    messages.value = res.messages
    historyOpen.value = false
    await scrollToBottom()
  } catch (err) {
    console.error(errorMessage(err))
  }
}

function newConversation() {
  currentId.value = ''
  messages.value = []
  historyOpen.value = false
}

async function removeConversation(id: string) {
  const res = await ui.askConfirm({
    title: '删除对话',
    message: '删除后该对话与消息不可恢复。',
    confirmText: '删除',
    danger: true,
  })
  if (!res.ok) return
  try {
    await api.delete(`/ai/conversations/${id}`)
    if (currentId.value === id) newConversation()
    await loadConversations()
  } catch (err) {
    console.error(errorMessage(err))
  }
}

async function send(text?: string) {
  const q = (text ?? question.value).trim()
  if (!q || sending.value) return
  question.value = ''
  const tmpId = `tmp-user-${Date.now()}`
  messages.value.push({ id: tmpId, role: 'user', content: q, created_at: new Date().toISOString() })
  sending.value = true
  await scrollToBottom()
  try {
    const res = await api.post<{ conversation: Conv; user_message: Msg; assistant_message: Msg }>(
      '/ai/messages',
      {
        conversation_id: currentId.value || null,
        question: q,
        context: contextLine.value,
        model_type: modelType.value,
      },
    )
    currentId.value = res.conversation.id
    const idx = messages.value.findIndex((m) => m.id === tmpId)
    if (idx >= 0) messages.value.splice(idx, 1, res.user_message)
    messages.value.push(res.assistant_message)
    await loadConversations()
  } catch (err) {
    messages.value.push({
      id: `err-${Date.now()}`, role: 'assistant', state: 'error',
      content: `调用失败：${errorMessage(err)}\n记账、台账、统计与历史查询不受影响。`,
      created_at: new Date().toISOString(),
    })
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

function goConfigure() {
  ui.closeAiPanel()
  router.push('/settings/local-ai')
}

function clearContext() {
  ui.aiPanelContext = ''
}

function fmtTime(v: string | null) {
  if (!v) return ''
  const d = new Date(v)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

watch(
  () => ui.aiPanelOpen,
  async (open) => {
    if (open) {
      await Promise.all([loadConversations(), loadProviderStatus()])
      await scrollToBottom()
    }
  },
)
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="ui.aiPanelOpen" class="ai-mask" :style="{ background: 'var(--scrim)' }" @click.self="ui.closeAiPanel()" />
    </Transition>
    <Transition name="slide">
      <aside v-if="ui.aiPanelOpen" class="ai-panel" aria-label="AI 助手">
        <header class="ai-head">
          <div class="t">
            <Sparkles :size="16" />
            <h3>AI 助手</h3>
          </div>
          <div class="head-actions">
            <button class="hbtn" type="button" title="新对话" aria-label="新对话" @click="newConversation">
              <Plus :size="15" />
            </button>
            <button class="hbtn" type="button" title="历史对话" aria-label="历史对话" @click="historyOpen = !historyOpen">
              <History :size="15" />
            </button>
            <button class="hbtn" type="button" aria-label="关闭 AI 助手" @click="ui.closeAiPanel()">
              <X :size="16" />
            </button>
          </div>
        </header>

        <!-- 历史对话 -->
        <div v-if="historyOpen" class="history">
          <div class="history-head">
            <button class="back" type="button" @click="historyOpen = false">
              <ChevronLeft :size="15" /> 返回对话
            </button>
            <span>历史对话（{{ conversations.length }}）</span>
          </div>
          <p v-if="!conversations.length" class="empty">
            暂无历史对话。发送第一条消息后会自动保存到云端。
          </p>
          <ul v-else class="conv-list">
            <li v-for="c in conversations" :key="c.id" :class="{ on: c.id === currentId }">
              <button class="conv-main" type="button" @click="openConversation(c.id)">
                <span class="ct">{{ c.title }}</span>
                <small>{{ c.message_count }} 条消息 · {{ fmtTime(c.last_message_at || c.created_at) }}</small>
              </button>
              <button class="del" type="button" title="删除对话" @click="removeConversation(c.id)">
                <Trash2 :size="14" />
              </button>
            </li>
          </ul>
        </div>

        <!-- 消息流 -->
        <div v-else ref="bodyRef" class="ai-body">
          <div v-if="!messages.length" class="welcome">
            <span class="bubble">
              我是本地的财务分析助手。所有统计金额都来自后端的确定性统计服务，我只负责解释与分析；
              任何需要写入正式台账的建议都必须由你确认。
            </span>
            <div class="reco">
              <p class="reco-title">试试这样问</p>
              <button v-for="r in recommendations" :key="r" class="chip" type="button" @click="send(r)">
                {{ r }}
              </button>
            </div>
          </div>

          <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
            <div class="bubble-msg" :class="{ warn: m.state === 'error' || m.state === 'model_unavailable' }">
              <p class="text">{{ m.content }}</p>
              <ul v-if="m.sources && m.sources.length" class="sources">
                <li v-for="(s, i) in m.sources" :key="i">{{ s }}</li>
              </ul>
              <div v-if="m.trust" class="trust-mini">
                <span>{{ m.trust.model_type_label }}</span>
                <span>{{ m.trust.period_label }}</span>
                <span>来源 {{ m.trust.source_count }} 条</span>
                <span>{{ m.trust.masked ? '已脱敏' : '未脱敏' }}</span>
              </div>
              <button v-if="m.state === 'model_unavailable'" class="mini primary" type="button" @click="goConfigure">
                前往设置 / 本地 AI 配置
              </button>
              <button v-if="m.role === 'assistant' && m.content" class="mini copy" type="button"
                      @click="copyMessage(m)">
                <component :is="copiedId === m.id ? Check : Copy" :size="12" />
                {{ copiedId === m.id ? '已复制' : '复制' }}
              </button>
              <span class="time">{{ fmtTime(m.created_at) }}</span>
            </div>
          </div>

          <div v-if="sending" class="msg assistant">
            <div class="bubble-msg thinking">
              <span class="dot" /><span class="dot" /><span class="dot" />
              <span class="tk">正在思考…</span>
            </div>
          </div>
        </div>

        <div class="ai-model">
          <span class="k">模型</span>
          <select v-model="modelType" class="model-select">
            <option value="auto">自动（本地优先）</option>
            <option value="local">仅本地 · 不出网</option>
            <option value="third_party">第三方 · 已脱敏</option>
          </select>
          <span class="model-hint">
            {{ modelType === 'third_party'
              ? '强制走第三方模型；证件号 / 卡号 / 手机号 / 车牌先脱敏，上下文是整个家庭账本'
              : modelType === 'local'
                ? '强制走本地模型，数据不出本机；上下文是整个家庭账本'
                : '数据查询用确定性统计，分析类问题交给模型（本地优先，失败切第三方）' }}
          </span>
        </div>
        <div class="ai-status">
          <span class="state-dot" :class="providerState.local_ai?.status || 'unknown'" />
          <span>本地 {{ providerState.local_ai?.status_label || '检测中' }}</span>
          <span class="sep">·</span>
          <span class="state-dot" :class="providerState.third_party_ai?.status || 'unknown'" />
          <span>第三方 {{ providerState.third_party_ai?.status_label || '检测中' }}</span>
          <span v-if="providerState.local_ai?.status === 'unreachable'" class="warn-tip">
            （请确认 Ollama 已启动）
          </span>
        </div>

        <div class="ai-context">
          <span class="k">上下文</span>
          <span class="v">{{ contextLine }}</span>
          <button class="mini" type="button" @click="clearContext">清除</button>
        </div>

        <footer class="ai-foot">
          <textarea
            v-model="question" class="ai-input" rows="1"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            @keydown.enter.exact.prevent="send()"
          />
          <button class="send" type="button" :disabled="sending || !question.trim()" @click="send()">
            <Send :size="15" />
          </button>
        </footer>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ai-mask { position: fixed; inset: 0; z-index: 1250; }
.ai-panel {
  position: fixed;
  top: 0; right: 0; bottom: 0;
  z-index: 1260;
  width: min(var(--ai-panel-width), 100vw);
  display: flex;
  flex-direction: column;
  background: var(--glass-3);
  border-left: var(--glass-border-line);
  box-shadow: var(--glass-shadow-heavy), var(--glass-inner-highlight);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
}
.ai-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border);
}
.t { display: flex; align-items: center; gap: 8px; color: var(--accent-ai); }
.t h3 { font-size: 15px; }
.head-actions { display: flex; align-items: center; gap: 4px; }
.hbtn {
  width: 30px; height: 30px; display: grid; place-items: center;
  border: none; border-radius: 8px; background: transparent;
  color: var(--ink-muted); cursor: pointer;
}
.hbtn:hover { background: var(--glass-1); color: var(--ink-strong); }

.history { flex: 1; overflow: auto; padding: var(--space-4) var(--space-5); }
.history-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-3); font-size: 12.5px; color: var(--ink-muted); }
.back { display: inline-flex; align-items: center; gap: 4px; border: none; background: transparent; color: var(--ink-strong); font-size: 13px; cursor: pointer; }
.empty { font-size: 13px; color: var(--ink-muted); }
.conv-list { display: flex; flex-direction: column; gap: 6px; }
.conv-list li { display: flex; align-items: center; gap: 6px; border: 1px solid var(--border); border-radius: 10px; background: var(--glass-1); }
.conv-list li.on { border-color: var(--accent-ai); }
.conv-main { flex: 1; text-align: left; padding: 10px 12px; border: none; background: transparent; cursor: pointer; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.conv-main .ct { font-size: 13.5px; color: var(--ink-strong); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conv-main small { font-size: 11.5px; color: var(--ink-muted); }
.del { flex: none; width: 32px; height: 32px; margin-right: 6px; display: grid; place-items: center; border: none; border-radius: 8px; background: transparent; color: var(--ink-muted); cursor: pointer; }
.del:hover { color: var(--danger); background: var(--glass-1); }

.ai-body { flex: 1; overflow: auto; padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-3); }
.welcome { display: flex; flex-direction: column; gap: var(--space-4); }
.bubble {
  display: block; padding: 10px 12px; border-radius: 12px;
  background: var(--glass-1); border: 1px solid var(--border);
  font-size: 13.5px; color: var(--ink);
}
.reco { display: flex; flex-direction: column; gap: 6px; }
.reco-title { font-size: 12px; color: var(--ink-muted); }
.chip {
  text-align: left; padding: 9px 12px; border-radius: 10px;
  border: 1px solid var(--border); background: var(--glass-1);
  color: var(--ink); font-size: 13px; cursor: pointer;
}
.chip:hover { border-color: var(--accent-ai); color: var(--accent-ai); }

.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.msg.assistant { justify-content: flex-start; }
.bubble-msg {
  max-width: 88%; padding: 10px 12px; border-radius: 12px;
  border: 1px solid var(--border); background: var(--glass-1);
  font-size: 13.5px; color: var(--ink-strong); position: relative;
}
.msg.user .bubble-msg {
  background: linear-gradient(135deg, var(--primary), var(--accent-ai));
  border-color: transparent; color: #fff;
}
.bubble-msg.warn { border-color: rgba(244, 166, 42, 0.5); }
.bubble-msg .text { white-space: pre-wrap; }
.sources { margin-top: 6px; list-style: disc; padding-left: 16px; font-size: 12px; color: var(--ink-muted); }
.msg.user .sources { color: rgba(255, 255, 255, 0.85); }
.trust-mini { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
.trust-mini span { font-size: 11px; color: var(--ink-muted); border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; }
.bubble-msg .time { display: block; margin-top: 6px; font-size: 10.5px; color: var(--ink-muted); }
.msg.user .bubble-msg .time { color: rgba(255, 255, 255, 0.75); }
.mini {
  height: 26px; padding: 0 10px; margin-top: 8px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--glass-1);
  color: var(--ink); font-size: 12px; cursor: pointer;
}
.mini.primary { border-color: transparent; color: #fff; background: linear-gradient(135deg, var(--accent-ai), var(--primary)); }
.mini.copy { display: inline-flex; align-items: center; gap: 4px; margin-left: 6px; }
.ai-model {
  display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap;
  padding: var(--space-2) var(--space-5); border-top: 1px solid var(--border); font-size: 12px;
}
.ai-model .k { color: var(--ink-muted); flex: none; }
.model-select {
  height: 28px; padding: 0 8px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--glass-1); color: var(--ink);
  font-size: 12px; cursor: pointer;
}
.model-hint { color: var(--ink-muted); flex: 1; min-width: 0; }
.ai-status {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 0 var(--space-5) var(--space-2); font-size: 11.5px; color: var(--ink-muted);
}
.ai-status .sep { opacity: 0.5; }
.state-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-muted); }
.state-dot.connected { background: #22a06b; }
.state-dot.unreachable, .state-dot.failed { background: #d64545; }
.state-dot.disabled, .state-dot.unconfigured { background: #b0b7c3; }
.warn-tip { color: #d64545; }
.thinking { display: inline-flex; align-items: center; gap: 5px; color: var(--ink-muted); }
.thinking .tk { font-size: 12.5px; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-ai); animation: blink 1.2s infinite both; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }

.ai-context {
  display: flex; align-items: center; gap: var(--space-2);
  padding: var(--space-2) var(--space-5);
  border-top: 1px solid var(--border); font-size: 12px;
}
.ai-context .k { color: var(--ink-muted); flex: none; }
.ai-context .v { color: var(--ink-strong); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
.ai-foot {
  display: flex; gap: var(--space-2); align-items: flex-end;
  padding: var(--space-3) var(--space-5);
  padding-bottom: calc(var(--space-3) + var(--safe-bottom));
  border-top: 1px solid var(--border);
}
.ai-input {
  flex: 1; min-height: 38px; max-height: 120px; padding: 9px 12px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border); background: var(--glass-1);
  color: var(--ink-strong); font-size: 14px; font-family: inherit;
  resize: none; line-height: 1.4;
}
.ai-input:focus { outline: none; border-color: var(--accent-ai); box-shadow: 0 0 0 3px rgba(124, 92, 252, 0.18); }
.send {
  flex: none; width: 40px; height: 38px; display: grid; place-items: center;
  border: none; border-radius: var(--glass-radius-control);
  color: #fff; cursor: pointer;
  background: linear-gradient(135deg, var(--accent-ai), var(--primary));
}
.send:disabled { opacity: 0.5; cursor: not-allowed; }
@media (max-width: 768px) {
  .ai-panel { width: 100%; }
}
</style>
