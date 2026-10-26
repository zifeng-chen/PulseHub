<script setup lang="ts">
/**
 * 提醒中心 /ledger/reminders（08 §5/§12、19 §5.7）。
 * 提醒由确定性查询实时计算；到点会由后台按规则自动发送，页面上的发送按钮用于手动补发。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { BellRing, Send, RefreshCw, ArrowRight, AlertTriangle } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useUiStore } from '@/stores/ui'
import { formatAmount } from '@/utils/format'
import { useToastStore } from '@/stores/toast'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface ReminderItem {
  key: string
  type: string
  type_label: string
  title: string
  due_date: string | null
  days_left: number | null
  overdue: boolean
  amount: string | null
  detail_to: string
  object_type: string
  object_id: string
  /** AI 建议字段（type === 'ai_suggestion'） */
  severity?: 'critical' | 'warning' | 'info'
  reason?: string
  suggestion?: string
}
interface ReminderGroup {
  type: string
  type_label: string
  count: number
  items: ReminderItem[]
}
interface RemindersData {
  groups: ReminderGroup[]
  total: number
  near_days: number
  data_updated_at: string
  note: string
}
interface RecordItem {
  id: string
  reminder_type: string | null
  title: string
  content: string
  delivery_status: string
  delivery_status_label: string
  error: string | null
  sent_at: string | null
  retry_count: number
}

const router = useRouter()
const ui = useUiStore()
const toast = useToastStore()

const loading = ref(true)
const error = ref('')
const data = ref<RemindersData | null>(null)
const records = ref<RecordItem[]>([])
const sending = ref(false)
const selected = ref<Set<string>>(new Set())

const groupsWithItems = computed(() => (data.value?.groups || []).filter((g) => g.count > 0))
const totalSelected = computed(() => selected.value.size)

function fmtDate(v: string | null) {
  return v ? new Date(v).toLocaleDateString('zh-CN') : '—'
}

function daysText(item: ReminderItem) {
  if (item.days_left === null) return '—'
  if (item.days_left < 0) return `已逾期 ${-item.days_left} 天`
  if (item.days_left === 0) return '今天到期'
  return `还有 ${item.days_left} 天`
}

function severityLabel(s?: string) {
  if (s === 'critical') return '需关注'
  if (s === 'warning') return '提醒'
  return '建议'
}

function toggle(item: ReminderItem) {
  const s = new Set(selected.value)
  if (s.has(item.key)) s.delete(item.key)
  else s.add(item.key)
  selected.value = s
}

function toggleAll() {
  const all = groupsWithItems.value.flatMap((g) => g.items).map((i) => i.key)
  if (selected.value.size === all.length) selected.value = new Set()
  else selected.value = new Set(all)
}

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const [r1, r2] = await Promise.all([
      api.get<RemindersData>('/ledger/reminders'),
      api.get<{ items: RecordItem[] }>('/ledger/notifications/records'),
    ])
    if (seq !== loadSeq) return
    data.value = r1
    records.value = r2.items
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function sendSelected() {
  if (!totalSelected.value || sending.value) return
  const { ok } = await ui.askConfirm({
    title: '发送提醒通知',
    message: `将同步发送 ${totalSelected.value} 条提醒到 PushPlus，结果立即写入通知记录。`,
    confirmText: '确认发送',
    danger: false,
  })
  if (!ok) return
  sending.value = true
  try {
    const r = await api.post<{ total: number; sent: number; failed: number }>('/ledger/reminders/send', {
      keys: Array.from(selected.value),
    })
    toast.success(`发送完成：${r.sent} 条成功，${r.failed} 条失败`)
    selected.value = new Set()
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    sending.value = false
  }
}

