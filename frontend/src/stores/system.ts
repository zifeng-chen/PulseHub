/** 系统状态：初始化判定、健康检查与枚举字典。 */

import { defineStore } from 'pinia'
import { api } from '@/api/client'
import { mergeDictionary } from '@/i18n'

export interface HealthInfo {
  app_name: string
  app_version: string
  env: string
  database: { ok: boolean; label: string; latency_ms: number; error?: string | null }
  file_storage: { ok: boolean; path: string }
  server_time: string
}

export const useSystemStore = defineStore('system', {
  state: () => ({
    checked: false,
    initialized: false,
    health: null as HealthInfo | null,
    backendReachable: true,
    dictionaryLoaded: false,
  }),
  actions: {
    async loadStatus() {
      try {
        const status = await api.get<{ initialized: boolean; next: string }>('/setup/status')
        this.initialized = status.initialized
        this.backendReachable = true
      } catch {
        this.backendReachable = false
        this.initialized = true // 后端不可达时不要误判为"未初始化"，交由错误状态提示
      } finally {
        this.checked = true
      }
    },
    async loadHealth() {
      try {
        this.health = await api.get<HealthInfo>('/health')
        this.backendReachable = true
      } catch {
        this.backendReachable = false
      }
      return this.health
    },
    async loadDictionary() {
      if (this.dictionaryLoaded) return
      try {
        const data = await api.get<{ registry: Record<string, Record<string, string>> }>('/meta/enums')
        mergeDictionary(data.registry)
        this.dictionaryLoaded = true
      } catch {
        /* 字典加载失败时回退内置中文映射 */
      }
    },
    reset() {
      this.checked = false
      this.initialized = false
    },
  },
})
