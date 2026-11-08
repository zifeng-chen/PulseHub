<script setup lang="ts">
/**
 * 登录页 /login（04 §4、17 §7）。
 *
 * 视觉：左侧品牌叙事 + 资金趋势动效，右侧登录卡片。
 * 浅色 / 深色都成立——配色全部走主题变量，右上角可直接切换并写回本地偏好。
 * 动效只有「曲线生长 + 脉冲游走」两段，纯 CSS / SMIL，尊重 prefers-reduced-motion。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, ArrowRight, ServerCog, Moon, Sun } from 'lucide-vue-next'
import { errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useSystemStore } from '@/stores/system'
import { useThemeStore } from '@/stores/theme'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'

const router = useRouter()
const auth = useAuthStore()
const system = useSystemStore()
const theme = useThemeStore()

const username = ref('')
const password = ref('')
const remember = ref(true)

/** iOS Safari 会清理 7 天未访问站点的本地存储；添加到主屏幕后不作此限制 */
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent)
const isStandalone = window.matchMedia('(display-mode: standalone)').matches
  || (navigator as unknown as { standalone?: boolean }).standalone === true
const installTipDismissed = ref(localStorage.getItem('pulsehub.ios.tip') === '1')
const showInstallTip = computed(() => isIOS && !isStandalone && !installTipDismissed.value)

function dismissInstallTip() {
  installTipDismissed.value = true
  localStorage.setItem('pulsehub.ios.tip', '1')
}
const submitting = ref(false)
const error = ref('')
const locked = ref(false)

const stateText = computed(() => {
  if (locked.value) return '账号已被短暂锁定'
  if (error.value) return error.value
  if (submitting.value) return '正在登录…'
  return ''
})

/* ---------------- 主题 ---------------- */

const isDark = computed(() => theme.resolved === 'dark')

function toggleTheme() {
  theme.setMode(isDark.value ? 'light' : 'dark')
}


/* ---------------- 资金趋势插画 ----------------
 * 11 根柱子整体上行（中间留一个回落，避免看着假），
 * 一个小人踩着柱顶一路往上跳，跳到最右端淡出后从最左端重来。
 * 纯装饰，不显示任何真实金额。
 */

const GROUND = 226
const BAR_W = 26
const BAR_GAP = 20
const BARS = [42, 56, 50, 72, 88, 78, 104, 122, 114, 140, 156]
const CHART_W = 560
const barTotal = BARS.length * BAR_W + (BARS.length - 1) * BAR_GAP
const barStartX = (CHART_W - barTotal) / 2

const bars = BARS.map((h, i) => {
  const x = barStartX + i * (BAR_W + BAR_GAP)
  return { x, h, y: GROUND - h, cx: x + BAR_W / 2 }
})

/** 小人在柱顶之间跳跃：每段用三次贝塞尔画一条抛物线 */
function buildHopPath(): string {
  const first = bars[0]
  if (!first) return ''
  let d = `M ${first.cx} ${first.y}`
  for (let i = 1; i < bars.length; i += 1) {
    const prev = bars[i - 1]
    const next = bars[i]
    const dx = next.cx - prev.cx
    const lift = 26
    d += ` C ${prev.cx + dx * 0.32} ${prev.y - lift}, ${next.cx - dx * 0.32} ${next.y - lift}, ${next.cx} ${next.y}`
  }
  return d
}

const hopPath = buildHopPath()
const prefersReduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

/* ---------------- 登录 ---------------- */

