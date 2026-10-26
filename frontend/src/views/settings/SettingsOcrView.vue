<script setup lang="ts">
/** 百度 OCR /settings/ocr（08 §6）：Key/Secret、区域、启停、测试识别；密钥掩码、不落日志。 */
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

interface Ocr {
  provider: string
  api_key_masked: string
  secret_key_masked: string
  region: string | null
  endpoint: string | null
  enabled: boolean
  adapter: { status: string; status_label: string; detail: string }
}

const auth = useAuthStore()
const toast = useToastStore()
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const testing = ref(false)
const data = ref<Ocr | null>(null)
const form = ref({ api_key: '', secret_key: '', region: 'general', enabled: false })

const canConfigure = () => auth.can('configure')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Ocr>('/settings/ocr')
    form.value = {
      api_key: '', secret_key: '',
      region: data.value.region || 'general',
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
    const body: Record<string, unknown> = { region: form.value.region, enabled: form.value.enabled }
    if (form.value.api_key) body.api_key = form.value.api_key
    if (form.value.secret_key) body.secret_key = form.value.secret_key
    await api.put('/settings/ocr', body)
    toast.success('百度 OCR 配置已保存（密钥加密存储）')
    form.value.api_key = ''
    form.value.secret_key = ''
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
    const res = await api.post<{ status_label: string; detail: string }>('/ai/providers/ocr/test')
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
  <div class="ocr">
    <PageHeader title="百度 OCR" subtitle="OCR 状态只影响智能录入，不影响手工记账；识别失败可手工录入。">
      <a class="official-link" href="https://console.bce.baidu.com/ai/#/ai/ocr/overview/index" target="_blank" rel="noopener noreferrer">
        <ExternalLink :size="14" /> 百度智能云控制台
      </a>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="凭证与接口">
        <div class="adapter-row">
          <div>
            <strong>百度智能云 OCR</strong>
            <p class="detail">{{ data.adapter.detail }}</p>
          </div>
          <StatusBadge
            :label="data.adapter.status_label"
            :tone="data.adapter.status === 'connected' ? 'success' : data.adapter.status === 'failed' ? 'danger' : data.adapter.status === 'untested' ? 'warning' : 'neutral'"
          />
        </div>
        <div class="grid">
          <AppField label="API Key" :hint="data.api_key_masked ? `当前：${data.api_key_masked}` : '尚未配置'">
            <AppInput v-model="form.api_key" sensitive placeholder="留空表示不修改" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="Secret Key" :hint="data.secret_key_masked ? `当前：${data.secret_key_masked}` : '尚未配置'">
            <AppInput v-model="form.secret_key" sensitive placeholder="留空表示不修改" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="区域 / 接口">
            <AppInput v-model="form.region" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="启用">
            <AppSwitch v-model="form.enabled" label="启用 OCR" :disabled="!canConfigure()" />
          </AppField>
        </div>
        <div class="foot">
          <AppButton variant="secondary" :loading="testing" @click="test"><Plug :size="15" /> 测试识别</AppButton>
          <AppButton v-if="canConfigure()" variant="primary" :loading="saving" @click="save"><Save :size="15" /> 保存</AppButton>
        </div>
        <p class="note">
          所有密钥掩码显示；测试材料不得默认保存为业务数据。最近调用与错误说明在调用后显示。
        </p>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.ocr { display: flex; flex-direction: column; gap: var(--space-4); }
.adapter-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-4); }
.adapter-row strong { font-size: 14px; color: var(--ink-strong); }
.detail { font-size: 12.5px; color: var(--ink-muted); margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.foot { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }
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
