/**
 * access token 过期判断（会话续期的前提条件）。
 *
 * 「切菜单不校验、刷新才掉登录」这个 bug 就出在这条链路上，所以这里把
 * JWT exp 解析的边界钉死：解析失败必须返回 false（交给 401 兜底），
 * 绝不能因为一段脏 token 就把用户判成已过期。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { ApiError, errorMessage, isAccessTokenExpiring, tokenStore } from './client'

/** 按 JWT 规范拼令牌：header.payload.signature，仅 payload 有语义 */
function makeJwt(payload: Record<string, unknown>): string {
  const b64url = (text: string) => {
    const bytes = new TextEncoder().encode(text)
    let bin = ''
    bytes.forEach((b) => {
      bin += String.fromCharCode(b)
    })
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  }
  return `${b64url('{"alg":"HS256","typ":"JWT"}')}.${b64url(JSON.stringify(payload))}.s1gn4ture`
}

const nowSec = () => Math.floor(Date.now() / 1000)

afterEach(() => {
  tokenStore.clear()
})

describe('isAccessTokenExpiring', () => {
  it('没有 token 时返回 false（交给 401 兜底，不主动踢人）', () => {
    expect(isAccessTokenExpiring()).toBe(false)
  })

  it('已过期的 token 返回 true', () => {
    tokenStore.set(makeJwt({ sub: 'u1', exp: nowSec() - 10 }), 'r')
    expect(isAccessTokenExpiring()).toBe(true)
  })

  it('落在 60 秒缓冲内即视为即将过期', () => {
    tokenStore.set(makeJwt({ sub: 'u1', exp: nowSec() + 30 }), 'r')
    expect(isAccessTokenExpiring()).toBe(true)
  })

  it('仍然充裕的 token 返回 false', () => {
    tokenStore.set(makeJwt({ sub: 'u1', exp: nowSec() + 5 * 60 }), 'r')
    expect(isAccessTokenExpiring()).toBe(false)
  })

  it('缓冲可自定义：剩 200 秒时，300 秒缓冲判过期、60 秒缓冲判有效', () => {
    tokenStore.set(makeJwt({ sub: 'u1', exp: nowSec() + 200 }), 'r')
    expect(isAccessTokenExpiring(300)).toBe(true)
    expect(isAccessTokenExpiring(60)).toBe(false)
  })

  it('payload 里没有 exp 时返回 false', () => {
    tokenStore.set(makeJwt({ sub: 'u1' }), 'r')
    expect(isAccessTokenExpiring()).toBe(false)
  })

  it('不是 JWT 的脏数据返回 false 且不抛错', () => {
    tokenStore.set('not-a-jwt', 'r')
    expect(() => isAccessTokenExpiring()).not.toThrow()
    expect(isAccessTokenExpiring()).toBe(false)
  })

  it('payload 含多字节字符也能正确解析', () => {
    tokenStore.set(makeJwt({ name: '财资脉枢', exp: nowSec() - 1 }), 'r')
    expect(isAccessTokenExpiring()).toBe(true)
  })
})

describe('errorMessage 的请求编号', () => {
  it('5xx 且后端给了 request_id 时，把编号显示出来', () => {
    const err = new ApiError(500, {
      message: '服务内部错误，请记录请求编号后联系管理员',
      request_id: 'req-abc-123',
    })
    expect(errorMessage(err)).toContain('req-abc-123')
  })

  it('4xx 业务错误不附带编号，避免噪音', () => {
    const err = new ApiError(422, {
      message: '金额格式不正确',
      request_id: 'req-def-456',
    })
    expect(errorMessage(err)).toBe('金额格式不正确')
  })
})
