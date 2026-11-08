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

/* ---------------- 资金趋势示意 ----------------
 * 纯装饰图形：不显示任何真实金额，只用走势表达「资产有数，资金有脉」。
 * 数据点固定写死，避免登录页拉接口；曲线用 Catmull-Rom 转贝塞尔，保证过点且平滑。
 */

const CW = 560
const CH = 260
const PAD_X = 18
const PAD_Y = 26
const NET = [30, 37, 34, 45, 43, 54, 51, 63, 69, 66, 78, 86, 93]
const FLOW = [24, 31, 22, 36, 28, 42, 34, 47, 40, 52, 44, 58, 50]

function toPoints(values: number[]): { x: number; y: number }[] {
  const step = (CW - PAD_X * 2) / (values.length - 1)
  const usable = CH - PAD_Y * 2
  return values.map((v, i) => ({
    x: PAD_X + i * step,
    y: CH - PAD_Y - (v / 100) * usable,
  }))
}

function smoothPath(values: number[]): string {
  const pts = toPoints(values)
  const first = pts[0]
  if (!first) return ''
  let d = `M ${first.x.toFixed(1)} ${first.y.toFixed(1)}`
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = pts[i - 1] ?? pts[i]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[i + 2] ?? p2
    const c1x = p1.x + (p2.x - p0.x) / 6
    const c1y = p1.y + (p2.y - p0.y) / 6
    const c2x = p2.x - (p3.x - p1.x) / 6
    const c2y = p2.y - (p3.y - p1.y) / 6
    d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`
  }
  return d
}

const trendPath = smoothPath(NET)
const flowPath = smoothPath(FLOW)
const areaPath = `${trendPath} L ${CW - PAD_X} ${CH - PAD_Y} L ${PAD_X} ${CH - PAD_Y} Z`
const gridYs = [0.2, 0.45, 0.7, 0.95].map((r) => CH - PAD_Y - r * (CH - PAD_Y * 2))

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

        <div class="chart-card">
          <div class="chart-head">
            <span class="chart-title">资金趋势</span>
            <span class="chart-sub">近 12 个月</span>
          </div>
          <svg class="trend" :viewBox="`0 0 ${CW} ${CH}`" role="img" aria-label="资金趋势示意曲线">
            <defs>
              <linearGradient id="loginArea" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" style="stop-color: var(--primary); stop-opacity: 0.26" />
                <stop offset="100%" style="stop-color: var(--primary); stop-opacity: 0" />
              </linearGradient>
              <linearGradient id="loginStroke" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" style="stop-color: var(--primary)" />
                <stop offset="100%" style="stop-color: var(--accent-ai)" />
              </linearGradient>
            </defs>

            <g class="grid">
              <line v-for="y in gridYs" :key="y" :x1="0" :x2="CW" :y1="y" :y2="y" />
            </g>

            <path class="area" :d="areaPath" fill="url(#loginArea)" />
            <path class="line-flow" :d="flowPath" />
            <path class="line-trend" :d="trendPath" pathLength="1" stroke="url(#loginStroke)" />

            <g class="pulse">
              <circle r="7" class="pulse-halo" />
              <circle r="3" class="pulse-core" />
              <animateMotion :path="trendPath" dur="9s" repeatCount="indefinite" />
            </g>
          </svg>
          <ul class="legend">
            <li><i class="dot dot-net" />资产净值</li>
            <li><i class="dot dot-flow" />现金流</li>
          </ul>
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

/* 资金趋势卡片 */
.chart-card {
  margin-top: 26px;
  padding: 16px 18px 12px;
  border-radius: var(--glass-radius-panel);
  background: var(--glass-1);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.chart-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }
.chart-title { font-size: 13px; font-weight: 600; color: var(--ink-strong); }
.chart-sub { font-size: 11.5px; color: var(--ink-muted); }
.trend { width: 100%; max-width: 100%; height: auto; display: block; overflow: visible; }

.grid line { stroke: var(--border); stroke-width: 1; stroke-dasharray: 2 7; }

.area { opacity: 0; animation: area-in 1s var(--ease-out) .85s forwards; }

.line-flow {
  fill: none;
  stroke: var(--success);
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-dasharray: 5 7;
  opacity: 0;
  animation: flow-in .9s ease .55s forwards;
}

.line-trend {
  fill: none;
  stroke-width: 2.6;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  animation: trend-draw 2.2s var(--ease-out) .1s forwards;
  filter: drop-shadow(0 6px 16px rgba(53, 109, 255, 0.25));
}

.pulse { opacity: 0; animation: flow-in .6s ease 2s forwards; }
.pulse-core { fill: var(--accent-ai); }
.pulse-halo { fill: var(--accent-ai); opacity: .22; }

.legend { display: flex; gap: 16px; margin-top: 6px; }
.legend li { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--ink-muted); }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot-net { background: linear-gradient(135deg, var(--primary), var(--accent-ai)); }
.dot-flow { background: var(--success); }

.points { margin-top: 22px; display: flex; flex-direction: column; gap: 9px; }
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
  .chart-card { backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--glass-2); }
}

@media (max-width: 960px) {
  .login-page { padding: calc(var(--space-4) + var(--safe-top)) var(--space-4) var(--space-5); }
  .stage { grid-template-columns: minmax(0, 1fr); gap: var(--space-5); padding: var(--space-4) 0 0; }
  .stage > * { min-width: 0; }
  .intro h1 { font-size: 30px; }
  .lede { display: none; }
  .points { display: none; }
  .chart-card { margin-top: 18px; padding: 12px 14px 8px; }
  .trend { max-height: 132px; }
  .eyebrow { margin-bottom: 10px; }
}

@media (prefers-reduced-motion: reduce) {
  .line-trend { animation: none; stroke-dashoffset: 0; }
  .area, .line-flow, .pulse { animation: none; opacity: 1; }
  .pulse { display: none; }
  .ambient-blob { animation: none; }
}
</style>
