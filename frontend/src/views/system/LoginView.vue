<script setup lang="ts">
/** 登录页 /login（04 §4、17 §7）：全屏深色玻璃空间 + 单卡片登录。 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, ArrowRight, ServerCog } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useSystemStore } from '@/stores/system'
import { useThemeStore } from '@/stores/theme'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'

const router = useRouter()
const route = useRoute()
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
const installTipDismissed = ref(localStorage.getItem('fam.ios.tip') === '1')
const showInstallTip = computed(() => isIOS && !isStandalone && !installTipDismissed.value)

function dismissInstallTip() {
  installTipDismissed.value = true
  localStorage.setItem('fam.ios.tip', '1')
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
  <div class="login-page" :class="{ 'force-dark': theme.resolved === 'light' }">
    <div class="ambient-layer" aria-hidden="true">
      <span class="ambient-blob blob-1" />
      <span class="ambient-blob blob-2" />
      <span class="ambient-blob blob-3" />
      <span class="ambient-blob blob-4" />
    </div>

    <section class="brand-side">
      <div class="brand">
        <img src="/logo.png" alt="财资脉枢" class="logo" />
        <div>
          <h1>财资脉枢</h1>
          <p>PulseHub · 家庭资产与财务智能分析平台</p>
          <p class="slogan">资产有数，资金有脉。</p>
        </div>
      </div>
      <p class="value">
        把家庭成员、资产、账户、收支、负债、投资与租赁统一成可追溯的财务事实，
        并由本地 AI 在权限范围内进行解释与分析。
      </p>
      <ul class="points">
        <li><Lock :size="15" /> 本地优先部署，数据默认不出网</li>
        <li><ServerCog :size="15" /> 后端确定性统计，AI 只做解释</li>
      </ul>
    </section>

    <section class="card">
      <header class="card-head">
        <h2>登录</h2>
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
  </div>
</template>

<style scoped>
/* 登录页使用固定深色玻璃空间（17 §7），与全局主题解耦 */
.login-page {
  position: relative;
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 460px);
  gap: var(--space-7);
  align-items: center;
  padding: calc(var(--space-7) + var(--safe-top)) var(--space-7) var(--space-7);
  overflow: hidden;
  background: radial-gradient(1200px 800px at 10% 0%, #102346 0%, #0A1222 46%, #050A16 100%);
  color: #E8EFFF;
}
/* 氛围光斑：柔和、无边界、缓慢漂浮（替代此前的矩形光带） */
.blob-1 {
  width: 540px; height: 540px; top: -170px; left: -10%;
  background: radial-gradient(circle, rgba(53, 109, 255, 0.52), transparent 70%);
  animation-duration: 34s;
}
.blob-2 {
  width: 470px; height: 470px; bottom: -190px; right: -8%;
  background: radial-gradient(circle, rgba(124, 92, 252, 0.48), transparent 70%);
  animation-duration: 44s; animation-delay: -10s;
}
.blob-3 {
  width: 400px; height: 400px; top: 26%; left: 44%;
  background: radial-gradient(circle, rgba(38, 214, 214, 0.3), transparent 70%);
  animation-duration: 52s; animation-delay: -22s;
}
.blob-4 {
  width: 330px; height: 330px; bottom: 6%; left: 8%;
  background: radial-gradient(circle, rgba(53, 109, 255, 0.26), transparent 70%);
  animation-duration: 40s; animation-delay: -28s;
}

.brand-side { position: relative; z-index: 2; max-width: 560px; }
.brand { display: flex; align-items: center; gap: var(--space-4); }
.logo {
  width: 58px; height: 58px; object-fit: contain;
  filter: drop-shadow(0 0 26px rgba(111, 227, 224, 0.42));
}
.brand h1 { font-size: 26px; color: #F2F6FF; letter-spacing: 1px; }
.brand p { font-size: 13px; color: #9FB0CC; }
.brand .slogan {
  margin-top: 6px; font-size: 13.5px; letter-spacing: 1px;
  color: #C8B47A; font-weight: 500;
}
.value { margin-top: var(--space-5); font-size: 14px; line-height: 1.9; color: #B7C6E0; }
.points { margin-top: var(--space-5); display: flex; flex-direction: column; gap: 10px; }
.points li { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #8FA3C4; }

.card {
  position: relative;
  z-index: 2;
  width: 100%;
  padding: var(--space-6);
  border-radius: var(--glass-radius-panel);
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.16);
  box-shadow: 0 30px 90px rgba(2, 8, 22, 0.62), inset 0 1px 0 rgba(255, 255, 255, 0.22);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}
.card-head h2 { font-size: 20px; color: #F2F6FF; }
.offline { margin-top: 6px; font-size: 12.5px; color: #F4A62A; }
.form { margin-top: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }

.remember { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #B7C6E0; }
.remember input { accent-color: #7C5CFC; }
.install-tip {
  margin-top: var(--space-4); padding: var(--space-3) var(--space-4);
  border-radius: 12px; border: 1px solid rgba(124, 92, 252, 0.28);
  background: rgba(124, 92, 252, 0.10);
}
.install-tip p { margin: 0 0 8px; font-size: 12.5px; line-height: 1.6; color: #C9D6EE; }
.tip-dismiss {
  border: 0; background: transparent; color: #9C8BFF; font-size: 12.5px;
  cursor: pointer; padding: 0;
}

.state { font-size: 13px; color: #F4A62A; }
.state.danger { color: #FF8EA0; }

.card-foot {
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}
.card-foot a { color: #9FC0FF; }

/* 登录页内部输入控件在深色背景下的适配 */
.login-page :deep(.lbl) { color: #B7C6E0; }
.login-page :deep(.input) {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.16);
  color: #F2F6FF;
}
.login-page :deep(.input::placeholder) { color: #7C8CA8; }
.login-page :deep(.err) { color: #FF8EA0; }

@media (max-width: 960px) {
  .login-page { grid-template-columns: 1fr; gap: var(--space-6); padding: calc(var(--space-6) + var(--safe-top)) var(--space-4) var(--space-6); }
  .brand-side { max-width: none; }
  .points { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .band { animation: none; left: 0; width: 100%; opacity: 0.4; }
}
</style>
