<script setup lang="ts">
/**
 * 应用壳（05 §1、17 §5、21 §2）。
 * - 桌面：左侧可折叠侧栏（240px / 72px）+ 顶部 64px 顶栏 + 内容最大宽 1440px。
 * - 移动：隐藏侧栏，使用四项底部导航，适配刘海与底部安全区，点击区 ≥44px。
 * - 一级菜单只有四项：脉枢台 / 家庭台账 / 脉诊 / 设置。
 * - 菜单选中状态由当前路由实时派生，绝不默认选中第一项。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bell, ChartLine, CircleHelp, Cloud, Cpu, Keyboard, LayoutDashboard, Lock, LogOut, MonitorSmartphone, Moon, PanelLeftClose, PanelLeftOpen, Settings, Sparkles, Sun, UserCog, Users, Wallet, Zap,
} from 'lucide-vue-next'
import { useThemeStore } from '@/stores/theme'
import { MENUS, activeMenuOf, breadcrumbOf, isMenuActive, titleOf } from '@/router/menu'
import { INSIGHT_TABS, LEDGER_TABS, SETTINGS_TABS } from '@/router/routes'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { useSystemStore } from '@/stores/system'
import AppTabs from '@/components/ui/AppTabs.vue'
import AiPanel from '@/components/AiPanel.vue'
import LockCoinGame from '@/components/LockCoinGame.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()
const system = useSystemStore()

const iconMap: Record<string, unknown> = { LayoutDashboard, Wallet, ChartLine, Settings }
const theme = useThemeStore()
const userMenuOpen = ref(false)

/** 头像预设与「个人资料」页保持一致：存 preset:<key>，这里只负责渲染 */
const AVATAR_CSS: Record<string, string> = {
  aurora: 'linear-gradient(140deg,#6f97ff,#7c5cfc)',
  sunset: 'linear-gradient(140deg,#ffb26b,#ff6b8a)',
  forest: 'linear-gradient(140deg,#4fdcae,#17a86f)',
  ocean: 'linear-gradient(140deg,#4ad3e8,#0f7fd1)',
  berry: 'linear-gradient(140deg,#f78ca8,#c14d9b)',
  slate: 'linear-gradient(140deg,#9aa7bd,#5b6878)',
}
const avatarStyle = computed(() => {
  const raw = auth.user?.avatar || ''
  const key = raw.startsWith('preset:') ? raw.slice(7) : ''
  return key && AVATAR_CSS[key] ? { background: AVATAR_CSS[key] } : {}
})
const helpOpen = ref(false)

/** 锁屏：只遮住界面，不动会话；刷新后仍保持锁定，必须输入当前用户密码解锁 */
const locked = ref(sessionStorage.getItem('fam.locked') === '1')
const unlockPassword = ref('')
const unlocking = ref(false)
const unlockError = ref('')

function lockScreen() {
  locked.value = true
  unlockPassword.value = ''
  unlockError.value = ''
  sessionStorage.setItem('fam.locked', '1')
  helpOpen.value = false
  userMenuOpen.value = false
}

/** 锁屏时钟：锁定时每秒刷新，解锁后停止计时器 */
const nowTick = ref(new Date())
let clockTimer: number | null = null
watch(locked, (value) => {
  if (value) {
    nowTick.value = new Date()
    clockTimer = window.setInterval(() => { nowTick.value = new Date() }, 1000)
  } else if (clockTimer) {
    window.clearInterval(clockTimer)
    clockTimer = null
  }
}, { immediate: true })
// 组件卸载（如在锁定态被登出）时清理定时器，避免累积（L-4）
onBeforeUnmount(() => {
  if (clockTimer) {
    window.clearInterval(clockTimer)
    clockTimer = null
  }
})

const clockTime = computed(() => nowTick.value.toLocaleTimeString('zh-CN', {
  timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit',
}))
const clockSeconds = computed(() => nowTick.value.toLocaleTimeString('zh-CN', {
  timeZone: 'Asia/Shanghai', hour12: false, second: '2-digit',
}).slice(-2))
const clockDate = computed(() => nowTick.value.toLocaleDateString('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
}))

