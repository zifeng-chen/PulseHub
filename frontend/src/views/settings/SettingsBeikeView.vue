<script setup lang="ts">
/** 贝壳开放平台 /settings/beike：AppKey/AppSecret（OAuth2.0）、默认城市、启停、真实连接测试。 */
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

interface Beike {
  app_key_masked: string
  app_secret_masked: string
  base_url: string
  default_city: string | null
  default_city_code: string | null
  enabled: boolean
  adapter: { status: string; status_label: string; detail: string }
  note: string
}

const auth = useAuthStore()
const toast = useToastStore()
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const testing = ref(false)
const data = ref<Beike | null>(null)
const form = ref({
  app_key: '', app_secret: '', base_url: 'https://gw-open.ke.com',
  default_city: '', default_city_code: '', enabled: false,
})

const canConfigure = () => auth.can('configure')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Beike>('/settings/beike')
    form.value = {
      app_key: '', app_secret: '',
      base_url: data.value.base_url || 'https://gw-open.ke.com',
      default_city: data.value.default_city || '',
      default_city_code: data.value.default_city_code || '',
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
      base_url: form.value.base_url,
      default_city: form.value.default_city,
      default_city_code: form.value.default_city_code,
      enabled: form.value.enabled,
    }
    if (form.value.app_key) body.app_key = form.value.app_key
    if (form.value.app_secret) body.app_secret = form.value.app_secret
    await api.put('/settings/beike', body)
    toast.success('贝壳开放平台配置已保存（密钥加密存储）')
    form.value.app_key = ''
    form.value.app_secret = ''
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
    const res = await api.post<{ status_label: string; detail: string }>('/settings/beike/test')
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
  <div class="beike">
    <PageHeader
      title="房产数据（贝壳）"
      subtitle="接入贝壳开放平台，用真实成交与楼盘数据做房产估价；不可用时不影响手工估值。"
    >
      <a class="official-link" href="https://open.ke.com/" target="_blank" rel="noopener noreferrer">
        <ExternalLink :size="14" /> 贝壳开放平台
      </a>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="开放平台凭证">
        <div class="adapter-row">
          <div>
            <strong>贝壳开放平台 · 壳e估</strong>
            <p class="detail">{{ data.adapter.detail }}</p>
          </div>
          <StatusBadge
            :label="data.adapter.status_label"
            :tone="data.adapter.status === 'connected' ? 'success' : data.adapter.status === 'failed' ? 'danger' : data.adapter.status === 'untested' ? 'warning' : 'neutral'"
          />
        </div>
        <div class="grid">
          <AppField label="AppKey（client_id）" :hint="data.app_key_masked ? `当前：${data.app_key_masked}` : '在贝壳开放平台控制台创建应用后获取'">
            <AppInput v-model="form.app_key" sensitive placeholder="留空表示不修改" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="AppSecret（client_secret）" :hint="data.app_secret_masked ? `当前：${data.app_secret_masked}` : '只存密文，页面只显示掩码'">
            <AppInput v-model="form.app_secret" sensitive placeholder="留空表示不修改" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="网关地址" hint="默认 https://gw-open.ke.com">
            <AppInput v-model="form.base_url" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="启用">
            <AppSwitch v-model="form.enabled" label="启用贝壳估价" :disabled="!canConfigure()" />
          </AppField>
        </div>
        <div class="foot">
          <AppButton variant="secondary" :loading="testing" @click="test">
            <Plug :size="15" /> 测试连接
          </AppButton>
          <AppButton v-if="canConfigure()" variant="primary" :loading="saving" @click="save">
            <Save :size="15" /> 保存
          </AppButton>
        </div>
      </AppCard>

      <AppCard title="估价默认参数">
        <div class="grid">
          <AppField label="默认城市" hint="如：北京市 / 上海市 / 深圳市">
            <AppInput v-model="form.default_city" placeholder="北京市" :disabled="!canConfigure()" />
          </AppField>
          <AppField label="行政区划代码" hint="国家标准代码，如北京 110000、上海 310000">
            <AppInput v-model="form.default_city_code" placeholder="110000" :disabled="!canConfigure()" />
          </AppField>
        </div>
        <p class="note">
          在「资产 → 不动产详情」点「贝壳估价」，会带上地址与建筑面积调用
          <code>POST /api/assessPrice</code>；估价结果只做参考，需你确认后才写入资产价值。
        </p>
      </AppCard>

      <AppCard title="接口与数据说明">
        <ul class="facts">
          <li>鉴权：OAuth2.0（client_credentials），先取 <code>/oauth/token</code>，再带 <code>access_token</code> 调业务接口。</li>
          <li>估价：<code>POST /api/assessPrice</code>，返回单价 <code>avgPrice</code>、总价 <code>priceForHouse</code>、价格维度与预警。</li>
          <li>该能力属于贝壳企业服务，需账号开通「壳e估」权限；未开通时接口会返回业务错误，状态会如实记为失败。</li>
        </ul>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.beike { display: flex; flex-direction: column; gap: var(--space-4); }
.adapter-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-4); }
.adapter-row strong { font-size: 14px; color: var(--ink-strong); }
.detail { font-size: 12.5px; color: var(--ink-muted); margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.foot { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; margin-top: var(--space-3); }
.facts { margin: 0; padding-left: 18px; font-size: 13px; color: var(--ink); line-height: 2; }
code { font-size: 12px; padding: 1px 5px; border-radius: 4px; background: var(--glass-2); }
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
