<script setup lang="ts">
/** 安装检测 /setup/check（04 §2）：不需要登录，展示环境检测结果与修复建议。 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ShieldCheck, RefreshCw, ArrowRight } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import AppButton from '@/components/ui/AppButton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import StateBlock from '@/components/ui/StateBlock.vue'

interface CheckItem {
  key: string
  label: string
  status: 'pass' | 'warn' | 'fail'
  detail: string
  suggestion: string | null
}

const router = useRouter()
const loading = ref(true)
const failed = ref('')
const overall = ref<'pass' | 'fail'>('pass')
const items = ref<CheckItem[]>([])
const appVersion = ref('')
const initialized = ref(false)

const statusMap: Record<string, { label: string; tone: 'success' | 'warning' | 'danger' }> = {
  pass: { label: '通过', tone: 'success' },
  warn: { label: '警告', tone: 'warning' },
  fail: { label: '失败', tone: 'danger' },
}

async function load() {
  loading.value = true
  failed.value = ''
  try {
    const data = await api.get<{
      initialized: boolean
      overall: 'pass' | 'fail'
      checks: CheckItem[]
      app_version: string
    }>('/setup/check')
    items.value = data.checks
    overall.value = data.overall
    appVersion.value = data.app_version
    initialized.value = data.initialized
  } catch (err) {
    failed.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function start() {
  router.push(initialized.value ? '/login' : '/setup')
}

onMounted(load)
</script>

<template>
  <div class="setup-check">
    <div class="brand-block">
      <img src="/logo.png" alt="财资脉枢" class="logo" />
      <div>
        <h1>财资脉枢</h1>
        <p class="tag">安装与服务检查 · 本地优先部署</p>
      </div>
    </div>

    <section class="card glass">
      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="failed" state="error" :description="failed" retryable @retry="load" />
      <template v-else>
        <header class="head">
          <h2>系统检查结果</h2>
          <StatusBadge
            :label="overall === 'pass' ? '全部通过' : '存在失败项'"
            :tone="overall === 'pass' ? 'success' : 'danger'"
          />
        </header>
        <p class="version">系统版本：{{ appVersion }} · 当前状态：{{ initialized ? '已完成初始化' : '尚未初始化' }}</p>

        <ul class="checks">
          <li v-for="c in items" :key="c.key">
            <div class="row">
              <span class="lbl">{{ c.label }}</span>
              <StatusBadge :label="statusMap[c.status].label" :tone="statusMap[c.status].tone" />
            </div>
            <p class="detail">{{ c.detail }}</p>
            <p v-if="c.suggestion" class="suggestion">修复建议：{{ c.suggestion }}</p>
          </li>
        </ul>

        <div class="actions">
          <AppButton variant="secondary" @click="load">
            <RefreshCw :size="15" /> 重新检测
          </AppButton>
          <AppButton variant="primary" :disabled="overall !== 'pass'" @click="start">
            <ShieldCheck :size="15" />
            {{ initialized ? '前往登录' : '开始初始化' }}
            <ArrowRight :size="15" />
          </AppButton>
        </div>
        <p v-if="overall !== 'pass'" class="block-hint">
          存在未通过项时不能继续初始化，请按修复建议处理后重新检测。
        </p>
      </template>
    </section>
  </div>
</template>

<style scoped>
.setup-check {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-6);
  padding: calc(var(--space-7) + var(--safe-top)) var(--space-4) var(--space-7);
}
.brand-block { display: flex; align-items: center; gap: var(--space-4); }
.logo {
  width: 52px; height: 52px; object-fit: contain;
  filter: drop-shadow(0 10px 24px rgba(53, 109, 255, 0.34));
}
.brand-block h1 { font-size: 20px; }
.tag { font-size: 13px; color: var(--ink-muted); }

.card { width: min(720px, 100%); padding: var(--space-5); border-radius: var(--glass-radius-panel); }
.head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.version { font-size: 12.5px; color: var(--ink-muted); margin-top: 4px; }
.checks { margin-top: var(--space-5); display: flex; flex-direction: column; gap: var(--space-3); }
.checks li {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--glass-radius-card);
  background: var(--glass-1);
}
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.lbl { font-size: 14px; font-weight: 500; color: var(--ink-strong); }
.detail { font-size: 12.5px; color: var(--ink-muted); margin-top: 4px; word-break: break-all; }
.suggestion { font-size: 12.5px; color: var(--warning); margin-top: 4px; }
.actions { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-5); flex-wrap: wrap; }
.block-hint { font-size: 12.5px; color: var(--danger); margin-top: var(--space-2); text-align: right; }
</style>