const QUOTES = [
  '资产有数，资金有脉。',
  '账目清楚，心里才踏实。',
  '先看清钱的流向，再谈增长。',
  '记录不是束缚，是对未来的掌控。',
  '每一笔支出，都是一次选择。',
  '真正的自由，来自知道自己有多少。',
  '慢慢积累，比一次暴富更可靠。',
  '家庭财务的秩序，从一张表开始。',
  '让数字说话，让判断更冷静。',
  '今天的克制，是明天的余地。',
  '别让情绪替你做财务决定。',
  '看得见的账，才守得住的财。',
  '稳健不是保守，是留有余地。',
  '钱要流动，但方向要清楚。',
  '把复杂留给自己，把清晰留给家人。',
]
/** 每日一言：按日期取，同一天保持不变 */
const dailyQuote = computed(() => {
  const d = new Date()
  const dayIndex = Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86_400_000)
  return QUOTES[dayIndex % QUOTES.length]
})

async function unlockScreen() {
  if (!unlockPassword.value || unlocking.value) return
  unlocking.value = true
  unlockError.value = ''
  try {
    await api.post('/auth/verify-password', { password: unlockPassword.value })
    locked.value = false
    sessionStorage.removeItem('fam.locked')
    unlockPassword.value = ''
  } catch (err) {
    unlockError.value = errorMessage(err)
    unlockPassword.value = ''
  } finally {
    unlocking.value = false
  }
}

const themeIcon = computed(
  () => (theme.mode === 'dark' ? Moon : theme.mode === 'light' ? Sun : MonitorSmartphone),
)
const themeLabel = computed(
  () => ({ dark: '深色', light: '浅色', system: '跟随系统' }[theme.mode] || '跟随系统'),
)
/** 顶部一键切换：浅色 → 深色 → 跟随系统 → 浅色… */
function cycleTheme() {
  const next = theme.mode === 'light' ? 'dark' : theme.mode === 'dark' ? 'system' : 'light'
  theme.setMode(next as 'light' | 'dark' | 'system', true)
}

const activeMenu = computed(() => activeMenuOf(route))
const mobileTitle = computed(() => titleOf(route, true))
const pageTitle = computed(() => titleOf(route, false))
const crumbs = computed(() => breadcrumbOf(route))

/** 右上角 AI 模型快捷键：循环切换 自动 / 仅本地 / 第三方，全局生效 */
const aiModelLabel = computed(() => (
  { auto: '自动', local: '本地', third_party: '第三方' }[ui.aiModel] || '自动'
))
const aiModelIcon = computed(() => (
  ui.aiModel === 'local' ? Cpu : ui.aiModel === 'third_party' ? Cloud : Zap
))

/** 二级页签：家庭台账 / 脉诊 / 设置 内部页使用顶部页签，不新增一级菜单 */
const secondaryTabs = computed(() => {
  switch (activeMenu.value) {
    case 'ledger': return LEDGER_TABS
    case 'insights': return INSIGHT_TABS
    case 'settings': return SETTINGS_TABS
    default: return []
  }
})

const showSecondary = computed(() => secondaryTabs.value.length > 0)

watch(
  () => route.path,
  () => {
    userMenuOpen.value = false
  },
)

/** 加载家庭「金额精度」，供全站金额显示统一使用（08 §4，默认两位）。 */
onMounted(async () => {
  try {
    const fin = await api.get<{ amount_precision?: number }>('/settings/finance')
    ui.setAmountPrecision(fin.amount_precision)
  } catch {
    /* 未初始化或无权限时保持默认两位 */
  }
})

async function logout() {
  const ok = await ui.askConfirm({
    title: '确认退出登录',
    message: '退出后需要重新输入账号密码。本地业务数据不会被清除。',
    confirmText: '确认退出',
  })
  if (!ok.ok) return
  await auth.logout()
  system.reset()
  router.push('/login')
}
</script>

