<script setup lang="ts">
/** 隐私与安全 /settings/privacy（08 §9）：脱敏、数据留存、云端外发总开关（默认关闭）。 */
import { onMounted, ref } from 'vue'
import { Save } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Privacy {
  file_retention_days: number
  ai_session_retention_days: number
  mask_sensitive: boolean
  cloud_egress_enabled: boolean
  external_call_audit: boolean
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const saving = ref(false)
const form = ref<Privacy>({
  file_retention_days: 180,
  ai_session_retention_days: 90,
  mask_sensitive: true,
  cloud_egress_enabled: false,
  external_call_audit: true,
})

const canConfigure = () => auth.can('configure')

async function load() {
  loading.value = true
  error.value = ''
  try {
    form.value = await api.get<Privacy>('/settings/privacy')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function toggleEgress(next: boolean) {
  if (!next) {
    // 关闭总开关：后端只有收到 cloud_egress_enabled 字段才会处理（settings.py 的
    // `if "cloud_egress_enabled" in payload`）。之前 save() 的 body 不含该字段，
    // 导致关闭形同虚设、load() 后开关弹回「开」。这里单独发一次关闭请求。
    saving.value = true
    try {
      await api.put('/settings/privacy', { cloud_egress_enabled: false })
      form.value.cloud_egress_enabled = false
      toast.success('云端外发总开关已关闭')
      await load()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      saving.value = false
    }
    return
  }
  const res = await ui.askConfirm({
    title: '确认开启云端外发总开关',
    message: '开启后，在显式启用第三方 AI 接口且外发预览通过时，必要的脱敏字段可能被发送到外部服务。',
    detail: [
      '外发字段预览与脱敏结果会写入审计。',
      '默认关闭；该开关是高影响动作，需要二次确认与原因。',
      '本地 AI 失败时不会静默切换第三方。',
    ],
    confirmText: '确认开启',
    danger: true,
    requireReason: true,
    reasonLabel: '开启原因（必填）',
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'privacy.cloud_egress', object_type: 'privacy', object_id: null,
    })
    await api.put('/settings/privacy', {
      cloud_egress_enabled: true,
      confirm_token: tokenRes.token,
      reason: res.reason,
    })
    toast.success('云端外发总开关已开启，操作已写入审计')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function save() {
  saving.value = true
  try {
    await api.put('/settings/privacy', {
      mask_sensitive: form.value.mask_sensitive,
      external_call_audit: form.value.external_call_audit,
      file_retention_days: Number(form.value.file_retention_days),
      ai_session_retention_days: Number(form.value.ai_session_retention_days),
    })
    toast.success('隐私与安全设置已更新')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="privacy">
    <PageHeader title="隐私与安全" subtitle="云端外发总开关默认为关闭；打开时必须二次确认并展示外发字段。" />

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else>
      <AppCard title="数据留存">
        <div class="grid">
          <AppField label="本地文件保留时间（天）" hint="未确认的智能录入材料与失败 OCR 材料按此期限保留">
            <AppInput v-model="form.file_retention_days" type="number" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="AI 会话保留时间（天）">
            <AppInput v-model="form.ai_session_retention_days" type="number" :disabled="!canConfigure()" />
          </AppField>
        </div>
      </AppCard>

      <AppCard title="脱敏与外部调用">
        <div class="stack">
          <div class="opt-row">
            <div>
              <strong>敏感字段脱敏</strong>
              <p>证件号、VIN、车牌、户号、Token 等在界面与日志中只显示掩码。</p>
            </div>
            <AppSwitch v-model="form.mask_sensitive" :disabled="!canConfigure()" />
          </div>
          <div class="opt-row">
            <div>
              <strong>外部调用审计</strong>
              <p>记录提供商、外发范围与响应状态。</p>
            </div>
            <AppSwitch v-model="form.external_call_audit" :disabled="!canConfigure()" />
          </div>
          <div class="opt-row danger-row">
            <div>
              <strong>云端外发总开关</strong>
              <p>默认关闭。打开后第三方 AI 才可能被调用；打开需要二次确认并展示外发字段。</p>
            </div>
            <AppSwitch
              :model-value="form.cloud_egress_enabled"
              :disabled="!canConfigure()"
              label="允许云端外发"
              @update:model-value="toggleEgress"
            />
          </div>
        </div>
        <div v-if="canConfigure()" class="foot">
          <AppButton variant="primary" :loading="saving" @click="save"><Save :size="15" /> 保存</AppButton>
        </div>
      </AppCard>

      <AppCard title="密钥管理" level="1">
        <p class="note">
          百度 OCR、第三方 AI、PushPlus 与数据库凭证不得明文写入前端、数据库普通字段、日志、错误堆栈或备份展示。
          统一使用主加密密钥加密保存；主密钥只从受保护环境变量或部署密钥服务读取，不写入代码仓库。
        </p>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.privacy { display: flex; flex-direction: column; gap: var(--space-4); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.stack { display: flex; flex-direction: column; gap: var(--space-2); }
.opt-row {
  display: flex; align-items: center; justify-content: space-between; gap: var(--space-4);
  padding: var(--space-3) var(--space-4); border: 1px solid var(--border);
  border-radius: var(--glass-radius-card); background: var(--glass-1);
}
.opt-row.danger-row { border-color: rgba(232, 93, 117, 0.34); }
.opt-row strong { font-size: 13.5px; color: var(--ink-strong); }
.opt-row p { font-size: 12.5px; color: var(--ink-muted); }
.foot { display: flex; justify-content: flex-end; margin-top: var(--space-4); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; }
@media (max-width: 700px) { .opt-row { flex-direction: column; align-items: flex-start; } }
</style>
