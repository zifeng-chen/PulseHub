/**
 * 主题：浅色 / 深色 / 跟随系统（03 §2.1、08 §1.1、17 §2）。
 * 切换只过渡颜色、边框、阴影和背景，不刷新页面、不丢失表单、不影响数据请求。
 */

import { defineStore } from 'pinia'
import { api } from '@/api/client'

export type ThemeMode = 'light' | 'dark' | 'system'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    mode: (localStorage.getItem('fam.theme') as ThemeMode) || 'system',
    systemDark: window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
    reducedMotion: false,
    bound: false,
  }),
  getters: {
    resolved(state): 'light' | 'dark' {
      if (state.mode === 'system') return state.systemDark ? 'dark' : 'light'
      return state.mode
    },
  },
  actions: {
    bindSystemListener() {
      if (this.bound) return
      const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
      mq?.addEventListener('change', (e) => {
        this.systemDark = e.matches
        this.apply()
      })
      this.bound = true
    },
    apply() {
      const root = document.documentElement
      root.setAttribute('data-theme', this.resolved)
      root.setAttribute('data-reduced-motion', this.reducedMotion ? 'true' : 'false')
      const meta = document.querySelector('meta[name="theme-color"]')
      if (meta) meta.setAttribute('content', this.resolved === 'dark' ? '#0A1222' : '#F4F7FC')
    },
    setMode(mode: ThemeMode, persistToServer = false) {
      this.mode = mode
      localStorage.setItem('fam.theme', mode)
      this.apply()
      if (persistToServer) {
        api.put('/settings/preferences', { theme: mode }).catch(() => {
          /* 主题属于个人偏好，服务端失败不阻塞本地切换 */
        })
      }
    },
    setReducedMotion(value: boolean) {
      this.reducedMotion = value
      this.apply()
    },
    async loadFromServer() {
      try {
        const prefs = await api.get<{ theme: ThemeMode; reduced_motion: boolean }>('/settings/preferences')
        if (prefs?.theme) {
          this.mode = prefs.theme
          localStorage.setItem('fam.theme', prefs.theme)
        }
        if (typeof prefs?.reduced_motion === 'boolean') this.reducedMotion = prefs.reduced_motion
      } catch {
        /* 未登录或接口失败时沿用本地偏好 */
      }
      this.apply()
    },
    init() {
      this.bindSystemListener()
      this.apply()
    },
  },
})
