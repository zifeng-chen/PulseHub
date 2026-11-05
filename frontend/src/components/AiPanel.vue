<script setup lang="ts">
/**
 * 全局 AI 聊天面板（05 §3、09 §3）。
 * 消息流 + 历史对话 + 新建对话：发送后立即显示、思考中状态、可滚动查看历史。
 * 会话持久化到后端；模型未连接时保留确定性回答，并给出配置入口。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Sparkles, X, Send, Plus, History, Trash2, ChevronLeft, Copy, Check, RotateCw, Pencil, ArrowDown, PictureInPicture2 } from 'lucide-vue-next'
import { useUiStore } from '@/stores/ui'
import { AI_TIMEOUT, api, errorMessage } from '@/api/client'
import { renderMarkdown } from '@/utils/markdown'

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
/** 助手回复按 Markdown 渲染（用户消息保持纯文本，避免 `*`/`_` 被误解析） */
function md(text: string) {
  return renderMarkdown(text)
}
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
/** 找某条助手消息前面最近的一条用户提问（用于「重新生成」） */
function precedingUser(m: Msg): Msg | undefined {
  const idx = messages.value.findIndex((x) => x.id === m.id)
  for (let i = idx - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') return messages.value[i]
  }
  return undefined
}
/** 重新生成：把这条回答对应的提问再发一次，得到一份新答案（追加在末尾） */
function regenerate(m: Msg) {
  const u = precedingUser(m)
  if (u?.content) send(u.content)
}
/** 编辑：把这条消息的内容带回输入框，光标聚焦，可直接改完再发 */
function editMessage(m: Msg) {
  question.value = m.content || ''
  nextTick(() => {
    inputRef.value?.focus()
    autoGrow()
  })
}
const question = ref('')
const sending = ref(false)
/** 等待秒数：本地模型一次要几十秒，不给计时会让人以为卡死 */
const elapsed = ref(0)
let elapsedTimer: ReturnType<typeof setInterval> | undefined

function startElapsed() {
  elapsed.value = 0
  stopElapsed()
  elapsedTimer = setInterval(() => { elapsed.value += 1 }, 1000)
}
function stopElapsed() {
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = undefined
}
const historyOpen = ref(false)
const bodyRef = ref<HTMLElement | null>(null)

