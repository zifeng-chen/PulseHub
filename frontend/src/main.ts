import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { migrateLegacyStorage } from './utils/storage-migration'
import App from './App.vue'
import { router } from './router'
import { setUnauthorizedHandler } from './api/client'
import { needsRenewal, renewSessionIfNeeded } from './router/session'
import { useAuthStore } from './stores/auth'
import { useThemeStore } from './stores/theme'
import './styles/tokens.css'
import './styles/base.css'
import './styles/cards.css'

// 必须在任何模块读取 localStorage 之前执行：把旧的 fam.* 偏好与登录态搬到 pulsehub.*
migrateLegacyStorage()

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 首屏前先落 data-theme：登录页跟随主题后，晚设置会让深色用户看到一瞬间白底
useThemeStore().init()

// 会话过期统一跳转登录（11 §3：401 跳转登录）。
// 必须清空 auth store，否则守卫会因「已登录」把用户从登录页弹回首页造成死循环（B-2）。
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.forceLogout()
  if (router.currentRoute.value.path !== '/login') {
    router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

/**
 * 切回标签页时静默续期：手机上常见「放着不管几十分钟再切回来」，
 * 此时 access 早已过期，但页面不导航就不会触发路由守卫。
 * 只在需要续期时才发一次刷新请求，正常使用时零额外开销。
 */
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return
  if (!needsRenewal()) return
  void (async () => {
    if ((await renewSessionIfNeeded()) !== 'expired') return
    // 续期失败 = refresh 也失效，按会话过期处理，回登录页并保留来源
    if (router.currentRoute.value.path !== '/login') {
      router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
    }
  })()
})

// 渲染期异常兜底：避免未捕获异常导致白屏且无任何提示（L-14）。
app.config.errorHandler = (err) => {
  console.error('[app] 未捕获异常：', err)
}

app.mount('#app')
