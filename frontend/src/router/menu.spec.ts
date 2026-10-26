/**
 * 导航回归测试（21 §5、18 §3 第 6 项）。
 * 一级菜单选中状态必须由当前路由派生，且绝不能默认选中第一个菜单。
 */
import { describe, expect, it } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'
import { activeMenuOf, isMenuActive, MENUS, menuFromPath, titleOf } from './menu'
import { allRoutes, LEDGER_TABS, INSIGHT_TABS } from './routes'

function fakeRoute(path: string, meta: Record<string, unknown> = {}) {
  return { path, meta } as never
}

describe('一级菜单定义', () => {
  it('只有四个一级菜单，顺序为 脉枢台 / 家庭台账 / 脉诊 / 设置', () => {
    expect(MENUS.map((m) => m.key)).toEqual(['dashboard', 'ledger', 'insights', 'settings'])
    expect(MENUS.map((m) => m.label)).toEqual(['脉枢台', '家庭台账', '脉诊', '设置'])
  })

  it('不存在盘点、位置、导出、后台任务、AI 一级菜单', () => {
    const labels = MENUS.map((m) => m.label).join('|')
    for (const forbidden of ['盘点', '位置', '导出', '后台任务', 'Worker', 'AI']) {
      expect(labels).not.toContain(forbidden)
    }
  })
})

describe('基于路由的选中状态', () => {
  it('由路由元数据决定选中项', () => {
    expect(activeMenuOf(fakeRoute('/settings/ai-providers', { menu: 'settings' }))).toBe('settings')
    expect(activeMenuOf(fakeRoute('/ledger/liabilities', { menu: 'ledger' }))).toBe('ledger')
    expect(activeMenuOf(fakeRoute('/insights/assets', { menu: 'insights' }))).toBe('insights')
    expect(activeMenuOf(fakeRoute('/dashboard', { menu: 'dashboard' }))).toBe('dashboard')
  })

  it('无元数据时按路径兜底', () => {
    expect(menuFromPath('/ledger/assets/real-estates')).toBe('ledger')
    expect(menuFromPath('/insights/data-quality')).toBe('insights')
    expect(menuFromPath('/settings/backup')).toBe('settings')
    expect(menuFromPath('/dashboard')).toBe('dashboard')
  })

  it('未知路由不选中任何一级菜单（不得错误选中第一个）', () => {
    expect(activeMenuOf(fakeRoute('/unknown/path'))).toBeNull()
    expect(menuFromPath('/unknown/path')).toBeNull()
    expect(menuFromPath('/')).toBeNull()
    expect(menuFromPath('/login')).toBeNull()
    expect(menuFromPath('/setup/check')).toBeNull()
    for (const m of MENUS) {
      expect(isMenuActive(fakeRoute('/unknown/path'), m.key)).toBe(false)
    }
  })

  it('同一路由下只有一个一级菜单被选中', () => {
    for (const path of ['/dashboard', '/ledger/assets', '/insights', '/settings/system']) {
      const route = fakeRoute(path)
      const active = MENUS.filter((m) => isMenuActive(route, m.key))
      expect(active).toHaveLength(1)
    }
  })
})

describe('路由注册表', () => {
  it('每个平台路由都登记一级菜单与中文标题', () => {
    const appRoutes = allRoutes.filter((r) => r.meta.menu)
    expect(appRoutes.length).toBeGreaterThan(20)
    for (const r of appRoutes) {
      expect(r.meta.title).toBeTruthy()
      expect(['dashboard', 'ledger', 'insights', 'settings']).toContain(r.meta.menu)
    }
  })

  it('路由注册表与 23 §7 规定的一级菜单归属一致', () => {
    for (const r of allRoutes) {
      const menu = r.meta.menu
      if (!menu) continue
      if (menu === 'dashboard') expect(r.path).toBe('/dashboard')
      if (menu === 'ledger') expect(r.path.startsWith('/ledger')).toBe(true)
      if (menu === 'insights') expect(r.path.startsWith('/insights')).toBe(true)
      if (menu === 'settings') expect(r.path.startsWith('/settings')).toBe(true)
    }
  })

  it('系统入口（登录 / 安装检测 / 首次启动）不计入一级菜单', () => {
    for (const path of ['/login', '/setup', '/setup/check', '/forgot-password']) {
      const r = allRoutes.find((x) => x.path === path)
      expect(r).toBeTruthy()
      expect(r?.meta.menu).toBeUndefined()
      expect(r?.meta.publicRoute).toBe(true)
    }
  })

  it('家庭台账与脉诊内部页使用页签，不新增一级菜单', () => {
    expect(LEDGER_TABS.length).toBeGreaterThan(0)
    expect(INSIGHT_TABS.length).toBeGreaterThan(0)
    for (const t of [...LEDGER_TABS, ...INSIGHT_TABS]) {
      expect(menuFromPath(t.path)).not.toBeNull()
    }
  })
})

describe('真实路由实例解析后仍派生正确菜单', () => {
  it('解析深层链接、刷新与前进后退时选中状态跟随路由', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: allRoutes.map((r) => ({
        path: r.path,
        name: r.name,
        component: { template: '<div />' },
        meta: { ...r.meta },
      })),
    })

    const cases: [string, string | null][] = [
      ['/settings/ai-providers', 'settings'],
      ['/ledger/liabilities', 'ledger'],
      ['/insights/assets', 'insights'],
      ['/dashboard', 'dashboard'],
      ['/ledger/assets/real-estates', 'ledger'],
      ['/settings', 'settings'],
    ]

    for (const [path, expected] of cases) {
      await router.push(path)
      await router.isReady()
      const resolved = router.currentRoute.value
      expect(activeMenuOf(resolved)).toBe(expected)
      expect((resolved.meta as Record<string, unknown>).menu).toBe(expected)
      expect(titleOf(resolved)).toBeTruthy()
    }
  })

  it('未知路由解析为 not-found，且不选中任何一级菜单', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: allRoutes.map((r) => ({
        path: r.path,
        name: r.name,
        component: { template: '<div />' },
        meta: { ...r.meta },
      })),
    })
    await router.push('/definitely-not-a-page')
    await router.isReady()
    const resolved = router.currentRoute.value
    expect(resolved.name).toBe('not-found')
    expect(activeMenuOf(resolved)).toBeNull()
  })
})
