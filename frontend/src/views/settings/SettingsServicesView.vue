<script setup lang="ts">
/**
 * 第三方服务（/settings/services）：OCR、邮件记账、本地 AI、第三方 AI 的统一配置入口。
 *
 * 以前这四个各占一个路由、散在设置首页的卡片里，换 Key 要挨个页面找；
 * 现在集中成一个页签，内部用分段控件切换。旧路由保留重定向，书签不会失效。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/ui/PageHeader.vue'
import SettingsOcrView from './SettingsOcrView.vue'
import SettingsMailInboxView from './SettingsMailInboxView.vue'
import SettingsLocalAiView from './SettingsLocalAiView.vue'
import SettingsAiProvidersView from './SettingsAiProvidersView.vue'

const TABS = [
  { key: 'ocr', label: '百度 OCR', hint: '票据文字识别' },
  { key: 'mail', label: '邮件记账', hint: '信用卡消费入账' },
  { key: 'local-ai', label: '本地 AI', hint: 'Ollama 本地模型' },
  { key: 'ai', label: '第三方 AI', hint: '云端接口与 Key' },
] as const

type TabKey = (typeof TABS)[number]['key']

const COMPONENTS = {
  ocr: SettingsOcrView,
  mail: SettingsMailInboxView,
  'local-ai': SettingsLocalAiView,
  ai: SettingsAiProvidersView,
} as const

const route = useRoute()
const router = useRouter()

function normalize(value: unknown): TabKey {
  const key = String(value || '')
  return TABS.some((t) => t.key === key) ? (key as TabKey) : 'ocr'
}

const tab = ref<TabKey>(normalize(route.query.tab))
const activeComponent = computed(() => COMPONENTS[tab.value])

// 刷新后保持当前页签；用 replace 避免每次切换都塞一条历史记录
watch(tab, (value) => {
  if (route.query.tab !== value) router.replace({ query: { ...route.query, tab: value } })
})
// 从旧入口重定向过来（/settings/ocr 等）时，跟随 query 切到对应页签
watch(
  () => route.query.tab,
  (value) => {
    const next = normalize(value)
    if (next !== tab.value) tab.value = next
  },
)
</script>

<template>
  <div class="services">
    <PageHeader
      title="第三方服务"
      subtitle="所有需要填 Key / 地址的外部能力都在这里：OCR、邮件记账、本地 AI、第三方 AI 接口。"
    />

    <div class="tabs" role="tablist">
      <button
        v-for="item in TABS"
        :key="item.key"
        type="button"
        role="tab"
        class="tab"
        :class="{ 'is-active': tab === item.key }"
        :aria-selected="tab === item.key"
        @click="tab = item.key"
      >
        <span class="t-label">{{ item.label }}</span>
        <span class="t-hint">{{ item.hint }}</span>
      </button>
    </div>

    <component :is="activeComponent" embedded />
  </div>
</template>

<style scoped>
.services { display: flex; flex-direction: column; gap: var(--space-4); }

.tabs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: var(--glass-radius-card);
  background: var(--glass-2);
  box-shadow: var(--glass-inner-highlight);
}
.tab {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 9px 12px;
  border: none;
  border-radius: 12px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background var(--motion-fast) var(--ease-out), color var(--motion-fast) var(--ease-out);
}
.tab .t-label { font-size: 13.5px; font-weight: 600; color: var(--ink); }
.tab .t-hint { font-size: 11.5px; color: var(--ink-muted); }
.tab:hover { background: rgba(53, 109, 255, 0.08); }
.tab.is-active {
  background: linear-gradient(135deg, rgba(53, 109, 255, 0.16), rgba(124, 92, 252, 0.14));
  box-shadow: inset 0 0 0 1px rgba(53, 109, 255, 0.26);
}
.tab.is-active .t-label { color: var(--ink-strong); }
.tab:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }

@media (max-width: 640px) {
  .tabs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .tab { padding: 8px 10px; }
  .tab .t-hint { display: none; }
}
</style>