async function submit() {
  if (submitting.value) return
  error.value = ''
  locked.value = false
  if (!username.value.trim() || !password.value) {
    error.value = '请输入账号和密码'
    return
  }
  submitting.value = true
  try {
    await auth.login(username.value.trim(), password.value, remember.value)
    // 登录后统一进脉枢台：不沿用 ?redirect=，避免落到上次停留的二级页面
    const target = '/dashboard'
    // 主题偏好属于体验优化，不能阻塞进入系统：放到跳转之后异步加载
    void theme.loadFromServer()
    try {
      await router.replace(target)
    } catch {
      // 目标页按需加载失败或被守卫中断时兜底整页跳转，避免停在登录页
      window.location.replace(target)
    }
  } catch (err) {
    const message = errorMessage(err)
    error.value = message
    // 账号锁定用稳定错误码判定（ACCOUNT_LOCKED / 423），不依赖文案，避免后端改文案即失效（L-8）
    const code = (err as { code?: string })?.code
    const status = (err as { status?: number })?.status
    if (code === 'ACCOUNT_LOCKED' || status === 423) locked.value = true
    password.value = ''
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="ambient-layer" aria-hidden="true">
      <span class="ambient-blob blob-1" />
      <span class="ambient-blob blob-2" />
      <span class="ambient-blob blob-3" />
    </div>

    <header class="topbar">
      <div class="brand-mini">
        <img src="/logo.png" alt="" class="brand-mark" />
        <span>财资脉枢</span>
        <em>PulseHub</em>
      </div>
      <button
        type="button"
        class="theme-toggle"
        :aria-label="isDark ? '切换到浅色模式' : '切换到深色模式'"
        :title="isDark ? '切换到浅色模式' : '切换到深色模式'"
        @click="toggleTheme"
      >
        <Sun v-if="isDark" :size="17" />
        <Moon v-else :size="17" />
      </button>
    </header>

    <main class="stage">
      <section class="intro">
        <p class="eyebrow">家庭资产 · 现金流 · 负债 · 租赁</p>
        <h1>资产有数，<br /><span class="grad">资金有脉。</span></h1>
        <p class="lede">
          把账户、资产、负债、投资与租赁收进同一本账。统计由后端确定性计算，
          本地 AI 只做解释，不替你改数。
        </p>

        <div class="trend">
          <svg class="trend-svg" :viewBox="`0 0 ${CHART_W} 260`" role="img" aria-label="资金趋势示意：柱状上行">
            <defs>
              <linearGradient id="barFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" style="stop-color: var(--accent-ai); stop-opacity: 0.95" />
                <stop offset="100%" style="stop-color: var(--primary); stop-opacity: 0.92" />
              </linearGradient>
              <linearGradient id="groundFade" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" style="stop-color: var(--primary); stop-opacity: 0.16" />
                <stop offset="100%" style="stop-color: var(--primary); stop-opacity: 0" />
              </linearGradient>
              <clipPath id="chartArea"><rect x="0" y="0" :width="CHART_W" height="252" /></clipPath>
            </defs>

            <rect x="0" :y="GROUND" :width="CHART_W" height="34" fill="url(#groundFade)" />
            <line x1="0" :x2="CHART_W" :y1="GROUND" :y2="GROUND" class="ground" />

            <g v-for="(b, i) in bars" :key="i">
              <rect :x="b.x" :y="b.y" :width="BAR_W" :height="b.h" rx="6" class="bar-base" />
              <rect :x="b.x" :y="b.y" :width="BAR_W" :height="b.h" rx="6" class="bar-fill">
                <animate
                  v-if="!prefersReduced" attributeName="height" :values="'0;' + b.h + ';' + b.h"
                  keyTimes="0;0.55;1" dur="4.2s" :begin="(-i * 0.26) + 's'"
                  repeatCount="indefinite"
                />
                <animate
                  v-if="!prefersReduced" attributeName="y" :values="GROUND + ';' + b.y + ';' + b.y"
                  keyTimes="0;0.55;1" dur="4.2s" :begin="(-i * 0.26) + 's'"
                  repeatCount="indefinite"
                />
              </rect>
            </g>

            <g clip-path="url(#chartArea)">
              <g class="jumper" :transform="prefersReduced ? 'translate(' + bars[0].cx + ', ' + bars[0].y + ')' : undefined">
                <g transform="scale(1.35)">
                <g class="figure">
                  <circle cx="0" cy="-20" r="4.2" />
                  <rect x="-4" y="-15" width="8" height="11" rx="4" />
                  <path class="limb arm arm-l" d="M -4 -13.5 L -9 -18.5" />
                  <path class="limb arm arm-r" d="M 4 -13.5 L 9 -18.5" />
                  <path class="limb leg leg-l" d="M -2.4 -4.5 L -6 -0.5" />
                  <path class="limb leg leg-r" d="M 2.4 -4.5 L 6 -0.5" />
                </g>
                </g>
                <animateMotion v-if="!prefersReduced" :path="hopPath" dur="9s" repeatCount="indefinite" />
                <animate
                  v-if="!prefersReduced" attributeName="opacity"
                  values="0;1;1;0;0" keyTimes="0;0.06;0.92;0.97;1"
                  dur="9s" repeatCount="indefinite"
                />
              </g>
            </g>
          </svg>
        </div>

        <ul class="points">
          <li><Lock :size="15" /> 本地优先部署，数据默认不出网</li>
          <li><ServerCog :size="15" /> 后端确定性统计，AI 只做解释</li>
        </ul>
      </section>

      <section class="login-card">
        <header class="card-head">
          <h2>登录</h2>
          <p class="card-sub">使用家庭管理员账号进入</p>
          <p v-if="!system.backendReachable" class="offline">后端服务状态不可用，请确认服务已启动</p>
        </header>

        <form class="form" @submit.prevent="submit">
          <AppField label="账号" required for-id="login-username">
            <AppInput
              id="login-username"
              v-model="username"
              placeholder="请输入账号"
              autocomplete="username"
              @enter="submit"
            />
          </AppField>

          <AppField label="密码" required for-id="login-password">
            <AppInput
              id="login-password"
              v-model="password"
              type="password"
              mask="coin"
              placeholder="请输入密码"
              autocomplete="current-password"
              @enter="submit"
            />
          </AppField>

          <label class="remember">
            <input v-model="remember" type="checkbox" />
            <span>记住此设备</span>
          </label>

          <p v-if="stateText" class="state" :class="{ danger: locked || error }" role="alert">{{ stateText }}</p>

          <AppButton variant="primary" size="lg" block type="submit" :loading="submitting">
            登录 <ArrowRight :size="16" />
          </AppButton>
        </form>

        <div v-if="showInstallTip" class="install-tip">
          <p>
            iPhone / iPad 的 Safari 会清理 7 天未访问的网站数据，导致需要重新登录。
            点下方「分享」→「添加到主屏幕」，之后从桌面图标打开，登录状态会长期保留。
          </p>
          <button type="button" class="tip-dismiss" @click="dismissInstallTip">知道了</button>
        </div>

        <footer class="card-foot">
          <RouterLink to="/forgot-password">忘记密码</RouterLink>
          <RouterLink to="/setup/check">查看系统状态</RouterLink>
        </footer>
      </section>
    </main>
  </div>
</template>

<style scoped>
/* 背景：主题色光晕 + 底色，浅色/深色共用同一套变量 */
.login-page {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  padding: calc(var(--space-5) + var(--safe-top)) var(--space-7) var(--space-7);
  overflow: hidden;
  background:
    radial-gradient(900px 620px at 8% 0%, var(--halo-blue), transparent 62%),
    radial-gradient(780px 560px at 92% 96%, var(--halo-purple), transparent 60%),
    var(--bg-base);
  color: var(--ink);
}

/* 光斑：颜色跟随主题，浅色下不会糊成一片 */
.blob-1 {
  width: 520px; height: 520px; top: -180px; left: -8%;
  background: radial-gradient(circle, var(--halo-blue), transparent 70%);
  animation-duration: 34s;
}
.blob-2 {
  width: 460px; height: 460px; bottom: -200px; right: -6%;
  background: radial-gradient(circle, var(--halo-purple), transparent 70%);
  animation-duration: 44s; animation-delay: -12s;
}
.blob-3 {
  width: 380px; height: 380px; top: 30%; left: 42%;
  background: radial-gradient(circle, var(--halo-cyan), transparent 70%);
  animation-duration: 52s; animation-delay: -24s;
}

/* 顶栏 */
.topbar {
  position: relative;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand-mini { display: flex; align-items: center; gap: 10px; font-size: 14px; font-weight: 600; color: var(--ink-strong); }
.brand-mini em {
  font-style: normal; font-size: 11px; letter-spacing: 1.4px; text-transform: uppercase;
  color: var(--ink-muted); padding-left: 2px;
}
.brand-mark { width: 26px; height: 26px; object-fit: contain; }
.theme-toggle {
  display: grid; place-items: center;
  width: 38px; height: 38px; border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  cursor: pointer;
  transition: background .2s, border-color .2s, transform .2s;
}
.theme-toggle:hover { background: var(--glass-2); border-color: var(--primary); transform: rotate(-12deg); }

/* 主体：左叙事 + 右卡片 */
.stage {
  position: relative;
  z-index: 2;
  flex: 1;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 420px);
  gap: clamp(32px, 6vw, 96px);
  align-items: center;
  padding: clamp(24px, 5vh, 64px) 0;
}
.intro { max-width: 620px; min-width: 0; }
.eyebrow {
  font-size: 12px; letter-spacing: 1.6px; text-transform: uppercase;
  color: var(--ink-muted); margin-bottom: 18px;
}
.intro h1 {
  font-size: clamp(30px, 3.6vw, 44px);
  line-height: 1.22;
  letter-spacing: .5px;
  color: var(--ink-strong);
  font-weight: 700;
}
.intro h1 .grad {
  background: linear-gradient(115deg, var(--primary), var(--accent-ai) 70%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.lede {
  margin-top: 18px;
  font-size: 14px;
  line-height: 1.9;
  color: var(--ink);
  max-width: 520px;
}

/* 资金趋势：不套任何卡片容器，直接落在页面背景上 */
.trend { margin-top: 30px; }
.trend-svg { width: 100%; height: auto; display: block; overflow: visible; }

.ground { stroke: var(--border); stroke-width: 1; }

/* 柱子分两层：轮廓固定，亮面从底部涨上来，形成连续向上的流动 */
.bar-base { fill: var(--primary); opacity: 0.13; }
.bar-fill { fill: url(#barFill); }

.jumper { color: var(--ink-strong); filter: drop-shadow(0 3px 6px rgba(10, 22, 45, 0.22)); }
.figure { transform-box: fill-box; transform-origin: 50% 100%; animation: figure-hop 0.9s cubic-bezier(0.4, 0, 0.2, 1) infinite; }
.figure circle, .figure rect { fill: currentColor; }
.limb { fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; transform-box: fill-box; transform-origin: 50% 0%; }
.arm-l, .leg-r { animation: swing-a 0.9s ease-in-out infinite; }
.arm-r, .leg-l { animation: swing-b 0.9s ease-in-out infinite; }

@keyframes figure-hop {
  0% { transform: scale(1, 1); }
  12% { transform: scale(1.08, 0.88); }
  34% { transform: scale(0.93, 1.12); }
  64% { transform: scale(1.02, 0.98); }
  86% { transform: scale(1.06, 0.9); }
  100% { transform: scale(1, 1); }
}
@keyframes swing-a {
  0%, 100% { transform: rotate(-9deg); }
  50% { transform: rotate(11deg); }
}
@keyframes swing-b {
  0%, 100% { transform: rotate(9deg); }
  50% { transform: rotate(-11deg); }
}

/* 底部两条：横向排列，间距固定，窄屏自动换行 */
.points {
  margin: 20px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 30px;
}
.points li { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-muted); }

/* 登录卡片 */
.login-card {
  position: relative;
  width: 100%;
  min-width: 0;
  padding: var(--space-6);
  border-radius: var(--glass-radius-panel);
  background: var(--glass-2);
  border: 1px solid var(--border);
  box-shadow: var(--glass-shadow-heavy);
  backdrop-filter: blur(22px);
  -webkit-backdrop-filter: blur(22px);
}
.card-head h2 { font-size: 20px; color: var(--ink-strong); }
.card-sub { margin-top: 6px; font-size: 12.5px; color: var(--ink-muted); }
.offline { margin-top: 6px; font-size: 12.5px; color: #F4A62A; }
.form { margin-top: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }

.remember { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--ink); }
.remember input { accent-color: var(--primary); }

.install-tip {
  margin-top: var(--space-4); padding: var(--space-3) var(--space-4);
  border-radius: 12px; border: 1px solid rgba(124, 92, 252, 0.28);
  background: rgba(124, 92, 252, 0.08);
}
.install-tip p { margin: 0 0 8px; font-size: 12.5px; line-height: 1.6; color: var(--ink); }
.tip-dismiss {
  border: 0; background: transparent; color: var(--accent-ai); font-size: 12.5px;
  cursor: pointer; padding: 0;
}

.state { font-size: 13px; color: #F4A62A; }
.state.danger { color: var(--danger); }

.card-foot {
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}
.card-foot a { color: var(--primary); }

/* 输入控件跟随主题：不再强制深色底 */
.login-page :deep(.lbl) { color: var(--ink); }
.login-page :deep(.input) {
  background: var(--surface);
  border-color: var(--border);
  color: var(--ink-strong);
}
.login-page :deep(.input::placeholder) { color: var(--ink-muted); }
.login-page :deep(.err) { color: var(--danger); }

/* 动效 */
@keyframes trend-draw { to { stroke-dashoffset: 0; } }
@keyframes area-in { to { opacity: 1; } }
@keyframes flow-in { to { opacity: 1; } }

/* 触摸设备：卡片切实底，避免模糊被关掉后透底 */
@media (max-width: 900px), (pointer: coarse) {
  .login-card {
    background: var(--panel-solid);
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
  
}

@media (max-width: 960px) {
  .login-page { padding: calc(var(--space-4) + var(--safe-top)) var(--space-4) var(--space-5); }
  .stage { grid-template-columns: minmax(0, 1fr); gap: var(--space-5); padding: var(--space-4) 0 0; }
  .stage > * { min-width: 0; }
  .intro h1 { font-size: 30px; }
  .lede { display: none; }
  .points { display: none; }
  .trend { margin-top: 16px; }
  .trend-svg { max-height: 150px; }
  .eyebrow { margin-bottom: 10px; }
}

@media (prefers-reduced-motion: reduce) {
  .figure, .limb { animation: none; }
  .ambient-blob { animation: none; }
}
</style>
