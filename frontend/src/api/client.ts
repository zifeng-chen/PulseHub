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

const ACCESS_KEY = 'pulsehub.access_token'
const REFRESH_KEY = 'pulsehub.refresh_token'

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

/**
 * AI 请求的独立超时。
 *
 * 本地 4B 模型实测生成约 7.5 tok/s、提示词预填约 50 tok/s：带账本快照的一次
 * 问答通常 30~60 秒，冷加载再加 50 秒，长的能到 100 秒以上。用全局 30 秒会让
 * 前端先放弃，用户看到「网络异常」，而后端其实还在正常算——这是 2026-11-05 早上的故障。
 *
 * 超时链路必须逐层放宽，任何一层比下游短都会先切断：
 *   前端 axios 330s > nginx proxy_read_timeout 300s > 后端 local_ai_configs.timeout 240s
 * 改这里时记得同步 `frontend/nginx.conf` 与数据库里的本地 AI 超时。
 */
export const AI_TIMEOUT = 330000

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

let refreshing: Promise<boolean> | null = null

// 跨标签页互斥锁：避免两个标签各拿同一个 refresh token 调 /auth/refresh，
// 后端是单次轮换，后到者会 401 并清掉先到者刚写入的新 token。
// 锁带时间戳，持有超 10 秒自动释放（标签崩溃/卡死时不会永久卡住别的标签）。
const REFRESH_LOCK_KEY = 'pulsehub.refresh_lock'
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
      // 显式给续期请求设超时：路由守卫会 await 它，后端无响应时不能让切页无限卡住。
      // 取 20s 而非更短：实测 /auth/refresh 偶发 10~11s（NAS 上 MySQL 慢查询），
      // 超时设太紧会把「慢但成功」的续期误判为失败，反而把用户踢下线。
      .post('/api/v1/auth/refresh', { refresh_token: tokenStore.refresh }, { timeout: 20000 })
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

/**
 * 本地判断 access token 是否已过期或即将过期（默认提前 60 秒视为过期）。
 * 只解 JWT payload 的 exp，不验签——前端没有任何验签能力，这里仅用于
 * 「该不该在切页时提前续期」的本地决策；真正的有效性仍以服务端 401 为准。
 * 解析失败（不是 JWT）时返回 false，交给 401 兜底，避免误判把用户踢下线。
 */
export function isAccessTokenExpiring(bufferSeconds = 60): boolean {
  const token = tokenStore.access
  if (!token) return false
  try {
    const part = token.split('.')[1]
    if (!part) return false
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/')
    // 标准 base64url → 百分号编码 → UTF-8 解码，避免 payload 含多字节字符时 JSON.parse 失败
    const json = decodeURIComponent(
      Array.from(atob(b64), (ch) => '%' + ('00' + ch.charCodeAt(0).toString(16)).slice(-2)).join(''),
    )
    const exp = (JSON.parse(json) as { exp?: number })?.exp
    if (typeof exp !== 'number') return false
    return exp * 1000 - Date.now() <= bufferSeconds * 1000
  } catch {
    return false
  }
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
    // 超时和「网线断了」要分开说：本地模型慢导致的超时，让用户去查网络
    // 只会白折腾。axios 在超时/主动中断时给 ECONNABORTED。
    const code = (axiosErr as { code?: string }).code
    if (code === 'ECONNABORTED' || code === 'ETIMEDOUT') {
      const ai = String(config.url || '').includes('/ai/')
        || String(config.url || '').includes('ai-analysis')
      throw new ApiError(0, {
        code: 'TIMEOUT',
        message: ai
          ? '等待超时：本地模型这次算得太久（通常 30~60 秒），可重试一次或切换到第三方模型'
          : '请求超时，服务器响应过慢，请稍后重试',
        retryable: true,
      })
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
  // config 用于按请求覆盖超时等选项（AI 端点需要 AI_TIMEOUT，见文件顶部说明）
  get: <T>(url: string, params?: Record<string, unknown>, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'GET', url, params }),
  // 写接口统一携带幂等键：后端认这个键，重复到达只创建一次（防网络重试/手抖连点）
  post: <T>(url: string, data?: unknown, idempotencyKey?: string, config?: AxiosRequestConfig) =>
    request<T>({
      ...config, method: 'POST', url, data,
      headers: { ...(config?.headers || {}), 'Idempotency-Key': idempotencyKey || newIdempotencyKey() },
    }),
  put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'PUT', url, data }),
  patch: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'PATCH', url, data }),
  delete: <T>(url: string, params?: Record<string, unknown>, data?: unknown) =>
    request<T>({ method: 'DELETE', url, params, data }),
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    // 服务端让用户「记录请求编号」，就必须把编号显示出来——否则用户无从记录，
    // 只能说一句「它报错了」。只在 5xx 或文案本身提到请求编号时附加，避免噪音。
    if (err.requestId && (err.status >= 500 || err.message.includes('请求编号'))) {
      return `${err.message}（请求编号 ${err.requestId}）`
    }
    return err.message
  }
  if (err instanceof Error) return err.message
  return '操作失败，请稍后重试'
}

export function errorFields(err: unknown): Record<string, string> {
  return err instanceof ApiError ? err.fieldErrors : {}
}
