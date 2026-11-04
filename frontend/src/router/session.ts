/**
 * 切页 / 切回标签页时的会话续期决策。
 *
 * 抽成独立函数而不是塞在路由守卫里，原因有二：
 * 1. 「token 过期 → 静默续期 → 只有续期失败才登出」是最容易出错的分支，
 *    守卫本身依赖真实 history 与懒加载组件、很难在单测里完整驱动；
 *    单独成函数后这条分支可以被直接断言。
 * 2. 路由守卫与 visibilitychange 两处复用同一份判断，避免两套逻辑漂移。
 */

import { isAccessTokenExpiring, refreshAccessToken, tokenStore } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

export type RenewResult = 'ok' | 'expired'

/**
 * 是否需要走一次续期：已登录，且 access 缺失或临近过期。
 * 正常使用（token 还有效）时恒为 false，切页不会产生任何额外请求。
 */
export function needsRenewal(): boolean {
  const auth = useAuthStore()
  if (!auth.isAuthenticated) return false
  return !tokenStore.access || isAccessTokenExpiring()
}

/**
 * 按需静默续期。
 * - `ok`：无需续期，或续期成功——调用方可继续导航/停留。
 * - `expired`：refresh 也失效，已清空本地会话（user + token），调用方须回登录页。
 */
export async function renewSessionIfNeeded(): Promise<RenewResult> {
  if (!needsRenewal()) return 'ok'
  const renewed = tokenStore.refresh ? await refreshAccessToken() : false
  if (renewed) return 'ok'
  // forceLogout 会把 loaded 置 true，避免守卫把用户从登录页又弹回首页造成 401 死循环
  useAuthStore().forceLogout()
  return 'expired'
}