/** 悬浮模式：缩小为右下角浮窗，不遮罩主界面，可边操作边对话（持久化） */
const floatMode = ref(localStorage.getItem('pulsehub.ai.float') === '1')
function toggleFloat() {
  floatMode.value = !floatMode.value
  localStorage.setItem('pulsehub.ai.float', floatMode.value ? '1' : '0')
}
/** 悬浮窗拖动：未拖动前用 CSS 右下角定位；首次拖动时按当前矩形切成 left/top 坐标 */
const panelRef = ref<HTMLElement | null>(null)
const floatPos = ref<{ x: number; y: number } | null>(null)
const dragging = ref(false)
let dragState = { px: 0, py: 0, x: 0, y: 0 }
function clamp(v: number, lo: number, hi: number) {
  return Math.min(Math.max(v, lo), Math.max(lo, hi))
}
function onDragStart(e: PointerEvent) {
  if (!floatMode.value) return
  const t = e.target as HTMLElement
  if (t.closest('button, select, textarea, input, a')) return
  const el = panelRef.value
  if (!el) return
  if (!floatPos.value) {
    const r = el.getBoundingClientRect()
    floatPos.value = { x: r.left, y: r.top }
  }
  dragging.value = true
  dragState = { px: e.clientX, py: e.clientY, x: floatPos.value.x, y: floatPos.value.y }
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  document.body.style.userSelect = 'none'
}
function onDragMove(e: PointerEvent) {
  if (!dragging.value) return
  floatPos.value = {
    x: clamp(dragState.x + (e.clientX - dragState.px), 0, window.innerWidth - 80),
    y: clamp(dragState.y + (e.clientY - dragState.py), 0, window.innerHeight - 48),
  }
}
function onDragEnd() {
  dragging.value = false
  document.body.style.userSelect = ''
}
/** 是否已滚动到底部（决定「回到底部」按钮显隐） */
const atBottom = ref(true)
function onBodyScroll() {
  const el = bodyRef.value
  if (!el) return
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
function scrollDown() {
  const el = bodyRef.value
  if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
}

/** 输入框自动增高：随内容撑到最高 120px，再高就内部滚动 */
const inputRef = ref<HTMLTextAreaElement | null>(null)
function autoGrow() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 120)}px`
}
/** 中文输入法组词期间按回车不应发送，否则拼音还没上屏就发出去一句半截话 */
const composing = ref(false)
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !composing.value) {
    e.preventDefault()
    send()
  }
}

/** 展示用的短标签（页面名 / 分析名） */
const contextLabel = computed(() => ui.aiPanelContext || '未指定页面上下文')
/**
 * 真正发给模型的上下文正文。
 *
 * 之前只把页面名当上下文发过去，从「接着问 AI 助手」进来时模型完全不知道
 * 刚才分析了什么，用户得把结论再复述一遍。现在这里可以承载整段解读 + 关键指标。
 */
const sessionContext = ref('')
const recommendations = [
  '净资产为什么变化？',
  '本月消费增加在哪里？',
  '下月还款压力如何？',
  '哪些租金逾期了？',
]

async function scrollToBottom(smooth = false) {
  await nextTick()
  const el = bodyRef.value
  if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  atBottom.value = true
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
    // 历史对话用它自己的上下文，别被当前页面的标签覆盖掉
    sessionContext.value = res.conversation.context || contextLabel.value
    historyOpen.value = false
    await scrollToBottom()
  } catch (err) {
    console.error(errorMessage(err))
  }
}

function newConversation(context?: string) {
  currentId.value = ''
  messages.value = []
  historyOpen.value = false
  sessionContext.value = context ?? (ui.aiPanelBrief || contextLabel.value)
}

/**
 * 每次「打开」都重新对齐上下文：
 * - 从「接着问 AI 助手」进来（fresh）→ 开新对话并带上刚生成的分析，
 *   否则会接在上一次无关聊天后面，等于没有上下文；
 * - 普通打开且当前不在对话中 → 跟随入口刷新上下文标签。
 */
watch(
  () => ui.aiPanelNonce,
  () => {
    if (ui.aiPanelFresh) {
      newConversation(ui.aiPanelBrief || contextLabel.value)
    } else if (!currentId.value) {
      sessionContext.value = ui.aiPanelBrief || contextLabel.value
    }
  },
  { immediate: true },
)

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
  nextTick(autoGrow)
  const tmpId = `tmp-user-${Date.now()}`
  messages.value.push({ id: tmpId, role: 'user', content: q, created_at: new Date().toISOString() })
  sending.value = true
  startElapsed()
  await scrollToBottom()
  try {
    const res = await api.post<{ conversation: Conv; user_message: Msg; assistant_message: Msg }>(
      '/ai/messages',
      {
        conversation_id: currentId.value || null,
        question: q,
        context: sessionContext.value,
        model_type: modelType.value,
      },
      undefined,
      // 本地模型 30~60 秒是常态，用全局 30 秒会先超时（见 client.ts 的 AI_TIMEOUT）
      { timeout: AI_TIMEOUT },
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
    stopElapsed()
    await scrollToBottom()
  }
}

function goConfigure() {
  ui.closeAiPanel()
  router.push('/settings/local-ai')
}

function clearContext() {
  ui.aiPanelContext = ''
  ui.aiPanelBrief = ''
  sessionContext.value = ''
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
      // 默认打开最近一次对话（「接着问」fresh 场景由 nonce watcher 新开，跳过）
      if (!ui.aiPanelFresh && conversations.value.length
        && currentId.value !== conversations.value[0].id) {
        await openConversation(conversations.value[0].id)
      }
      await scrollToBottom()
    }
  },
)
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="ui.aiPanelOpen && !floatMode" class="ai-mask" :style="{ background: 'var(--scrim)' }" @click.self="ui.closeAiPanel()" />
    </Transition>
    <Transition name="slide">
      <aside v-if="ui.aiPanelOpen" ref="panelRef" class="ai-panel" :class="{ float: floatMode }"
             :style="floatMode && floatPos
               ? { left: floatPos.x + 'px', top: floatPos.y + 'px', right: 'auto', bottom: 'auto' }
               : undefined"
             aria-label="AI 助手">
        <header class="ai-head" :class="{ draggable: floatMode }"
                @pointerdown="onDragStart" @pointermove="onDragMove"
                @pointerup="onDragEnd" @pointercancel="onDragEnd">
          <div class="t">
            <Sparkles :size="16" />
            <h3>AI 助手</h3>
            <select v-model="modelType" class="model-select" title="切换模型">
              <option value="auto">自动</option>
              <option value="local">仅本地</option>
              <option value="third_party">第三方</option>
            </select>
          </div>
          <div class="head-actions">
            <button class="hbtn" type="button" :title="floatMode ? '切回侧栏模式' : '悬浮模式'"
                    :aria-label="floatMode ? '切回侧栏模式' : '悬浮模式'" @click="toggleFloat">
              <PictureInPicture2 :size="15" />
            </button>
            <button class="hbtn" type="button" title="新对话" aria-label="新对话" @click="newConversation()">
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
        <div v-else ref="bodyRef" class="ai-body" @scroll="onBodyScroll">
          <!-- 上下文直接放在对话里，不单独占用底部一栏 -->
          <div v-if="sessionContext" class="ctx-card">
            <div class="ctx-head">
              <span class="ctx-k">上下文</span>
              <button class="mini ctx-del" type="button" @click="clearContext">清除</button>
            </div>
            <div class="ctx-text">{{ sessionContext }}</div>
          </div>

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
              <p v-if="m.role === 'user'" class="text">{{ m.content }}</p>
              <div v-else class="md" v-html="md(m.content)" />
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
              <div v-if="m.content" class="msg-foot">
                <span class="time">{{ fmtTime(m.created_at) }}</span>
                <span class="msg-tools">
                  <button class="tool" type="button"
                          :title="m.role === 'assistant' ? '重新生成' : '重新发送'"
                          @click="m.role === 'assistant' ? regenerate(m) : send(m.content)">
                    <RotateCw :size="13" />
                  </button>
                  <button class="tool" type="button" title="复制" @click="copyMessage(m)">
                    <component :is="copiedId === m.id ? Check : Copy" :size="13" />
                  </button>
                  <button class="tool" type="button" title="编辑" @click="editMessage(m)">
                    <Pencil :size="13" />
                  </button>
                </span>
              </div>
            </div>
          </div>

          <div v-if="sending" class="msg assistant">
            <div class="bubble-msg thinking">
              <span class="dot" /><span class="dot" /><span class="dot" />
              <span class="tk">
                正在思考…<template v-if="elapsed >= 5">{{ elapsed }}s</template>
                <template v-if="elapsed >= 20">（本地模型较慢，请稍候）</template>
              </span>
            </div>
          </div>

          <!-- 向上滚动后出现的「回到底部」按钮 -->
          <Transition name="fade">
            <button v-if="!atBottom" class="scroll-bottom" type="button" title="回到底部" @click="scrollDown">
              <ArrowDown :size="16" />
            </button>
          </Transition>
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

        <footer class="ai-foot">
          <textarea
            ref="inputRef" v-model="question" class="ai-input" rows="1"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            @input="autoGrow"
            @keydown="onKeydown"
            @compositionstart="composing = true"
            @compositionend="composing = false"
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

.history { flex: 1; overflow-y: auto; overflow-x: hidden; padding: var(--space-4) var(--space-5); }
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

.ai-body { flex: 1; overflow-y: auto; overflow-x: hidden; padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-3); }
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
  overflow-wrap: anywhere; min-width: 0;
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
.bubble-msg .time { font-size: 10.5px; color: var(--ink-muted); }
.msg.user .bubble-msg .time { color: rgba(255, 255, 255, 0.75); }
.mini {
  height: 26px; padding: 0 10px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--glass-1);
  color: var(--ink); font-size: 12px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 4px;
}
.mini:hover { border-color: var(--accent-ai); color: var(--accent-ai); }
.mini.primary { margin-top: 8px; border-color: transparent; color: #fff; background: linear-gradient(135deg, var(--accent-ai), var(--primary)); }
/* 消息底部：时间在前，后面跟一排小图标（重新发送 / 复制 / 编辑） */
.msg-foot { margin-top: 6px; display: flex; align-items: center; gap: 8px; }
.msg-tools { display: inline-flex; align-items: center; gap: 2px; }
.tool {
  width: 20px; height: 20px; display: grid; place-items: center;
  border: none; border-radius: 6px; background: transparent;
  color: var(--ink-muted); cursor: pointer;
}
.tool:hover { background: var(--glass-2); color: var(--accent-ai); }
.msg.user .tool { color: rgba(255, 255, 255, 0.7); }
.msg.user .tool:hover { background: rgba(255, 255, 255, 0.18); color: #fff; }

/* Markdown 渲染：标题 / 列表 / 代码 / 表格 / 引用 / 链接（按主题取色） */
.md { line-height: 1.65; word-break: break-word; overflow-wrap: anywhere; max-width: 100%; }
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p { margin: 0 0 8px; }
.md h1, .md h2, .md h3, .md h4 { margin: 14px 0 6px; font-weight: 600; line-height: 1.3; color: var(--ink-strong); }
.md h1 { font-size: 16px; }
.md h2 { font-size: 15px; }
.md h3 { font-size: 14px; }
.md h4 { font-size: 13.5px; }
.md ul, .md ol { margin: 0 0 8px; padding-left: 20px; }
.md li { margin: 2px 0; }
.md code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px; background: var(--glass-2); padding: 1px 5px; border-radius: 5px;
}
.md pre { margin: 0 0 10px; padding: 10px 12px; border-radius: 10px; background: var(--glass-2); border: 1px solid var(--border); overflow: auto; }
.md pre code { background: transparent; padding: 0; }
.md blockquote { margin: 0 0 8px; padding: 2px 0 2px 12px; border-left: 3px solid var(--accent-ai); color: var(--ink-muted); }
.md a { color: var(--accent-ai); text-decoration: underline; }
.md table { border-collapse: collapse; margin: 0 0 10px; font-size: 12.5px; width: 100%; display: block; max-width: 100%; overflow-x: auto; }
.md th, .md td { border: 1px solid var(--border); padding: 5px 8px; text-align: left; }
.md th { background: var(--glass-1); }
.md hr { border: none; border-top: 1px solid var(--border); margin: 10px 0; }
.md strong { font-weight: 600; }
/* 模型切换已挪到标题栏，这里只保留选择器外观 */
.model-select {
  height: 26px; padding: 0 6px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--glass-1); color: var(--ink);
  font-size: 12px; cursor: pointer;
}
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

/* 上下文卡片：直接嵌在对话流顶部，随消息一起滚动 */
.ctx-card {
  flex: none; border: 1px solid var(--border); border-radius: 12px;
  background: var(--glass-1); padding: 10px 12px;
}
.ctx-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
.ctx-k { font-size: 11px; color: var(--ink-muted); letter-spacing: 0.5px; }
.ctx-del { height: 22px; padding: 0 8px; margin-top: 0; font-size: 11px; }
.ctx-text {
  font-size: 12.5px; line-height: 1.6; color: var(--ink-muted);
  white-space: pre-wrap; word-break: break-word;
  max-height: 120px; overflow: auto;
}
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

/* 滚动条加深：全局用 --border 太浅，这里换成更明显的灰 */
.ai-body::-webkit-scrollbar-thumb,
.history::-webkit-scrollbar-thumb {
  background: var(--ink-muted);
  border-radius: 8px;
  border: 2px solid transparent;
  background-clip: padding-box;
}
.ai-body::-webkit-scrollbar-thumb:hover,
.history::-webkit-scrollbar-thumb:hover { background: var(--ink); }

/* 向上滚动后出现的「回到底部」悬浮按钮 */
.scroll-bottom {
  position: sticky; bottom: 12px; align-self: center;
  width: 34px; height: 34px; border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--panel-solid, var(--glass-3));
  color: var(--ink-strong); cursor: pointer;
  display: grid; place-items: center;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.18);
  margin-top: -4px;
}
.scroll-bottom:hover { color: var(--accent-ai); border-color: var(--accent-ai); }

/* 悬浮模式：缩小为右下角浮窗，不遮罩主界面 */
.ai-panel.float {
  top: auto; bottom: 20px; left: auto; right: 20px;
  height: auto; max-height: min(72vh, 720px);
  width: min(400px, calc(100vw - 32px));
  border: 1px solid var(--border);
  border-radius: 18px;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.28), var(--glass-inner-highlight);
}
.ai-panel.float .ai-body { max-height: none; }
/* 悬浮时可拖动：按住标题栏空白处移动（按钮/下拉仍可正常点按） */
.ai-panel.float .ai-head { cursor: move; touch-action: none; }
.ai-panel.float .ai-head.draggable { user-select: none; }
@media (max-width: 768px) {
  .ai-panel { width: 100%; }
  .ai-panel.float { width: calc(100vw - 24px); right: 12px; bottom: 12px; }
}
</style>
