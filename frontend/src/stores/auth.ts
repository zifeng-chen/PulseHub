/** 认证状态：当前用户、权限动作、登录/退出（23 §6）。 */

import { defineStore } from 'pinia'
import { api, refreshAccessToken, tokenStore } from '@/api/client'

export interface CurrentUser {
  id: string
  username: string
  display_name: string
  avatar?: string | null
  email?: string | null
  phone?: string | null
  is_system_admin: boolean
  status: string
  household_id: string | null
  role: string
  role_label: string
  actions: string[]
  last_login_at?: string | null
}

interface LoginResult {
  access_token: string
  refresh_token: string
  session_id: string
  user: CurrentUser
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as CurrentUser | null,
    loaded: false,
    loading: false,
    /** 首次登录欢迎层只显示一次（04 §6） */
    welcomeDismissed: localStorage.getItem('fam.welcome.dismissed') === '1',
  }),
  getters: {
    isAuthenticated: (s) => Boolean(s.user),
    displayName: (s) => s.user?.display_name || '',
    roleLabel: (s) => s.user?.role_label || '',
  },
  actions: {
    can(action: string): boolean {
      return Boolean(this.user?.actions?.includes(action))
    },
    async login(username: string, password: string, rememberDevice: boolean) {
      const data = await api.post<LoginResult>('/auth/login', {
        username,
        password,
        remember_device: rememberDevice,
      })
      tokenStore.set(data.access_token, data.refresh_token)
      this.user = data.user
      this.loaded = true
      return data
    },
    async loadMe() {
      // 只要本地还有 token 就尝试恢复会话。
      if (!tokenStore.access && !tokenStore.refresh) {
        this.user = null
        this.loaded = true
        return null
      }
      this.loading = true
      try {
        // 有 access 就直接用它取 /auth/me，只有 access 缺失或过期（401）才触发 refresh，
        // 避免每次加载都无谓轮换 refresh token（B-3 放大器）。
        try {
          this.user = await api.get<CurrentUser>('/auth/me')
        } catch (err) {
          const status = (err as { status?: number })?.status
          if (status !== 401 || !tokenStore.refresh) throw err
          // access 过期：先刷新再重试一次
          await refreshAccessToken()
          this.user = await api.get<CurrentUser>('/auth/me')
        }
      } catch (err) {
        // 只有明确的 401（认证失效）才清空用户；网络异常（status===0）保留重试机会
        const status = (err as { status?: number })?.status
        if (status === 401) {
          this.user = null
        }
        // 网络错误：不置空 user，保留 loaded=false 以便下次守卫重试
        if (status !== 0) {
          this.user = null
        }
      } finally {
        this.loading = false
        this.loaded = true
      }
      return this.user
    },
    async logout() {
      try {
        await api.post('/auth/logout')
      } catch {
        /* 忽略退出接口异常，本地会话仍需清除 */
      }
      tokenStore.clear()
      this.user = null
      this.loaded = false
    },
    clear() {
      tokenStore.clear()
      this.user = null
    },
    /**
     * 会话失效时的完整登出：清 token + 清 user + 标记 loaded，
     * 否则守卫会因「已登录」把用户从登录页弹回首页，造成 401 死循环（B-2）。
     */
    forceLogout() {
      tokenStore.clear()
      this.user = null
      this.loaded = true
    },
    dismissWelcome() {
      this.welcomeDismissed = true
      localStorage.setItem('fam.welcome.dismissed', '1')
    },
  },
})