async function retry(id: string) {
  try {
    await api.post(`/ledger/notifications/records/${id}/retry`)
    toast.success('已重试')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

onMounted(load)
</script>

<template>
  <div class="reminders">
    <PageHeader
      title="提醒中心"
      subtitle="到期提醒会按设置自动发送（提前天数 / 每天次数 / 每周期次数）；这里的发送按钮用于手动补发。"
      :breadcrumb="['家庭台账', '提醒中心']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="primary" :disabled="!totalSelected || sending" :loading="sending" @click="sendSelected">
        <Send :size="15" /> 手动补发所选（{{ totalSelected }}）
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <p v-if="data.total === 0" class="all-clear">
        <BellRing :size="15" /> 当前没有待处理的提醒，一切正常。
      </p>

      <section v-if="groupsWithItems.length" class="groups">
        <AppCard v-for="g in groupsWithItems" :key="g.type" :title="`${g.type_label}（${g.count}）`">
          <ul class="items">
            <li v-for="it in g.items" :key="it.key" class="item">
              <label class="check">
                <input type="checkbox" :checked="selected.has(it.key)" @change="toggle(it)" />
              </label>
              <div class="i-main">
                <strong>{{ it.title }}</strong>
                <small v-if="g.type === 'ai_suggestion'">
                  {{ it.reason }}<template v-if="it.suggestion"> · {{ it.suggestion }}</template>
                </small>
                <small v-else>
                  {{ daysText(it) }}<template v-if="it.due_date"> · {{ fmtDate(it.due_date) }}</template>
                  <template v-if="it.amount"> · {{ formatAmount(it.amount, ui.amountPrecision) }}</template>
                </small>
              </div>
              <StatusBadge
                v-if="g.type === 'ai_suggestion'"
                :label="severityLabel(it.severity)"
                :tone="it.severity === 'critical' ? 'danger' : it.severity === 'warning' ? 'warning' : 'neutral'"
              />
              <StatusBadge
                v-else
                :label="it.overdue ? '已逾期' : it.days_left === 0 ? '今天' : '临近'"
                :tone="it.overdue ? 'danger' : it.days_left === 0 ? 'warning' : 'neutral'"
              />
              <button class="go" type="button" @click="router.push(it.detail_to)">查看</button>
            </li>
          </ul>
        </AppCard>
      </section>

      <AppCard title="通知发送记录">
        <StateBlock v-if="!records.length" state="empty" title="暂无发送记录"
                    description="发送提醒后，这里会显示发送结果（成功/失败/重试）。" />
        <ul v-else class="records">
          <li v-for="r in records" :key="r.id">
            <div class="r-main">
              <strong>{{ r.title }}</strong>
              <small>{{ r.content }} · 重试 {{ r.retry_count }} 次 · {{ fmtDate(r.sent_at) }}</small>
              <small v-if="r.error" class="err">{{ r.error }}</small>
            </div>
            <StatusBadge
              :label="r.delivery_status_label"
              :tone="r.delivery_status === 'sent' ? 'success' : r.delivery_status === 'failed' ? 'danger' : 'neutral'"
            />
            <button v-if="r.delivery_status === 'failed'" class="retry" type="button" @click="retry(r.id)">
              重试
            </button>
          </li>
        </ul>
      </AppCard>

      <p class="note">{{ data.note }}</p>
    </template>
  </div>
</template>

<style scoped>
.reminders { display: flex; flex-direction: column; gap: var(--space-4); }
.all-clear { display: flex; align-items: center; gap: 8px; color: var(--success); font-size: 13.5px; }
.groups { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: var(--space-4); }
.items, .records { display: flex; flex-direction: column; gap: 8px; }
.item, .records li {
  display: flex; align-items: center; gap: var(--space-3);
  padding: 10px 12px; border: 1px solid var(--border);
  border-radius: var(--glass-radius-control); background: var(--glass-1);
}
.check input { accent-color: var(--primary); width: 16px; height: 16px; cursor: pointer; }
.i-main, .r-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.i-main strong, .r-main strong { font-size: 13.5px; color: var(--ink-strong); }
.i-main small, .r-main small { font-size: 11.5px; color: var(--ink-muted); }
.r-main .err { color: var(--danger); }
.go, .retry { font-size: 12.5px; color: var(--primary); background: none; border: none; cursor: pointer; padding: 0; white-space: nowrap; }
.go:hover, .retry:hover { text-decoration: underline; }
.note { font-size: 12px; color: var(--ink-muted); }
</style>
