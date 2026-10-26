<script setup lang="ts">
/**
 * 第三方 AI 接口 /settings/ai-providers（08 §8、14 §5）。
 * 默认全部关闭；启用需要二次确认 + 云端外发总开关允许；调用前展示外发字段预览并写入审计。
 */
import { onMounted, reactive, ref } from 'vue'
import { Save, Trash2, Eye, Plug, Plus } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Provider {
  key: string
  id: string
  name: string
  interface_type: string
  base_url: string | null
  api_key_masked: string
  default_model: string | null
  fallback_model: string | null
  enabled: boolean
  priority: number
  last_status: string
  allow_attachment: boolean
  allow_sensitive: boolean
  timeout: number
  stream: boolean
}

interface EgressPreview {
  provider: string
  fields: { field: string; label: string; desensitized: boolean }[]
  excluded: string[]
  enabled: boolean
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const items = ref<Provider[]>([])
const previewOpen = ref(false)
const preview = ref<EgressPreview | null>(null)
const previewTarget = ref<Provider | null>(null)
const editing = ref<Provider | null>(null)
const saving = ref(false)

/** 新增提供商（此前只有列表/编辑/删除，没有办法新建，导致永远「尚未配置」） */
const creating = ref(false)
const createErrors = ref<Record<string, string>>({})
const createForm = reactive({
  name: '',
  interface_type: 'openai_compatible',
  base_url: '',
  api_key: '',
  default_model: '',
  priority: 100,
  timeout: 60,
})

function openCreate() {
  Object.assign(createForm, {
    name: '', interface_type: 'openai_compatible', base_url: '',
    api_key: '', default_model: '', priority: 100, timeout: 60,
  })
  createErrors.value = {}
  creating.value = true
}

async function saveCreate() {
  saving.value = true
  createErrors.value = {}
  try {
    await api.post('/settings/ai-providers', {
      name: createForm.name,
      interface_type: createForm.interface_type,
      base_url: createForm.base_url,
      api_key: createForm.api_key,
      default_model: createForm.default_model,
      priority: Number(createForm.priority) || 100,
      timeout: Number(createForm.timeout) || 60,
    })
    toast.success('已新增提供商（默认关闭）；建议先「连接测试」，再启用')
    creating.value = false
    await load()
  } catch (err) {
    const detail = (err as { fieldErrors?: Record<string, string> })?.fieldErrors
    if (detail) createErrors.value = detail
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

const canConfigure = () => auth.can('configure')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get<{ items: Provider[] }>('/settings/ai-providers')
    items.value = res.items
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function toggleEnable(p: Provider) {
  if (!p.enabled) {
    const res = await ui.askConfirm({
      title: '确认启用第三方 AI 接口',
      message: `启用后，在本地 AI 无法回答时可能将必要字段发送到第三方服务「${p.name}」。`,
      detail: [
        '启用前必须确认云端外发总开关已打开（设置 / 隐私与安全）。',
        '外发字段会先展示预览，并写入调用审计。',
        '本地 AI 失败时不会静默切换第三方。',
      ],
      confirmText: '确认启用',
      danger: true,
      requireReason: true,
      reasonLabel: '启用原因（必填）',
    })
    if (!res.ok) return
    try {
      const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
        action: 'provider.enable', object_type: 'ai_provider', object_id: p.id,
      })
      await api.put(`/settings/ai-providers/${p.id}`, {
        enabled: true, confirm_token: tokenRes.token, reason: res.reason,
      })
      toast.success('第三方 AI 已启用，调用将写入审计')
      await load()
    } catch (err) {
      toast.error(errorMessage(err))
    }
    return
  }
  try {
    await api.put(`/settings/ai-providers/${p.id}`, { enabled: false })
    toast.info('第三方 AI 已关闭')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function openPreview(p: Provider) {
  previewTarget.value = p
  previewOpen.value = true
  try {
    preview.value = await api.get<EgressPreview>(`/settings/ai-providers/${p.id}/egress-preview`)
  } catch (err) {
    toast.error(errorMessage(err))
    preview.value = null
  }
}

async function saveProvider() {
  if (!editing.value) return
  saving.value = true
  try {
    await api.put(`/settings/ai-providers/${editing.value.id}`, {
      priority: Number(editing.value.priority),
      default_model: editing.value.default_model,
    })
    toast.success('提供商配置已更新')
    editing.value = null
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function revoke(p: Provider) {
  const res = await ui.askConfirm({
    title: '确认撤销第三方 AI 配置',
    message: `将移除提供商「${p.name}」的全部配置，包括加密保存的 API Key。`,
    detail: ['撤销后该提供商不再可被调用。', '此操作会写入审计。'],
    confirmText: '确认撤销',
    danger: true,
    irreversible: true,
    requireReason: true,
    reasonLabel: '撤销原因（必填）',
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'provider.delete', object_type: 'ai_provider', object_id: p.id,
    })
    await api.delete(`/settings/ai-providers/${p.id}`, { confirm_token: tokenRes.token, reason: res.reason })
    toast.success('第三方 AI 配置已撤销')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function testProvider(p: Provider) {
  try {
    const res = await api.post<{ status_label: string; detail: string }>(`/ai/providers/${p.key}/test`)
    toast.success(`${res.status_label}：${res.detail}`)
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

onMounted(load)
</script>

<template>
  <div class="providers">
    <PageHeader title="第三方 AI 接口" subtitle="与本地 AI 分开管理；默认全部关闭，Key 加密保存并掩码显示。">
      <AppButton v-if="canConfigure()" variant="primary" @click="openCreate">
        <Plus :size="15" /> 新增提供商
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else>
      <AppCard title="提供商列表">
        <StateBlock
          v-if="!items.length"
          state="empty"
          title="尚未配置第三方 AI 接口"
          description="点击右上角「新增提供商」添加 OpenAI 兼容服务（如 DeepSeek、阿里百炼、企业内部网关）。默认全部关闭。"
          hint="保存后先做「连接测试」，通过再启用；启用需要开启隐私设置里的云端外发总开关。"
        />
        <ul v-else class="list">
          <li v-for="p in items" :key="p.id">
            <div class="p-head">
              <div>
                <strong>{{ p.name }}</strong>
                <p class="meta">
                  {{ p.interface_type }} · {{ p.base_url || '未填写 Base URL' }} · 模型 {{ p.default_model || '未指定' }}
                </p>
                <p class="meta">API Key：{{ p.api_key_masked || '未配置' }} · 优先级 {{ p.priority }}</p>
              </div>
              <StatusBadge :label="p.enabled ? '已启用' : '已关闭'" :tone="p.enabled ? 'warning' : 'neutral'" />
            </div>
            <div class="p-actions">
              <button class="act" type="button" @click="openPreview(p)"><Eye :size="13" /> 外发预览</button>
              <button class="act" type="button" @click="testProvider(p)"><Plug :size="13" /> 连接测试</button>
              <button v-if="canConfigure()" class="act" type="button" @click="editing = { ...p }">编辑</button>
              <button v-if="canConfigure()" class="act" type="button" @click="toggleEnable(p)">
                {{ p.enabled ? '关闭' : '启用' }}
              </button>
              <button v-if="canConfigure()" class="act danger" type="button" @click="revoke(p)">
                <Trash2 :size="13" /> 撤销
              </button>
            </div>
          </li>
        </ul>
      </AppCard>

      <AppCard title="调用规则" level="1">
        <ul class="rules">
          <li>本地 AI 优先；仅当用户显式启用第三方 AI、隐私策略允许、外发预览通过并写入审计后才可调用。</li>
          <li>本地 AI 失败时不能静默切换第三方 API。</li>
          <li>API Key 加密保存并掩码显示，不能写入日志。</li>
          <li>可查看错误、外发字段预览、调用审计，并一键撤销配置。</li>
        </ul>
      </AppCard>
    </template>

    <AppDrawer :open="previewOpen" title="外发字段预览" @close="previewOpen = false">
      <p class="note">调用第三方 AI 前必须展示外发字段与脱敏范围。</p>
      <ul v-if="preview" class="fields">
        <li v-for="f in preview.fields" :key="f.field">
          <span>{{ f.label }}</span>
          <StatusBadge :label="f.desensitized ? '已脱敏' : '原文' " :tone="f.desensitized ? 'success' : 'warning'" />
        </li>
      </ul>
      <div v-if="preview" class="excluded">
        <p class="note">永不外发：</p>
        <ul>
          <li v-for="e in preview.excluded" :key="e">{{ e }}</li>
        </ul>
      </div>
    </AppDrawer>

    <AppDrawer :open="creating" title="新增第三方 AI 提供商" @close="creating = false">
      <div class="form">
        <AppField label="名称" required :error="createErrors.name"
                  hint="例如 DeepSeek、阿里百炼、企业内部网关">
          <AppInput v-model="createForm.name" placeholder="DeepSeek" />
        </AppField>
        <AppField label="接口类型" hint="统一按 OpenAI 兼容协议调用（/chat/completions）">
          <AppSelect v-model="createForm.interface_type" :options="[
            { value: 'openai_compatible', label: 'OpenAI 兼容接口' },
            { value: 'enterprise_compatible', label: '企业内部兼容接口' },
            { value: 'self_hosted_compatible', label: '自部署兼容服务' },
          ]" />
        </AppField>
        <AppField label="Base URL" required :error="createErrors.base_url"
                  hint="填到 /v1 为止，例如 https://api.deepseek.com/v1">
          <AppInput v-model="createForm.base_url" placeholder="https://api.deepseek.com/v1" />
        </AppField>
        <AppField label="API Key" hint="加密保存，页面只显示掩码，不写入日志">
          <AppInput v-model="createForm.api_key" type="password" placeholder="sk-..." />
        </AppField>
        <AppField label="默认模型" hint="例如 deepseek-chat、qwen-plus">
          <AppInput v-model="createForm.default_model" placeholder="deepseek-chat" />
        </AppField>
        <AppField label="优先级" hint="数字越小越优先；本地不可用时按此顺序选择">
          <AppInput v-model="createForm.priority" type="number" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="creating = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="saveCreate">保存（默认关闭）</AppButton>
      </template>
    </AppDrawer>

    <AppDrawer :open="Boolean(editing)" title="编辑提供商" @close="editing = null">
      <div v-if="editing" class="form">
        <AppField label="提供商名称"><AppInput v-model="editing.name" disabled /></AppField>
        <AppField label="接口类型"><AppInput v-model="editing.interface_type" disabled /></AppField>
        <AppField label="Base URL"><AppInput v-model="editing.base_url" disabled /></AppField>
        <AppField label="默认模型"><AppInput v-model="editing.default_model" /></AppField>
        <AppField label="备用模型"><AppInput v-model="editing.fallback_model" disabled /></AppField>
        <AppField label="调用优先级" hint="数值越小优先级越高"><AppInput v-model="editing.priority" type="number" /></AppField>
        <AppField label="是否允许附件">
          <AppSwitch v-model="editing.allow_attachment" disabled />
        </AppField>
        <AppField label="是否允许敏感字段">
          <AppSwitch v-model="editing.allow_sensitive" disabled />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editing = null">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="saveProvider"><Save :size="15" /> 保存</AppButton>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.providers { display: flex; flex-direction: column; gap: var(--space-4); }
.list { display: flex; flex-direction: column; gap: var(--space-3); }
.list li { padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); }
.p-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.p-head strong { font-size: 14px; color: var(--ink-strong); }
.meta { font-size: 12.5px; color: var(--ink-muted); margin-top: 2px; word-break: break-all; }
.p-actions { display: flex; gap: var(--space-2); margin-top: var(--space-3); flex-wrap: wrap; }
.act {
  display: inline-flex; align-items: center; gap: 4px;
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--glass-2); color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.rules { display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; color: var(--ink-muted); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; }
.fields { margin-top: var(--space-3); display: flex; flex-direction: column; gap: var(--space-2); }
.fields li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); font-size: 13px; }
.excluded { margin-top: var(--space-4); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
</style>