<template>
  <div class="shell" :class="{ collapsed: ui.sidebarCollapsed }">
    <div class="app-backdrop" aria-hidden="true" />

    <!-- 侧栏（桌面） -->
    <aside class="shell-sidebar" aria-label="主导航">
      <div class="brand">
        <img src="/logo.png" alt="财资脉枢" class="logo" />
        <span class="brand-text">
          <strong>财资脉枢</strong>
          <small>PulseHub</small>
        </span>
      </div>

      <nav class="nav">
        <button
          v-for="m in MENUS"
          :key="m.key"
          class="nav-item"
          :class="{ 'is-active': isMenuActive(route, m.key) }"
          type="button"
          :aria-current="isMenuActive(route, m.key) ? 'page' : undefined"
          @click="router.push(m.home)"
        >
          <span class="bar" aria-hidden="true" />
          <component :is="iconMap[m.icon]" :size="18" />
          <span class="label">{{ m.label }}</span>
        </button>
      </nav>

      <div class="sidebar-foot">
        <button class="nav-item ghost" type="button" @click="ui.toggleSidebar()">
          <component :is="ui.sidebarCollapsed ? PanelLeftOpen : PanelLeftClose" :size="18" />
          <span class="label">收起侧栏</span>
        </button>
      </div>
    </aside>

    <!-- 顶栏 -->
    <header class="shell-topbar">
      <div class="tb-left">
        <h2 class="tb-title">{{ pageTitle }}</h2>
        <span v-if="crumbs.length" class="tb-crumb">{{ crumbs.join(' / ') }}</span>
      </div>
      <div class="tb-right">
        <button class="icon-btn" type="button" :title="`主题：${themeLabel}（点击切换）`"
                :aria-label="`切换主题，当前${themeLabel}`" @click="cycleTheme">
          <component :is="themeIcon" :size="17" />
        </button>
        <button class="icon-btn" type="button" aria-label="帮助与快捷键" @click="helpOpen = true">
          <CircleHelp :size="17" />
        </button>
        <button class="icon-btn" type="button" aria-label="提醒" @click="router.push('/ledger/reminders')">
          <Bell :size="17" />
          <span class="dot" aria-hidden="true" />
        </button>
        <button
          class="icon-btn ai-model-btn"
          :class="{ on: ui.aiModel !== 'auto' }"
          type="button"
          :title="`AI 模型：${aiModelLabel}（点击切换到 ${ui.aiModel === 'auto' ? '仅本地' : ui.aiModel === 'local' ? '第三方' : '自动'}）`"
          :aria-label="`切换 AI 模型，当前 ${aiModelLabel}`"
          @click="ui.cycleAiModel()"
        >
          <component :is="aiModelIcon" :size="17" />
          <span class="ai-model-label">{{ aiModelLabel }}</span>
        </button>
        <button class="ai-btn" type="button" @click="ui.openAiPanel(pageTitle)">
          <Sparkles :size="15" />
          <span>AI 助手</span>
        </button>
        <div class="user">
          <button class="user-btn" type="button" @click="userMenuOpen = !userMenuOpen">
            <span class="avatar" aria-hidden="true" :style="avatarStyle">{{ auth.displayName.slice(0, 1) || 'U' }}</span>
            <span class="uname">{{ auth.displayName }}</span>
          </button>
          <div v-if="userMenuOpen" class="user-menu">
            <p class="um-head">
              <strong>{{ auth.displayName }}</strong>
              <small>{{ auth.roleLabel }}</small>
            </p>
            <button type="button" @click="router.push('/settings/profile'); userMenuOpen = false">
              <UserCog :size="15" /> 个人资料与密码
            </button>
            <button type="button" @click="router.push('/settings/household'); userMenuOpen = false">
              <Users :size="15" /> 家庭与成员
            </button>
            <button type="button" @click="helpOpen = true"><Keyboard :size="15" /> 快捷键</button>
            <button type="button" @click="lockScreen"><Lock :size="15" /> 锁定屏幕</button>
            <button class="danger" type="button" @click="logout"><LogOut :size="15" /> 退出登录</button>
          </div>
        </div>
      </div>
    </header>

    <!-- 移动顶栏 -->
    <header class="mobile-topbar">
      <h2>{{ mobileTitle }}</h2>
      <div class="mt-right">
        <button class="icon-btn" type="button" :aria-label="`切换主题，当前${themeLabel}`" @click="cycleTheme">
          <component :is="themeIcon" :size="18" />
        </button>
        <button class="icon-btn" type="button" aria-label="提醒" @click="router.push('/ledger/reminders')">
          <Bell :size="18" />
        </button>
        <button class="icon-btn" type="button" :aria-label="`切换 AI 模型，当前 ${aiModelLabel}`" @click="ui.cycleAiModel()">
          <component :is="aiModelIcon" :size="18" />
        </button>
        <button class="icon-btn" type="button" aria-label="AI 助手" @click="ui.openAiPanel(mobileTitle)">
          <Sparkles :size="18" />
        </button>
        <div class="user">
          <button class="user-btn" type="button" aria-label="账户菜单" @click="userMenuOpen = !userMenuOpen">
            <span class="avatar" aria-hidden="true" :style="avatarStyle">{{ auth.displayName.slice(0, 1) || 'U' }}</span>
          </button>
          <div v-if="userMenuOpen" class="user-menu">
            <p class="um-head">
              <strong>{{ auth.displayName }}</strong>
              <small>{{ auth.roleLabel }}</small>
            </p>
            <button type="button" @click="router.push('/settings/profile'); userMenuOpen = false">
              <UserCog :size="15" /> 个人资料与密码
            </button>
            <button type="button" @click="router.push('/settings/household'); userMenuOpen = false">
              <Users :size="15" /> 家庭与成员
            </button>
            <button type="button" @click="router.push('/settings'); userMenuOpen = false">
              <Settings :size="15" /> 设置与主题
            </button>
            <button class="danger" type="button" @click="logout">
              <LogOut :size="15" /> 退出登录
            </button>
          </div>
        </div>
      </div>
    </header>

    <!-- 内容 -->
    <main class="shell-main">
      <div class="content">
        <AppTabs v-if="showSecondary" :tabs="secondaryTabs" />
        <RouterView v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" />
          </Transition>
        </RouterView>
      </div>
    </main>

    <!-- 移动底部导航（四个一级菜单，选中由路由派生） -->
    <nav class="bottom-nav" aria-label="底部导航">
      <button
        v-for="m in MENUS"
        :key="m.key"
        class="bn-item"
        :class="{ 'is-active': isMenuActive(route, m.key) }"
        type="button"
        :aria-current="isMenuActive(route, m.key) ? 'page' : undefined"
        @click="router.push(m.home)"
      >
        <component :is="iconMap[m.icon]" :size="20" />
        <span>{{ m.label }}</span>
      </button>
    </nav>

    <AiPanel />

    <!-- 帮助 / 快捷键 -->
    <div v-if="helpOpen" class="help-mask" :style="{ background: 'var(--scrim)' }" @click.self="helpOpen = false">
      <div class="help-card glass">
        <h3>帮助与快捷键</h3>
        <ul>
          <li><kbd>Ctrl</kbd> + <kbd>K</kbd> 打开 AI 助手</li>
          <li><kbd>Esc</kbd> 关闭当前弹窗或抽屉</li>
          <li><kbd>Alt</kbd> + <kbd>1~4</kbd> 切换一级菜单</li>
          <li>一级菜单只有四项：脉枢台、家庭台账、脉诊、设置</li>
        </ul>
        <p class="help-note">
          AI 是全局能力，不是独立菜单。所有 AI 结论都需用户确认后才写入正式台账。
        </p>
      </div>
    </div>

    <!-- 锁屏遮罩：左侧北京时间 + 每日一言，右侧解锁；未解锁前无法查看或操作任何页面 -->
    <div v-if="locked" class="lock-screen">
      <span class="lock-orb orb-a" aria-hidden="true" />
      <span class="lock-orb orb-b" aria-hidden="true" />
      <div class="lock-panel">
        <section class="lock-left">
          <div class="clock">
            <span class="clock-main">{{ clockTime }}</span>
            <span class="clock-sec">{{ clockSeconds }}</span>
          </div>
          <p class="clock-date">{{ clockDate }} · 北京时间</p>
          <p class="quote">「{{ dailyQuote }}」</p>

          <!-- 锁屏小游戏：跳起来顶问号砖拿金币 -->
          <LockCoinGame />
        </section>

        <section class="lock-right">
          <div class="lock-card">
            <!-- 品牌标识放在解锁卡片里，和密码框一起构成一个整体 -->
            <div class="lock-brand">
              <img src="/logo.png" alt="财资脉枢" class="lock-logo" />
              <div class="lock-brand-text">
                <strong>财资脉枢</strong>
                <small>家庭财务中枢</small>
              </div>
            </div>
            <h2>屏幕已锁定</h2>
            <p class="lock-sub">输入当前账号密码继续使用</p>
            <input
              v-model="unlockPassword" type="password" class="lock-input"
              placeholder="账号密码" autocomplete="current-password"
              @keyup.enter="unlockScreen"
            />
            <p v-if="unlockError" class="lock-error">{{ unlockError }}</p>
            <button class="lock-btn" type="button" :disabled="unlocking || !unlockPassword" @click="unlockScreen">
              {{ unlocking ? '解锁中…' : '解锁' }}
            </button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.shell { min-height: 100vh; position: relative; }

