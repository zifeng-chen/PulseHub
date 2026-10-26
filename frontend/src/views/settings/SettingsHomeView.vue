<script setup lang="ts">
/** 设置首页 /settings（08 §1）：配置完整度、状态卡与入口；不在首页直接编辑敏感凭证。 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import ThemeToggle from '@/components/ui/ThemeToggle.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Overview {
  completeness: number
  household: { name: string; base_currency: string; timezone: string } | null
  admin: { display_name: string; username: string; role_label: string }
  providers: Record<string, { status: string; status_label: string; detail: string }>
  category_count: number
  theme: string
  cards: { key: string; title: string; route: string; value?: string; status?: string }[]
}

const router = useRouter()
const loading = ref(true)
const error = ref('')
const data = ref<Overview | null>(null)

function tone(status?: string) {
  if (!status) return 'neutral'
  if (['已连接', '已保存'].includes(status)) return 'success'
  if (['未配置'].includes(status)) return 'neutral'
  if (['测试失败'].includes(status)) return 'danger'
  return 'warning'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Overview>('/settings/overview')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="settings-home">
    <PageHeader title="设置" subtitle="基座阶段已包含主题、中文界面、统一弹窗、删除二次确认与详细审计。">
      <ThemeToggle />
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <!-- 这个百分比只统计「外部能力」（OCR / 通知 / AI），不含家庭、币种、分类；
           标题必须写明，否则用户看到 0% 会以为整个系统没配好。 -->
      <AppCard title="外部能力配置" level="3" aurora>
        <div class="completeness">
          <div class="bar"><span :style="{ width: `${data.completeness}%` }" /></div>
          <p class="pct">{{ data.completeness }}%</p>
        </div>
        <p class="hint">仅统计百度 OCR、提醒通知、本地 AI、第三方 AI 四项外部能力的配置情况；家庭信息与分类不影响此比例。</p>
        <ul class="meta">
          <li><span class="k">家庭</span><span class="v">{{ data.household?.name || '—' }}</span></li>
          <li><span class="k">基础币种 / 时区</span><span class="v">{{ data.household?.base_currency }} / {{ data.household?.timezone }}</span></li>
          <li><span class="k">当前用户</span><span class="v">{{ data.admin.display_name }}（{{ data.admin.role_label }}）</span></li>
          <li><span class="k">分类数量</span><span class="v">{{ data.category_count }} 个</span></li>
        </ul>
      </AppCard>

      <section class="providers">
        <AppCard v-for="(p, key) in data.providers" :key="key" :title="p.status_label ? key : key" level="1">
          <template #header><h3>{{ { local_ai: '本地 AI', ocr: '百度 OCR', notification: '提醒通知', third_party_ai: '第三方 AI 接口' }[key as 'local_ai'] || key }}</h3></template>
          <StatusBadge :label="p.status_label" :tone="tone(p.status_label) as never" />
          <p class="pdetail">{{ p.detail }}</p>
        </AppCard>
      </section>

      <section class="entries">
        <button v-for="c in data.cards" :key="c.key" class="entry" type="button" @click="router.push(c.route)">
          <div class="e-top">
            <strong>{{ c.title }}</strong>
            <StatusBadge v-if="c.status" :label="c.status" :tone="tone(c.status) as never" />
            <span v-else-if="c.value" class="e-value">{{ c.value }}</span>
          </div>
          <span class="e-route">{{ c.route }}</span>
        </button>
      </section>
    </template>
  </div>
</template>

<style scoped>
.hint { margin-top: 6px; font-size: 12px; line-height: 1.55; color: var(--ink-muted); }
.settings-home { display: flex; flex-direction: column; gap: var(--space-4); }
.completeness { display: flex; align-items: center; gap: var(--space-4); }
.bar { flex: 1; height: 8px; border-radius: 6px; background: var(--border); overflow: hidden; }
.bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--primary), var(--accent-ai)); transition: width var(--motion-base) var(--ease-out); }
.pct { font-size: 16px; font-weight: 600; color: var(--ink-strong); }
.meta { margin-top: var(--space-4); display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: var(--space-3); }
@media (max-width: 640px) {
  .meta { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; }
  .meta .v { font-size: 12.5px; }
  .entries { grid-template-columns: 1fr; }
  .entry { padding: 12px 13px; }
}
.meta li { display: flex; flex-direction: column; gap: 2px; }
.meta .k { font-size: 12px; color: var(--ink-muted); }
.meta .v { font-size: 13.5px; color: var(--ink-strong); }
.providers { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.pdetail { margin-top: var(--space-2); font-size: 12.5px; color: var(--ink-muted); }
.entries { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: var(--space-3); }
.entry {
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start; text-align: left;
  padding: var(--space-4); border: 1px solid var(--border);
  border-radius: var(--glass-radius-card); background: var(--glass-2); cursor: pointer;
  box-shadow: var(--glass-inner-highlight);
}
.entry:hover { border-color: var(--primary); }
.e-top { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); width: 100%; }
.e-top strong { font-size: 14px; color: var(--ink-strong); }
.e-value { font-size: 12.5px; color: var(--ink-muted); }
.e-route { font-size: 11.5px; color: var(--ink-muted); }
</style>
