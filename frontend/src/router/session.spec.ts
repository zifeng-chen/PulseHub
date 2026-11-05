/**
 * 会话续期决策（登录态体验的核心分支）。
 *
 * 历史 bug：路由守卫只在首次加载时校验会话，之后切菜单不再检查，
 * 于是「菜单随便切、一刷新才掉登录」。这里把「过期 → 静默续期 →
 * 只有续期失败才登出」这条链路逐条钉死。
 *
 * 只 mock 网络续期（refreshAccessToken），tokenStore 与 JWT 解析走真实实现，
 * 保证测的是真实链路而不是替身。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore, type CurrentUser } from '@/stores/auth'
import { refreshAccessToken, tokenStore } from '@/api/client'
import { needsRenewal, renewSessionIfNeeded } from './session'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, refreshAccessToken: vi.fn() }
})

const mockedRefresh = vi.mocked(refreshAccessToken)

function makeJwt(expSecondsFromNow: number): string {
  const b64url = (text: string) => {
    const bytes = new TextEncoder().encode(text)
    let bin = ''
    bytes.forEach((b) => {
      bin += String.fromCharCode(b)
    })
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  }
  const exp = Math.floor(Date.now() / 1000) + expSecondsFromNow
  return `${b64url('{"alg":"HS256","typ":"JWT"}')}.${b64url(JSON.stringify({ sub: 'u1', exp }))}.s1gn4ture`
}

const USER: CurrentUser = {
  id: 'u1',
  username: 'tester',
  display_name: '测试用户',
  is_system_admin: false,
  status: 'active',
  household_id: null,
  role: 'member',
  role_label: '成员',
  actions: [],
}

/** 造一个「已登录」现场：user 在内存里，access/refresh 在 localStorage */
function loginAs(accessExpSeconds: number) {
  const auth = useAuthStore()
  auth.user = { ...USER }
  auth.loaded = true
  tokenStore.set(makeJwt(accessExpSeconds), 'refresh-token')
  return auth
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  mockedRefresh.mockReset()
})

describe('needsRenewal', () => {
  it('未登录时不需要续期', () => {
    expect(needsRenewal()).toBe(false)
  })

  it('token 仍然充裕时不需要续期（正常切页零额外请求）', () => {
    loginAs(600)
    expect(needsRenewal()).toBe(false)
  })

  it('token 已过期时需要续期', () => {
    loginAs(-10)
    expect(needsRenewal()).toBe(true)
  })

  it('access 缺失但 refresh 仍在时需要续期（可自愈，不该直接踢回登录页）', () => {
    const auth = loginAs(600)
    localStorage.removeItem('pulsehub.access_token')
    expect(auth.isAuthenticated).toBe(true)
    expect(needsRenewal()).toBe(true)
  })
})

describe('renewSessionIfNeeded', () => {
  it('token 有效：返回 ok 且不发起续期请求', async () => {
    loginAs(600)
    await expect(renewSessionIfNeeded()).resolves.toBe('ok')
    expect(mockedRefresh).not.toHaveBeenCalled()
  })

  it('token 过期 + 续期成功：返回 ok，会话完整保留', async () => {
    const auth = loginAs(-10)
    mockedRefresh.mockResolvedValue(true)

    await expect(renewSessionIfNeeded()).resolves.toBe('ok')
    expect(mockedRefresh).toHaveBeenCalledTimes(1)
    expect(auth.user).not.toBeNull()
    expect(tokenStore.refresh).toBe('refresh-token')
  })

  it('token 过期 + 续期失败：返回 expired，并清空本地会话', async () => {
    const auth = loginAs(-10)
    mockedRefresh.mockResolvedValue(false)

    await expect(renewSessionIfNeeded()).resolves.toBe('expired')
    expect(auth.user).toBeNull()
    expect(tokenStore.access).toBe('')
    expect(tokenStore.refresh).toBe('')
    // loaded 必须为 true，否则守卫会把用户从登录页又弹回首页，形成 401 死循环
    expect(auth.loaded).toBe(true)
  })

  it('token 过期且没有 refresh token：不发请求，直接 expired', async () => {
    const auth = loginAs(-10)
    localStorage.removeItem('pulsehub.refresh_token')

    await expect(renewSessionIfNeeded()).resolves.toBe('expired')
    expect(mockedRefresh).not.toHaveBeenCalled()
    expect(auth.user).toBeNull()
  })

  it('未登录：返回 ok，且不产生任何登出副作用', async () => {
    const auth = useAuthStore()
    await expect(renewSessionIfNeeded()).resolves.toBe('ok')
    expect(mockedRefresh).not.toHaveBeenCalled()
    expect(auth.loaded).toBe(false)
    expect(auth.user).toBeNull()
  })
})
