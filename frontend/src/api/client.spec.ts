/**
 * access token 过期判断（会话续期的前提条件）。
 *
 * 「切菜单不校验、刷新才掉登录」这个 bug 就出在这条链路上，所以这里把
 * JWT exp 解析的边界钉死：解析失败必须返回 false（交给 401 兜底），
 * 绝不能因为一段脏 token 就把用户判成已过期。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { isAccessTokenExpiring, tokenStore } from './client'

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
