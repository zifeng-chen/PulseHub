/**
 * 统一 API 客户端（23 §1）。
 * - 统一响应信封：{ success, data, request_id, message } / { success:false, error }
 * - 自动附带访问令牌、请求编号与来源页面
 * - 401 自动尝试刷新一次；失败则跳转登录
 */

import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'

export interface ApiErrorBody {
  code: string
  message: string
  field_errors: Record<string, string>
  retryable: boolean
  request_id: string
}

export class ApiError extends Error {
  code: string
  status: number
  fieldErrors: Record<string, string>
  retryable: boolean
  requestId: string

  constructor(status: number, body: Partial<ApiErrorBody>) {
    super(body.message || '请求失败')
    this.name = 'ApiError'
    this.status = status
    this.code = body.code || 'UNKNOWN'
    this.fieldErrors = body.field_errors || {}
    this.retryable = Boolean(body.retryable)
    this.requestId = body.request_id || ''
  }
}

export interface ListPayload<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  has_next: boolean
}

const ACCESS_KEY = 'fam.access_token'
const REFRESH_KEY = 'fam.refresh_token'

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_KEY) || ''
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY) || ''
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    // 退出只清除本地会话缓存，不清除业务数据（05 §1）
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

let refreshing: Promise<boolean> | null = null

// 跨标签页互斥锁：避免两个标签各拿同一个 refresh token 调 /auth/refresh，
// 后端是单次轮换，后到者会 401 并清掉先到者刚写入的新 token。
// 锁带时间戳，持有超 10 秒自动释放（标签崩溃/卡死时不会永久卡住别的标签）。
const REFRESH_LOCK_KEY = 'fam.refresh_lock'
function acquireRefreshLock(): boolean {
  const now = Date.now()
  const raw = localStorage.getItem(REFRESH_LOCK_KEY)
  if (raw) {
    const ts = Number(raw) || 0
    if (now - ts < 10000) return false
  }
  localStorage.setItem(REFRESH_LOCK_KEY, String(now))
  return true
}
function releaseRefreshLock() {
  localStorage.removeItem(REFRESH_LOCK_KEY)
}

async function tryRefresh(): Promise<boolean> {
  if (!tokenStore.refresh) return false
  // 别的标签正在刷新：等它完成后直接复用其结果（token 已写入 localStorage）
  if (!refreshing) {
    if (!acquireRefreshLock()) {
      // 等待持有锁的标签刷新完成（最多 10 秒）
      await new Promise((resolve) => setTimeout(resolve, 300))
      return Boolean(tokenStore.access)
    }
    refreshing = axios
      .post('/api/v1/auth/refresh', { refresh_token: tokenStore.refresh })
      .then((res) => {
        const data = res.data?.data
        if (data?.access_token) {
          tokenStore.set(data.access_token, data.refresh_token)
          return true
        }
        return false
      })
      .catch(() => false)
      .finally(() => {
        releaseRefreshLock()
        refreshing = null
      })
  }
  return refreshing
}

/** 供启动时静默续期：只要本地还有 refresh token，就先换一张新 access */
export async function refreshAccessToken(): Promise<boolean> {
  return tryRefresh()
}

http.interceptors.request.use((config) => {
  const token = tokenStore.access
  if (token) config.headers.Authorization = `Bearer ${token}`
  config.headers['X-Source-Page'] = window.location.pathname
  return config
})

async function request<T>(config: AxiosRequestConfig, retry = true): Promise<T> {
  try {
    const res = await http.request(config)
    const body = res.data
    if (body && typeof body === 'object' && 'success' in body && body.success === false) {
      throw new ApiError(res.status, body.error)
    }
    return (body?.data ?? body) as T
  } catch (err) {
    // 业务失败（HTTP 200 + success:false）走 ApiError，不能被下面的 axios 分支误吞成「网络异常」
    if (err instanceof ApiError) throw err
    const axiosErr = err as AxiosError<{ error?: ApiErrorBody }>
    if (axiosErr.response) {
      const status = axiosErr.response.status
      const payload = axiosErr.response.data?.error
      // 只有"不该续期"的端点才跳过刷新，避免 401 循环；
      // /auth/me 必须允许刷新，否则 access 过期后刷新页面会直接掉登录。
      const url = String(config.url || '')
      const noRefresh = ['/auth/refresh', '/auth/login', '/auth/register', '/auth/logout',
        '/auth/change-password', '/auth/verify-password', '/auth/setup'].some((p) => url.includes(p))
      // 这些端点返回 401 只是「操作未通过」（如锁屏密码输错），不代表会话失效，
      // 不能触发全局登出（否则锁屏输错一次密码就把整个会话销毁）。
      const noGlobalUnauthorized = ['/auth/verify-password', '/auth/login', '/auth/refresh']
        .some((p) => url.includes(p))
      if (status === 401 && retry && !noRefresh) {
        const ok = await tryRefresh()
        if (ok) return request<T>(config, false)
      }
      if (status === 401 && !noGlobalUnauthorized) {
        tokenStore.clear()
        onUnauthorized?.()
      }
      throw new ApiError(status, payload || { code: 'HTTP_ERROR', message: axiosErr.message })
    }
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: '网络异常，请检查连接后重试', retryable: true })
  }
}

/**
 * 生成幂等键：同一笔「保存」在重试时必须复用同一个值，后端才能认出是同一次提交。
 * 键放在 config 里随请求走，401 续期重试会复用同一份 config，因此不会换键。
 */
function newIdempotencyKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return uuid
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) => request<T>({ method: 'GET', url, params }),
  // 写接口统一携带幂等键：后端认这个键，重复到达只创建一次（防网络重试/手抖连点）
  post: <T>(url: string, data?: unknown, idempotencyKey?: string) =>
    request<T>({
      method: 'POST', url, data,
      headers: { 'Idempotency-Key': idempotencyKey || newIdempotencyKey() },
    }),
  put: <T>(url: string, data?: unknown) => request<T>({ method: 'PUT', url, data }),
  patch: <T>(url: string, data?: unknown) => request<T>({ method: 'PATCH', url, data }),
  delete: <T>(url: string, params?: Record<string, unknown>, data?: unknown) =>
    request<T>({ method: 'DELETE', url, params, data }),
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return '操作失败，请稍后重试'
}

export function errorFields(err: unknown): Record<string, string> {
  return err instanceof ApiError ? err.fieldErrors : {}
}