/* 侧栏 */
.shell-sidebar {
  position: fixed;
  top: 0; left: 0; bottom: 0;
  width: var(--sidebar-width);
  z-index: 20;
  display: flex;
  flex-direction: column;
  padding: var(--space-3);
  padding-top: calc(var(--space-3) + var(--safe-top));
  background: var(--shell-bg);
  border-right: var(--glass-border-line);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  color: var(--shell-ink);
  overflow: hidden;
  transition: width var(--motion-base) var(--ease-out);
}
.shell.collapsed .shell-sidebar { width: var(--sidebar-collapsed); }
.shell.collapsed .brand { justify-content: center; padding-left: 0; padding-right: 0; }

.brand { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-2) var(--space-5); }
.logo {
  width: 36px; height: 36px; flex: 0 0 36px;
  object-fit: contain;
  filter: drop-shadow(0 6px 14px rgba(53, 109, 255, 0.35));
}
.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  max-width: 140px;
  overflow: hidden;
  white-space: nowrap;
  transition: max-width var(--motion-base) var(--ease-out), opacity 160ms linear;
}
.shell.collapsed .brand-text { max-width: 0; opacity: 0; }
.brand-text strong { font-size: 15px; color: var(--ink-strong); }
.brand-text small { font-size: 11px; color: var(--ink-muted); }

