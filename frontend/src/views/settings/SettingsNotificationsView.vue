<script setup lang="ts">
/** 提醒通知 /settings/notifications（08 §5、§12）：PushPlus、提醒类型开关、测试发送与通知记录。 */
import { onMounted, ref } from 'vue'
import { Plug, Save, ExternalLink } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Notifications {
  channel_type: string
  channel_label: string
  token_masked: string
  enabled: boolean
  last_sent_at: string | null
  last_status: string
  adapter: { status: string; status_label: string; detail: string }
  reminder_types: { key: string; label: string; enabled: boolean }[]
  /** 自动发送规则（后台每 30 分钟按此判断） */
  auto: {
    enabled: boolean
    days_before: number
    times_per_day: number
    max_per_period: number
    hours: number[]
  }
  note: string
}

const auth = useAuthStore()
const toast = useToastStore()
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const testing = ref(false)
const data = ref<Notifications | null>(null)
const form = ref({ token: '', enabled: false })
const auto = ref({ enabled: true, days_before: 3, times_per_day: 1, max_per_period: 3, hoursText: '9' })
const savingAuto = ref(false)

const canConfigure = () => auth.can('configure')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Notifications>('/settings/notifications')
    form.value = { token: '', enabled: data.value.enabled }
    const a = data.value.auto
    auto.value = {
      enabled: a?.enabled ?? true,
      days_before: a?.days_before ?? 3,
      times_per_day: a?.times_per_day ?? 1,
      max_per_period: a?.max_per_period ?? 3,
      hoursText: (a?.hours?.length ? a.hours : [9]).join(','),
    }
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function saveAuto() {
  savingAuto.value = true
  try {
    const hours = auto.value.hoursText
      .split(/[，,\s]+/).filter(Boolean)
      .map((h) => Number(h))
      .filter((h) => Number.isFinite(h) && h >= 0 && h <= 23)
    await api.put('/settings/notifications/auto', {
      enabled: auto.value.enabled,
      days_before: Number(auto.value.days_before),
      times_per_day: Number(auto.value.times_per_day),
      max_per_period: Number(auto.value.max_per_period),
      hours: hours.length ? hours : [9],
    })
    toast.success('自动提醒规则已保存，后台会按新规则发送')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    savingAuto.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const body: Record<string, unknown> = { enabled: form.value.enabled }
    if (form.value.token) body.token = form.value.token
    await api.put('/settings/notifications', body)
    toast.success('通知渠道配置已保存（Token 加密存储、掩码显示）')
    form.value.token = ''
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  try {
    const res = await api.post<{ status_label: string; detail: string }>('/ai/providers/notification/test')
    toast.success(`${res.status_label}：${res.detail}`)
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="notifications">
    <PageHeader title="提醒通知" subtitle="到期提醒会自动发送；这里配置提前天数、每天次数、每周期次数与发送时段。">
      <a class="official-link" :href="'https://www.pushplus.plus/'" target="_blank" rel="noopener noreferrer">
        <ExternalLink :size="14" /> PushPlus 官网
      </a>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="通知渠道">
        <div class="adapter-row">
          <div>
            <strong>{{ data.channel_label }}</strong>
            <p class="detail">{{ data.adapter.detail }}</p>
          </div>
          <StatusBadge :label="data.adapter.status_label" :tone="data.adapter.status === 'connected' ? 'success' : data.adapter.status === 'failed' ? 'danger' : data.adapter.status === 'untested' ? 'warning' : 'neutral'" />
        </div>
        <div class="grid">
          <AppField label="PushPlus Token" :hint="data.token_masked ? `当前：${data.token_masked}` : '尚未配置'">
            <AppInput v-model="form.token" sensitive placeholder="留空表示不修改现有 Token" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="启用">
            <AppSwitch v-model="form.enabled" label="启用 PushPlus 通知" :disabled="!canConfigure()" />
          </AppField>
        </div>
        <div class="foot">
          <AppButton variant="secondary" :loading="testing" @click="test"><Plug :size="15" /> 测试发送</AppButton>
          <AppButton v-if="canConfigure()" variant="primary" :loading="saving" @click="save"><Save :size="15" /> 保存</AppButton>
        </div>
        <p class="note">
          最近发送：{{ data.last_sent_at ? new Date(data.last_sent_at).toLocaleString('zh-CN') : '暂无' }}。
          通知失败不影响台账内提醒，用户可在提醒页面重试。
        </p>
      </AppCard>

      <AppCard title="提醒类型开关">
        <ul class="types">
          <li v-for="t in data.reminder_types" :key="t.key">
            <span>{{ t.label }}</span>
            <StatusBadge :label="t.enabled ? '已启用' : '未启用'" :tone="t.enabled ? 'success' : 'neutral'" />
          </li>
        </ul>
        <p class="note">{{ data.note }}</p>
      </AppCard>

      <AppCard title="自动发送规则" level="1">
        <p class="note">
          提醒默认自动发送：后台每 30 分钟检查一次，命中下列规则就推送，不需要再到提醒页手动点发送。
          提醒页仍保留手动补发，用于「漏了想立刻再发一条」。
        </p>
        <div class="auto-grid">
          <AppField label="自动发送">
            <AppSwitch v-model="auto.enabled" label="到期前自动推送" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="提前几天开始提醒" hint="0 表示只在与到期日当天提醒">
            <AppInput v-model="auto.days_before" type="number" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="每天最多几次" hint="同时是全天总量上限，避免反复轰炸">
            <AppInput v-model="auto.times_per_day" type="number" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="一个周期内最多几次" hint="周期 = 到期日往前 N 天到到期日">
            <AppInput v-model="auto.max_per_period" type="number" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="发送时段（小时）" hint="用逗号分隔，例如 9,20 表示上午 9 点与晚上 8 点各检查一次">
            <AppInput v-model="auto.hoursText" placeholder="9,20" :disabled="!canConfigure()" />
          </AppField>
        </div>
        <AppButton v-if="canConfigure()" variant="primary" :loading="savingAuto" @click="saveAuto">
          <Save :size="15" /> 保存自动规则
        </AppButton>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.auto-grid {
  display: grid; gap: var(--space-3);
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  max-width: 720px; margin-bottom: var(--space-3);
}
.notifications { display: flex; flex-direction: column; gap: var(--space-4); }
.adapter-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-4); }
.adapter-row strong { font-size: 14px; color: var(--ink-strong); }
.detail { font-size: 12.5px; color: var(--ink-muted); margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.foot { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }
.types { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--space-3); }
.types li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); }
.types span { font-size: 13.5px; color: var(--ink-strong); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; margin-top: var(--space-2); }
.official-link {
  display: inline-flex; align-items: center; gap: 5px;
  height: 34px; padding: 0 12px;
  border: 1px solid var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1); color: var(--ink-strong);
  font-size: 13px; text-decoration: none; white-space: nowrap;
  transition: border-color var(--motion-fast) var(--ease-out), color var(--motion-fast) var(--ease-out);
}
.official-link:hover { border-color: var(--primary); color: var(--primary); }
</style>
