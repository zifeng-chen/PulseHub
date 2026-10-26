<script setup lang="ts">
/** 本地 AI /settings/local-ai（08 §7）：协议、地址、模型、超时、上下文、流式与连接测试。 */
import { onMounted, ref } from 'vue'
import { Plug, Save } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface LocalAi {
  protocol: string
  base_url: string | null
  model: string | null
  api_key_masked: string
  timeout: number
  context_length: number
  stream: boolean
  enabled: boolean
  adapter: { status: string; status_label: string; detail: string }
  protocols: { value: string; label: string }[]
}

const auth = useAuthStore()
const toast = useToastStore()
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const testing = ref(false)
const data = ref<LocalAi | null>(null)
const form = ref({
  protocol: 'openai_compatible', base_url: '', model: '', api_key: '',
  timeout: 60, context_length: 8192, stream: true, enabled: false,
})

const canConfigure = () => auth.can('configure')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<LocalAi>('/settings/local-ai')
    form.value = {
      protocol: data.value.protocol,
      base_url: data.value.base_url || '',
      model: data.value.model || '',
      api_key: '',
      timeout: data.value.timeout,
      context_length: data.value.context_length,
      stream: data.value.stream,
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
      protocol: form.value.protocol,
      base_url: form.value.base_url,
      model: form.value.model,
      timeout: Number(form.value.timeout),
      context_length: Number(form.value.context_length),
      stream: form.value.stream,
      enabled: form.value.enabled,
    }
    if (form.value.api_key) body.api_key = form.value.api_key
    await api.put('/settings/local-ai', body)
    toast.success('本地 AI 配置已保存')
    form.value.api_key = ''
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
    const res = await api.post<{ status_label: string; detail: string }>('/ai/providers/local_ai/test')
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
  <div class="local-ai">
    <PageHeader title="本地 AI" subtitle="本地优先：默认不连接云端。模型断开不影响记账、台账、统计与历史查询。" />

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="连接状态">
        <div class="adapter-row">
          <div>
            <strong>本地模型服务</strong>
            <p class="detail">{{ data.adapter.detail }}</p>
          </div>
          <StatusBadge
            :label="data.adapter.status_label"
            :tone="data.adapter.status === 'connected' ? 'success' : data.adapter.status === 'unreachable' || data.adapter.status === 'failed' ? 'danger' : data.adapter.status === 'disabled' ? 'warning' : 'neutral'"
          />
        </div>
        <p class="note">状态是实时探测结果：未配置 / 已连接（服务可达）/ 不可达（已配置但服务无响应，请确认 Ollama 已启动）/ 已停用。探测结果缓存 15 秒。</p>
      </AppCard>

      <AppCard title="配置">
        <div class="grid">
          <AppField label="协议">
            <AppSelect v-model="form.protocol" :options="data.protocols" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="服务地址">
            <AppInput v-model="form.base_url" placeholder="http://127.0.0.1:11434/v1" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="模型名">
            <AppInput v-model="form.model" placeholder="例如：qwen2.5:7b" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="API Key（如需要）" :hint="data.api_key_masked ? `当前：${data.api_key_masked}` : '如服务无需鉴权可留空'">
            <AppInput v-model="form.api_key" sensitive :disabled="!canConfigure()" />
          </AppField>
          <AppField label="请求超时（秒）">
            <AppInput v-model="form.timeout" type="number" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="上下文长度">
            <AppInput v-model="form.context_length" type="number" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="流式输出">
            <AppSwitch v-model="form.stream" label="启用流式输出" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="启用本地 AI">
            <AppSwitch v-model="form.enabled" label="启用" :disabled="!canConfigure()" />
          </AppField>
        </div>
        <div class="foot">
          <AppButton variant="secondary" :loading="testing" @click="test"><Plug :size="15" /> 连接测试</AppButton>
          <AppButton v-if="canConfigure()" variant="primary" :loading="saving" @click="save"><Save :size="15" /> 保存</AppButton>
        </div>
      </AppCard>

      <AppCard title="AI 调用优先级" level="1">
        <p class="note">
          每次调用按"权限检查 → 确定性工具 → 本地 AI → 可选第三方 AI"顺序执行。
          第三方 AI 只有在设置中显式启用、隐私策略允许、外发预览通过并写入审计后才可调用。
          任何模型都不能绕过确定性统计工具自行计算权威金额，本地 AI 失败也不会静默切换第三方。
        </p>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.local-ai { display: flex; flex-direction: column; gap: var(--space-4); }
.adapter-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.adapter-row strong { font-size: 14px; color: var(--ink-strong); }
.detail { font-size: 12.5px; color: var(--ink-muted); margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.foot { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; margin-top: var(--space-2); }
</style>