.nav { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 44px;
  padding: 0 12px;
  border: none;
  border-radius: var(--glass-radius-control);
  background: transparent;
  color: var(--ink);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
  overflow: hidden;
  transition: gap var(--motion-base) var(--ease-out),
              padding var(--motion-base) var(--ease-out),
              background-color var(--motion-fast) linear;
}
.nav-item .label {
  max-width: 150px;
  overflow: hidden;
  white-space: nowrap;
  transition: max-width var(--motion-base) var(--ease-out), opacity 160ms linear;
}
.shell.collapsed .nav-item {
  justify-content: center;
  gap: 0;
  padding: 0 8px;
}
.shell.collapsed .nav-item .label { max-width: 0; opacity: 0; }
.nav-item:hover { background: var(--glass-1); color: var(--ink-strong); }
.nav-item.is-active {
  color: var(--ink-strong);
  font-weight: 600;
  background: var(--nav-active-bg);
  box-shadow: var(--glass-inner-highlight);
}
.nav-item.is-active .bar {
  position: absolute;
  left: 0; top: 10px; bottom: 10px;
  width: 3px;
  border-radius: 3px;
  background: linear-gradient(180deg, var(--primary), var(--accent-ai));
}
.nav-item.ghost { color: var(--ink-muted); }
.sidebar-foot { padding-top: var(--space-3); border-top: 1px solid var(--border); }

