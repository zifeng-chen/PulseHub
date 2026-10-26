<script setup lang="ts">
/**
 * 根组件。
 * 应用壳只在平台路由（带 menu 元数据）下渲染；登录/初始化等系统入口使用独立布局。
 * 一级菜单选中状态由 AppShell 依据当前路由计算（05 §1、21 §2）。
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import AiAnalysisDialog from '@/components/AiAnalysisDialog.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import WelcomeOverlay from '@/components/WelcomeOverlay.vue'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useSystemStore } from '@/stores/system'
import { applyRefreshRateHint } from '@/composables/useRefreshRate'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const auth = useAuthStore()
const theme = useThemeStore()
const system = useSystemStore()

const useShell = computed(() => Boolean(route.meta?.menu))

/** 首屏欢迎层延后出现：先让脉枢台渲染出来，避免刚登录时被全屏遮罩挡住误以为没跳转 */
const welcomeDelayPassed = ref(false)
watch(
  () => [auth.isAuthenticated, route.path] as const,
  ([authed, path]) => {
    if (authed && path === '/dashboard' && !auth.welcomeDismissed) {
      window.setTimeout(() => { welcomeDelayPassed.value = true }, 800)
    }
  },
  { immediate: true },
)

const showWelcome = computed(
  () => welcomeDelayPassed.value && auth.isAuthenticated && route.path === '/dashboard'
    && !auth.welcomeDismissed,
)

function onKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null
  const typing = target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
  if (typing) return

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    if (ui.aiPanelOpen) ui.closeAiPanel()
    else ui.openAiPanel((route.meta?.title as string) || '')
    return
  }
  if (e.altKey && ['1', '2', '3', '4'].includes(e.key)) {
    e.preventDefault()
    const homes = ['/dashboard', '/ledger', '/insights', '/settings']
    router.push(homes[Number(e.key) - 1])
  }
}

onMounted(async () => {
  // 探测实际刷新率并标记低帧设备（不阻塞首屏）
  void applyRefreshRateHint()
  theme.init()
  document.addEventListener('keydown', onKeydown)
  await system.loadHealth()
  await system.loadDictionary()
  if (auth.isAuthenticated) {
    // 会话恢复已由路由守卫的 loadMe 完成，这里不再重复调用（避免重复轮换 refresh token，B-3）
    await theme.loadFromServer()
  }
})

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="app-root">
    <div class="app-backdrop" aria-hidden="true" />
    <AppShell v-if="useShell" />
    <RouterView v-else />
    <WelcomeOverlay :open="showWelcome" @close="() => {}" />
    <AiAnalysisDialog />
    <ConfirmDialog />
    <ToastHost />
  </div>
</template>

<style scoped>
.app-root { min-height: 100vh; position: relative; }
</style>
