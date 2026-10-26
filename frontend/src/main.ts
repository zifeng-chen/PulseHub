import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import { setUnauthorizedHandler } from './api/client'
import { useAuthStore } from './stores/auth'
import './styles/tokens.css'
import './styles/base.css'
import './styles/cards.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 会话过期统一跳转登录（11 §3：401 跳转登录）。
// 必须清空 auth store，否则守卫会因「已登录」把用户从登录页弹回首页造成死循环（B-2）。
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.forceLogout()
  if (router.currentRoute.value.path !== '/login') {
    router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

// 渲染期异常兜底：避免未捕获异常导致白屏且无任何提示（L-14）。
app.config.errorHandler = (err) => {
  console.error('[app] 未捕获异常：', err)
}

app.mount('#app')