/* 顶栏 */
.shell-topbar {
  position: fixed;
  top: 0;
  left: var(--sidebar-width);
  right: 0;
  height: calc(var(--topbar-height) + var(--safe-top));
  padding: var(--safe-top) var(--space-5) 0;
  z-index: 15;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  background: var(--shell-bg);
  border-bottom: var(--glass-border-line);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  transition: left var(--motion-base) var(--ease-out);
}
.shell.collapsed .shell-topbar { left: var(--sidebar-collapsed); }
.tb-left { display: flex; flex-direction: column; min-width: 0; }
.tb-title { font-size: 16px; }
.tb-crumb { font-size: 11.5px; color: var(--ink-muted); }
.tb-right { display: flex; align-items: center; gap: var(--space-2); }
.hh-name { font-size: 12.5px; color: var(--ink-muted); max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.icon-btn {
  position: relative;
  width: 36px; height: 36px;
  display: grid; place-items: center;
  border: 1px solid transparent;
  border-radius: var(--glass-radius-control);
  background: transparent;
  color: var(--ink);
  cursor: pointer;
}
.icon-btn:hover { background: var(--glass-1); border-color: var(--border); }
.icon-btn .dot {
  position: absolute; top: 7px; right: 8px;
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--warning);
}

/* 右上角 AI 模型快捷键：带小文字标签，高亮表示「非自动」 */
.ai-model-btn {
  width: auto; height: 34px;
  display: inline-flex; align-items: center; gap: 5px;
  padding: 0 10px;
  font-size: 12px; font-weight: 600;
}
.ai-model-btn.on { background: var(--glass-1); border-color: var(--accent-ai); color: var(--accent-ai); }
.ai-model-label { white-space: nowrap; }

.ai-btn {
  display: inline-flex; align-items: center; gap: 6px;
  height: 34px; padding: 0 12px;
  border: none; border-radius: var(--glass-radius-control);
  color: #fff; font-size: 13px; cursor: pointer;
  background: linear-gradient(135deg, var(--accent-ai), var(--primary));
  box-shadow: 0 6px 16px rgba(124, 92, 252, 0.3);
}

