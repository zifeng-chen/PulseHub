/**
 * 一级菜单定义与"基于路由"的选中状态计算（05 §1、11 §7、21 §2）。
 *
 * 硬规则：
 * - 选中状态必须是当前路由的派生状态。
 * - 禁止 activeIndex = 0 之类的默认索引，禁止初始化后不再更新。
 * - 路由不存在 / 权限重定向 / 深层链接 / 刷新 / 前进后退 都要重新计算。
 * - 桌面侧栏与移动底部导航必须共用本文件。
 */

import type { RouteLocationNormalizedLoaded } from 'vue-router'
import type { MenuKey } from './routes'

export interface MenuItem {
  key: MenuKey
  label: string
  icon: string
  home: string
}

/** 唯一四个一级菜单 */
export const MENUS: MenuItem[] = [
  { key: 'dashboard', label: '脉枢台', icon: 'LayoutDashboard', home: '/dashboard' },
  { key: 'ledger', label: '家庭台账', icon: 'Wallet', home: '/ledger' },
  { key: 'insights', label: '脉诊', icon: 'ChartLine', home: '/insights' },
  { key: 'settings', label: '设置', icon: 'Settings', home: '/settings' },
]

/** 路由路径 → 一级菜单（与 23 §7 注册表一致的兜底映射）。 */
export function menuFromPath(path: string): MenuKey | null {
  if (path === '/dashboard' || path.startsWith('/dashboard/')) return 'dashboard'
  if (path === '/ledger' || path.startsWith('/ledger/')) return 'ledger'
  if (path === '/insights' || path.startsWith('/insights/')) return 'insights'
  if (path === '/settings' || path.startsWith('/settings/')) return 'settings'
  return null
}

/**
 * 计算当前一级菜单选中项。
 * 优先读取路由元数据；无元数据时按路径兜底；都不匹配返回 null（不默认选中第一项）。
 */
export function activeMenuOf(route: RouteLocationNormalizedLoaded): MenuKey | null {
  const declared = route.meta?.menu as MenuKey | undefined
  if (declared) return declared
  return menuFromPath(route.path)
}

/** 判断某个一级菜单是否选中 */
export function isMenuActive(route: RouteLocationNormalizedLoaded, key: MenuKey): boolean {
  return activeMenuOf(route) === key
}

/** 当前二级页签（用于家庭台账/脉诊/设置的内部页签高亮） */
export function isTabActive(route: RouteLocationNormalizedLoaded, path: string): boolean {
  return route.path === path
}

/** 顶栏与移动端标题 */
export function titleOf(route: RouteLocationNormalizedLoaded, mobile = false): string {
  const meta = route.meta || {}
  if (mobile && meta.mobileTitle) return meta.mobileTitle as string
  return (meta.title as string) || '财资脉枢'
}

/** 面包屑 */
export function breadcrumbOf(route: RouteLocationNormalizedLoaded): string[] {
  return (route.meta?.breadcrumb as string[]) || []
}
