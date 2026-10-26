/**
 * 路由实例与导航守卫。
 * 路由元数据由 routes.ts 集中登记（23 §7），导航状态全部由路由派生（21 §2）。
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { allRoutes } from './routes'
import { useAuthStore } from '@/stores/auth'
import { useSystemStore } from '@/stores/system'
import { tokenStore } from '@/api/client'

const records = allRoutes.map((r) => ({
  path: r.path,
  name: r.name,
  component: r.component as RouteRecordRaw['component'],
  meta: { ...r.meta } as RouteRecordRaw['meta'],
})) as RouteRecordRaw[]

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // 根路径直接进脉枢台：此前没有这条，访问域名会落到无匹配路由
    { path: '/', redirect: '/dashboard' },
    ...records,
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const system = useSystemStore()
  const auth = useAuthStore()

  if (!system.checked) {
    await system.loadStatus()
  }

  // 未初始化：除初始化与安装检测外一律引导到首次启动
  if (!system.initialized) {
    if (to.path.startsWith('/setup') || to.path === '/login') return true
    return { path: '/setup/check', query: { redirect: to.fullPath } }
  }

  // 已初始化后关闭初始化引导入口（04 §7），但保留只读的「安装检测/系统状态」页
  if (to.path.startsWith('/setup') && to.path !== '/setup/check') {
    return { path: auth.isAuthenticated ? '/dashboard' : '/login' }
  }

  // 先尝试从本地 token 恢复登录态：否则刷新后 Pinia 状态重置、user 为空，
  // 会被下方 isAuthenticated 误判为未登录而直接踢回登录页（loadMe 永远走不到）。
  if (!auth.loaded) {
    await auth.loadMe()
  }

  const isPublic = Boolean(to.meta?.publicRoute)
  if (isPublic) {
    // 有 user 且有 access token 才算真正登录；否则可能是 store 残留（token 已被清）
    if (to.path === '/login' && auth.isAuthenticated && tokenStore.access) {
      return { path: '/dashboard' }
    }
    return true
  }

  if (!auth.isAuthenticated || !tokenStore.access) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  const required = to.meta?.permission as string | undefined
  if (required && !auth.can(required)) {
    return { path: '/403', query: { from: to.fullPath } }
  }

  return true
})

router.afterEach((to) => {
  const title = (to.meta?.title as string) || ''
  document.title = title ? `财资脉枢 · ${title}` : '财资脉枢'
})

/**
 * 懒加载页面 chunk 取不到时（重新发布后旧 chunk 失效、弱网、代理缓存），
 * 单页内无法恢复，只能整页导航到目标地址，避免停在原页面。
 */
router.onError((error, to) => {
  const message = String((error as Error)?.message || error || '')
  if (/dynamically imported module|Importing a module script failed|Loading chunk|Failed to fetch/i.test(message)) {
    window.location.assign(to?.fullPath || '/dashboard')
  }
})