.user { position: relative; }
.user-btn {
  display: inline-flex; align-items: center; gap: 8px;
  height: 34px; padding: 0 10px 0 4px;
  border: 1px solid var(--border); border-radius: 999px;
  background: var(--glass-1); color: var(--ink-strong); cursor: pointer;
}
.avatar {
  width: 26px; height: 26px; border-radius: 50%;
  display: grid; place-items: center;
  background: linear-gradient(135deg, var(--primary), var(--accent-ai));
  color: #fff; font-size: 12px; font-weight: 600;
}
.uname { font-size: 13px; max-width: 96px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.user-menu {
  position: absolute; right: 0; top: calc(100% + 8px);
  min-width: 208px; padding: 6px;
  border-radius: var(--glass-radius-card);
  background: var(--glass-3);
  border: var(--glass-border-line);
  box-shadow: var(--glass-shadow-heavy);
  backdrop-filter: blur(var(--glass-blur));
}
.um-head { padding: 8px 10px 10px; border-bottom: 1px solid var(--border); margin-bottom: 4px; }
.um-head strong { display: block; font-size: 13.5px; color: var(--ink-strong); }
.um-head small { color: var(--ink-muted); font-size: 11.5px; }
.user-menu button {
  display: flex; align-items: center; gap: 8px;
  width: 100%; height: 36px; padding: 0 10px;
  border: none; border-radius: 9px; background: transparent;
  color: var(--ink); font-size: 13.5px; cursor: pointer; text-align: left;
}
.user-menu button:hover { background: var(--glass-1); color: var(--ink-strong); }
.user-menu button.danger { color: var(--danger); }

/* 内容 */
.shell-main {
  padding-left: var(--sidebar-width);
  padding-top: calc(var(--topbar-height) + var(--safe-top));
  min-height: 100vh;
  position: relative;
  z-index: 1;
  transition: padding-left var(--motion-base) var(--ease-out);
}
.shell.collapsed .shell-main { padding-left: var(--sidebar-collapsed); }
.content {
  max-width: var(--content-max);
  margin: 0 auto;
  padding: var(--space-5);
  padding-bottom: calc(var(--space-6) + var(--safe-bottom));
}

.mobile-topbar { display: none; }
.bottom-nav { display: none; }

/* 帮助 */
.help-mask { position: fixed; inset: 0; z-index: 1300; display: grid; place-items: center; padding: var(--space-4); }
.help-card { width: min(480px, 100%); padding: var(--space-5); border-radius: var(--glass-radius-panel); }
.help-card h3 { margin-bottom: var(--space-3); }
.help-card ul { display: flex; flex-direction: column; gap: 8px; font-size: 13.5px; color: var(--ink); }
kbd {
  padding: 1px 6px; border-radius: 5px;
  border: 1px solid var(--border); background: var(--glass-1);
  font-size: 12px; font-family: inherit;
}
.help-note { margin-top: var(--space-3); font-size: 12.5px; color: var(--ink-muted); }
.help-foot { margin-top: var(--space-4); display: flex; justify-content: flex-end; }

/* 响应式：移动端 */
@media (max-width: 900px) {
  .shell-sidebar { display: none; }
  .shell-topbar { display: none; }
  .mobile-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    /* 用 sticky 而非 fixed：iOS 地址栏伸缩时 fixed 元素会跟着跳动，sticky 稳定且不遮挡内容 */
    position: sticky;
    top: 0;
    height: calc(52px + var(--safe-top));
    padding: var(--safe-top) var(--space-4) 0;
    z-index: 15;
    background: var(--shell-bg);
    border-bottom: var(--glass-border-line);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
  }
  .mobile-topbar h2 { font-size: 16px; }
  .mt-right { display: flex; gap: 2px; }
  .shell-main {
    padding-left: 0;
    padding-top: 0; /* sticky 顶栏已占据流内空间，无需额外留白 */
  }
  .content { padding: var(--space-4); padding-bottom: calc(var(--bottom-nav-height) + var(--space-5) + var(--safe-bottom)); }
  .bottom-nav {
    display: flex;
    position: fixed;
    left: 0; right: 0; bottom: 0;
    z-index: 20;
    height: calc(var(--bottom-nav-height) + var(--safe-bottom));
    padding-bottom: var(--safe-bottom);
    background: var(--shell-bg);
    border-top: var(--glass-border-line);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
  }
  .bn-item {
    flex: 1;
    min-height: 44px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 2px;
    border: none; background: transparent;
    color: var(--ink-muted); font-size: 11px; cursor: pointer;
  }
  .bn-item.is-active { color: var(--primary); font-weight: 600; }
  .hh-name, .uname { display: none; }
  .mt-right { align-items: center; }
  .mobile-topbar .icon-btn { width: 40px; height: 40px; }
  .mt-right .user-btn { height: 36px; padding: 0 4px; border: none; background: transparent; }
  .mt-right .user-menu { min-width: 190px; }
}

