import { beforeEach, describe, expect, it } from 'vitest'

import { migrateLegacyStorage } from './storage-migration'

beforeEach(() => {
  localStorage.clear()
})

describe('migrateLegacyStorage', () => {
  it('把 fam.* 全部搬到 pulsehub.*，并删除旧键', () => {
    localStorage.setItem('fam.theme', 'dark')
    localStorage.setItem('fam.access_token', 'token-1')
    localStorage.setItem('fam.ai.float', '1')

    expect(migrateLegacyStorage()).toBe(3)

    expect(localStorage.getItem('pulsehub.theme')).toBe('dark')
    expect(localStorage.getItem('pulsehub.access_token')).toBe('token-1')
    expect(localStorage.getItem('pulsehub.ai.float')).toBe('1')
    expect(localStorage.getItem('fam.theme')).toBeNull()
    expect(localStorage.getItem('fam.access_token')).toBeNull()
  })

  it('新键已存在时不覆盖（以当前版本的值为准）', () => {
    localStorage.setItem('fam.theme', 'dark')
    localStorage.setItem('pulsehub.theme', 'light')

    migrateLegacyStorage()

    expect(localStorage.getItem('pulsehub.theme')).toBe('light')
    expect(localStorage.getItem('fam.theme')).toBeNull()
  })

  it('不碰 family.* 等只是前缀相同的键', () => {
    localStorage.setItem('family.theme', 'dark')
    localStorage.setItem('other', 'x')

    expect(migrateLegacyStorage()).toBe(0)
    expect(localStorage.getItem('family.theme')).toBe('dark')
    expect(localStorage.getItem('other')).toBe('x')
  })

  it('没有旧键时是空操作', () => {
    expect(migrateLegacyStorage()).toBe(0)
  })
})
