<script setup lang="ts">
/** 嵌进「第三方服务」整合页时隐藏页头，避免重复标题 */
defineProps<{ embedded?: boolean }>()
/** 邮件记账 /settings/mail-inbox：IMAP 账号、加密授权码、每天拉取时段、立即拉取。 */
import { computed, onMounted, ref } from 'vue'
import { Download, Plug, Save } from 'lucide-vue-next'
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

interface MailInbox {
  email: string
  auth_code_masked: string
  imap_host: string
  imap_port: number
  fetch_times: string
  enabled: boolean
  last_fetch_at: string | null
  last_status: string
  last_status_label: string
  last_error: string | null
  sender: string
}

const auth = useAuthStore()
const toast = useToastStore()
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const testing = ref(false)
const fetching = ref(false)
const data = ref<MailInbox | null>(null)
const form = ref({
  email: '', auth_code: '', imap_host: 'imap.qq.com', imap_port: '993',
  fetch_times: '09:00,14:00,21:00', enabled: false,
})

const canConfigure = computed(() => auth.can('configure'))
const statusTone = computed(() => {
  const status = data.value?.last_status
  return status === 'ok' ? 'success' : status === 'error' ? 'danger' : 'neutral'
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<MailInbox>('/settings/mail-inbox')
    form.value = {
      email: data.value.email,
      auth_code: '',
      imap_host: data.value.imap_host,
      imap_port: String(data.value.imap_port),
      fetch_times: data.value.fetch_times,
      enabled: data.value.enabled,
    }
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      email: form.value.email,
      imap_host: form.value.imap_host,
      imap_port: Number(form.value.imap_port) || 993,
      fetch_times: form.value.fetch_times,
      enabled: form.value.enabled,
    }
    if (form.value.auth_code) body.auth_code = form.value.auth_code
    await api.put('/settings/mail-inbox', body)
    toast.success('已保存，授权码加密存储')
    form.value.auth_code = ''
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
    const res = await api.post<{ ok: boolean; sample: string }>('/settings/mail-inbox/test')
    toast.success(res.sample ? `连接成功，最近邮件：${res.sample}` : '连接成功')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    testing.value = false
  }
}

async function fetchNow() {
  fetching.value = true
  try {
    const res = await api.post<{ mails: number; created: number; refunded: number; skipped: number }>(
      '/settings/mail-inbox/fetch',
    )
    toast.success(
      `解析 ${res.mails} 封，入账 ${res.created} 笔消费、${res.refunded} 笔退款，跳过 ${res.skipped} 笔`,
    )
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    fetching.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mail">
    <PageHeader
      v-if="!embedded"
      title="邮件记账"
      subtitle="读取信用卡消费邮件并直接入账；退货会自动匹配原消费对冲，不计收入。"
    />

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="收件配置">
        <div class="adapter-row">
          <div>
            <strong>招商银行信用卡消费邮件</strong>
            <p class="detail">发件人：{{ data.sender }} · 消费明细与每月账单同一发件人</p>
          </div>
          <StatusBadge :label="data.last_status_label" :tone="statusTone" />
        </div>

        <div class="grid">
          <AppField label="邮箱地址" hint="QQ 邮箱完整地址，例如 45429588@qq.com">
            <AppInput v-model="form.email" placeholder="xxx@qq.com" :disabled="!canConfigure" />
          </AppField>
          <AppField
            label="授权码"
            :hint="data.auth_code_masked ? `当前：${data.auth_code_masked}（留空表示不修改）` : 'QQ 邮箱设置 → 账户 → 开启 IMAP → 生成授权码'"
          >
            <AppInput v-model="form.auth_code" sensitive placeholder="16 位授权码，非 QQ 密码" :disabled="!canConfigure" />
          </AppField>
          <AppField label="IMAP 服务器">
            <AppInput v-model="form.imap_host" :disabled="!canConfigure" />
          </AppField>
          <AppField label="端口">
            <AppInput v-model="form.imap_port" type="number" :disabled="!canConfigure" />
          </AppField>
          <AppField label="每天拉取时段" hint="逗号分隔，最多 3 个；到点后由后台自动拉取">
            <AppInput v-model="form.fetch_times" placeholder="09:00,14:00,21:00" :disabled="!canConfigure" />
          </AppField>
          <AppField label="启用">
            <AppSwitch v-model="form.enabled" label="启用自动拉取" :disabled="!canConfigure" />
          </AppField>
        </div>

        <div class="foot">
          <AppButton variant="secondary" :loading="testing" @click="test">
            <Plug :size="15" /> 测试连接
          </AppButton>
          <AppButton variant="secondary" :loading="fetching" @click="fetchNow">
            <Download :size="15" /> 立即拉取
          </AppButton>
          <AppButton v-if="canConfigure" variant="primary" :loading="saving" @click="save">
            <Save :size="15" /> 保存
          </AppButton>
        </div>

        <p v-if="data.last_error" class="error-note">最近错误：{{ data.last_error }}</p>
        <p class="note">
          授权码等价于邮箱读取权限，已加密存储且不回显；仅只读收信，不发送、不删除。
          消费明细直接入账；退货按「同卡 + 同金额 + 退款之前」匹配原消费，对上就生成关联退款（冲减支出），对不上则按独立退款入账。
          <template v-if="data.last_fetch_at">上次拉取：{{ data.last_fetch_at.slice(0, 16).replace('T', ' ') }}。</template>
        </p>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.mail { display: flex; flex-direction: column; gap: var(--space-4); }
.adapter-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-4); }
.adapter-row strong { font-size: 14px; color: var(--ink-strong); }
.detail { font-size: 12.5px; color: var(--ink-muted); margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.foot { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; margin-top: var(--space-2); }
.error-note { font-size: 12.5px; color: var(--danger); margin-top: var(--space-2); }
</style>