/* 锁屏：左右布局（左：北京时间 + 每日一言；右：解锁） */
.lock-screen {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  /* 单列显式定宽：默认 auto 轨道会被内容撑到 max-content，窄屏上会横向溢出 */
  grid-template-columns: minmax(0, 1fr);
  place-items: center;
  padding: var(--space-6);
  background: radial-gradient(120% 90% at 20% 20%, rgba(24, 36, 62, 0.96), rgba(6, 11, 22, 0.94));
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}
.lock-panel {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: clamp(32px, 7vw, 88px);
  width: min(940px, 100%);
}
/* 左侧：北京时间 + 每日一言 */
.lock-left { color: #E8EEF9; min-width: 0; }
.lock-brand {
  display: flex; align-items: center; gap: 12px;
  margin: 0 0 18px; padding-bottom: 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
}
.lock-logo {
  width: 42px; height: 42px; object-fit: contain;
  filter: drop-shadow(0 8px 20px rgba(53, 109, 255, 0.45));
}
.lock-brand-text { display: flex; flex-direction: column; line-height: 1.25; }
.lock-brand-text strong { font-size: 16px; letter-spacing: 1px; color: #F2F6FD; }
.lock-brand-text small { font-size: 11.5px; color: #8FA3C4; letter-spacing: 1px; }
.clock { display: flex; align-items: flex-start; gap: 10px; font-variant-numeric: tabular-nums; }
.clock-main {
  font-size: clamp(58px, 9vw, 96px); font-weight: 300; letter-spacing: 3px; line-height: 1;
  color: #F2F6FD; text-shadow: 0 0 48px rgba(124, 92, 252, 0.35);
}
.clock-sec { font-size: clamp(16px, 2.2vw, 22px); color: #8FA3C4; margin-top: 12px; }
.clock-date { margin: 14px 0 0; font-size: 14px; color: #9FB0CC; letter-spacing: 1.5px; }
.quote {
  margin: 30px 0 0; font-size: 15px; line-height: 1.9;
  color: #C8B47A; max-width: 32ch; letter-spacing: 0.5px;
}
/* 背景光斑：缓慢漂浮 */
.lock-orb {
  position: absolute; border-radius: 50%; filter: blur(60px); pointer-events: none;
  animation: orb-drift 18s var(--ease-in-out) infinite alternate;
}
.orb-a { width: 340px; height: 340px; left: 8%; top: 12%; background: rgba(75, 123, 255, 0.22); }
.orb-b { width: 300px; height: 300px; right: 12%; bottom: 10%; background: rgba(111, 227, 224, 0.16); animation-delay: -8s; }
@keyframes orb-drift {
  from { transform: translate3d(0, 0, 0) scale(1); }
  to { transform: translate3d(36px, -28px, 0) scale(1.12); }
}
/* 右侧：解锁 */
.lock-right { justify-self: end; }
.lock-card {
  width: 300px;
  padding: var(--space-6);
  border-radius: 18px;
  text-align: center;
  color: #E8EEF9;
  background: rgba(20, 30, 52, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
}
.lock-icon {
  width: 48px; height: 48px; margin: 0 auto var(--space-3);
  display: grid; place-items: center; border-radius: 14px;
  color: #C8B47A; background: rgba(200, 180, 122, 0.12);
}
.lock-card h2 { margin: 0 0 6px; font-size: 17px; color: #E8EEF9; }
.lock-sub { margin: 0 0 var(--space-4); font-size: 12.5px; color: #9FB0CC; }
.lock-input {
  width: 100%; height: 42px; padding: 0 14px; border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.06); color: #E8EEF9; font-size: 14px;
}
.lock-input:focus { outline: none; border-color: rgba(124, 92, 252, 0.6); }
.lock-error { margin: 8px 0 0; font-size: 12.5px; color: #FF8B8B; }
.lock-btn {
  width: 100%; height: 42px; margin-top: var(--space-4); border: 0;
  border-radius: 10px; cursor: pointer; font-size: 14px; font-weight: 600; color: #fff;
  background: linear-gradient(135deg, #7C5CFC, #4B7BFF);
}
.lock-btn:disabled { opacity: 0.55; cursor: not-allowed; }

/* 窄屏：上下堆叠 */
@media (max-width: 760px) {
  .lock-panel { grid-template-columns: 1fr; gap: var(--space-6); }
  .lock-left { text-align: center; }
  .lock-brand { justify-content: center; }
  .lock-brand-text { text-align: left; }
  .clock { justify-content: center; }
  .quote { margin-left: auto; margin-right: auto; }
  .lock-right { justify-self: center; }
  .lock-card { width: min(320px, 100%); }
}

@media (prefers-reduced-motion: reduce) {
  .lock-orb { animation: none; }
}
</style>
