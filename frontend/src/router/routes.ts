/**
 * 路由注册表（23 §7）。
 * 每条路由必须登记中文标题、一级菜单归属、权限、移动端标题与面包屑。
 * 导航选中状态只从本表计算，不从页面默认值计算。
 */

export type MenuKey = 'dashboard' | 'ledger' | 'insights' | 'settings'

export interface AppRouteMeta {
  /** 页面中文标题（浏览器标题与顶栏） */
  title: string
  /** 属于哪个一级菜单；登录/初始化等系统入口不设该字段 */
  menu?: MenuKey
  /** 移动端顶部标题 */
  mobileTitle?: string
  /** 面包屑（中文） */
  breadcrumb?: string[]
  /** 需要的权限动作（23 §6 十类动作） */
  permission?: string
  /** 无需登录的公开路由（登录、安装检测、首次启动） */
  publicRoute?: boolean
  /** 所属模块尚未交付时的说明文案；为空表示该页面在 M0 已可用 */
  pendingModule?: string
}

export interface AppRoute {
  path: string
  name: string
  component: () => Promise<unknown>
  meta: AppRouteMeta
}

/** 系统入口（不计入一级菜单） */
export const systemRoutes: AppRoute[] = [
  {
    path: '/setup/check',
    name: 'setup-check',
    component: () => import('@/views/system/SetupCheckView.vue'),
    meta: { title: '安装检测', publicRoute: true },
  },
  {
    path: '/setup',
    name: 'setup',
    component: () => import('@/views/system/SetupWizardView.vue'),
    meta: { title: '首次启动引导', publicRoute: true },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/system/LoginView.vue'),
    meta: { title: '登录', publicRoute: true },
  },
  {
    path: '/forgot-password',
    name: 'forgot-password',
    component: () => import('@/views/system/ForgotPasswordView.vue'),
    meta: { title: '忘记密码', publicRoute: true },
  },
  {
    path: '/403',
    name: 'forbidden',
    component: () => import('@/views/system/ForbiddenView.vue'),
    meta: { title: '无访问权限' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/system/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

/** 平台路由（应用壳内） */
export const appRoutes: AppRoute[] = [
  // ---- 脉枢台 ----
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/views/DashboardView.vue'),
    meta: { title: '脉枢台', menu: 'dashboard', mobileTitle: '脉枢台', breadcrumb: ['脉枢台'] },
  },

  // ---- 家庭台账 ----
  {
    path: '/ledger',
    name: 'ledger-overview',
    component: () => import('@/views/ledger/LedgerOverviewView.vue'),
    meta: { title: '家庭台账总览', menu: 'ledger', mobileTitle: '家庭台账', breadcrumb: ['家庭台账', '总览'] },
  },
  {
    path: '/ledger/assets',
    name: 'ledger-assets',
    component: () => import('@/views/ledger/AssetsView.vue'),
    meta: {
      title: '资产', menu: 'ledger', mobileTitle: '资产', breadcrumb: ['家庭台账', '资产'],
      permission: 'view',
    },
  },
  {
    path: '/ledger/assets/real-estates',
    name: 'ledger-assets-real-estates',
    component: () => import('@/views/ledger/RealEstatesView.vue'),
    meta: {
      title: '不动产', menu: 'ledger', mobileTitle: '不动产',
      breadcrumb: ['家庭台账', '资产', '不动产'], permission: 'view',
    },
  },
  {
    path: '/ledger/assets/vehicles',
    name: 'ledger-assets-vehicles',
    component: () => import('@/views/ledger/VehiclesView.vue'),
    meta: {
      title: '车辆', menu: 'ledger', mobileTitle: '车辆',
      breadcrumb: ['家庭台账', '资产', '车辆'], permission: 'view',
    },
  },
  {
    path: '/ledger/assets/precious-metals',
    name: 'ledger-assets-precious-metals',
    component: () => import('@/views/ledger/PreciousMetalsView.vue'),
    meta: {
      title: '贵金属', menu: 'ledger', mobileTitle: '贵金属',
      breadcrumb: ['家庭台账', '资产', '贵金属'], permission: 'view',
    },
  },
  {
    path: '/ledger/accounts',
    name: 'ledger-accounts',
    component: () => import('@/views/ledger/AccountsView.vue'),
    meta: {
      title: '账户', menu: 'ledger', mobileTitle: '账户',
      breadcrumb: ['家庭台账', '账户'], permission: 'view',
    },
  },
  {
    path: '/ledger/transactions',
    name: 'ledger-transactions',
    component: () => import('@/views/ledger/TransactionsView.vue'),
    meta: {
      title: '流水', menu: 'ledger', mobileTitle: '流水',
      breadcrumb: ['家庭台账', '流水'], permission: 'view',
    },
  },
  {
    path: '/ledger/reconciliation',
    name: 'ledger-reconciliation',
    component: () => import('@/views/ledger/ReconciliationView.vue'),
    meta: {
      title: '对账', menu: 'ledger', mobileTitle: '对账',
      breadcrumb: ['家庭台账', '账户', '对账'], permission: 'view',
    },
  },
  {
    path: '/ledger/liabilities',
    name: 'ledger-liabilities',
    component: () => import('@/views/ledger/LiabilitiesView.vue'),
    meta: {
      title: '负债', menu: 'ledger', mobileTitle: '负债',
      breadcrumb: ['家庭台账', '负债'], permission: 'view',
    },
  },
  {
    path: '/ledger/credit-cards',
    name: 'ledger-credit-cards',
    component: () => import('@/views/ledger/CreditCardsView.vue'),
    meta: {
      title: '信用卡', menu: 'ledger', mobileTitle: '信用卡',
      breadcrumb: ['家庭台账', '负债', '信用卡'], permission: 'view',
    },
  },
  {
    path: '/ledger/investments',
    name: 'ledger-investments',
    component: () => import('@/views/ledger/InvestmentsView.vue'),
    meta: {
      title: '投资', menu: 'ledger', mobileTitle: '投资',
      breadcrumb: ['家庭台账', '投资'], permission: 'view',
    },
  },
  {
    path: '/ledger/leases',
    name: 'ledger-leases',
    component: () => import('@/views/ledger/LeasesView.vue'),
    meta: {
      title: '租赁', menu: 'ledger', mobileTitle: '租赁',
      breadcrumb: ['家庭台账', '租赁'], permission: 'view',
    },
  },
  {
    path: '/ledger/capture',
    name: 'ledger-capture',
    component: () => import('@/views/ledger/CaptureView.vue'),
    meta: {
      title: '智能录入', menu: 'ledger', mobileTitle: '智能录入',
      breadcrumb: ['家庭台账', '智能录入'], permission: 'view',
    },
  },
  {
    path: '/ledger/reminders',
    name: 'ledger-reminders',
    component: () => import('@/views/ledger/RemindersView.vue'),
    meta: {
      title: '提醒', menu: 'ledger', mobileTitle: '提醒',
      breadcrumb: ['家庭台账', '提醒'], permission: 'view',
    },
  },

  // ---- 脉诊 ----
  {
    path: '/insights',
    name: 'insights-overview',
    component: () => import('@/views/insights/InsightsOverviewView.vue'),
    meta: { title: '综合脉诊', menu: 'insights', mobileTitle: '脉诊', breadcrumb: ['脉诊', '综合脉诊'] },
  },
  {
    path: '/insights/assets',
    name: 'insights-assets',
    component: () => import('@/views/insights/InsightsTopicView.vue'),
    meta: {
      title: '资产分析', menu: 'insights', mobileTitle: '资产分析',
      breadcrumb: ['脉诊', '资产分析'], permission: 'view',
    },
  },
  {
    path: '/insights/income',
    name: 'insights-income',
    component: () => import('@/views/insights/InsightsTopicView.vue'),
    meta: {
      title: '收入分析', menu: 'insights', mobileTitle: '收入分析',
      breadcrumb: ['脉诊', '收入分析'], permission: 'view',
    },
  },
  {
    path: '/insights/expenses',
    name: 'insights-expenses',
    component: () => import('@/views/insights/InsightsTopicView.vue'),
    meta: {
      title: '消费分析', menu: 'insights', mobileTitle: '消费分析',
      breadcrumb: ['脉诊', '消费分析'], permission: 'view',
    },
  },
  {
    path: '/insights/liabilities',
    name: 'insights-liabilities',
    component: () => import('@/views/insights/InsightsTopicView.vue'),
    meta: {
      title: '负债分析', menu: 'insights', mobileTitle: '负债分析',
      breadcrumb: ['脉诊', '负债分析'], permission: 'view',
    },
  },
  {
    path: '/insights/investments',
    name: 'insights-investments',
    component: () => import('@/views/insights/InsightsTopicView.vue'),
    meta: {
      title: '投资分析', menu: 'insights', mobileTitle: '投资分析',
      breadcrumb: ['脉诊', '投资分析'], permission: 'view',
    },
  },
  {
    path: '/insights/leases',
    name: 'insights-leases',
    component: () => import('@/views/insights/InsightsTopicView.vue'),
    meta: {
      title: '租赁分析', menu: 'insights', mobileTitle: '租赁分析',
      breadcrumb: ['脉诊', '租赁分析'], permission: 'view',
    },
  },
  {
    path: '/insights/spending-habits',
    name: 'insights-spending-habits',
    component: () => import('@/views/insights/SpendingHabitsView.vue'),
    meta: {
      title: '消费习惯分析', menu: 'insights', mobileTitle: '消费习惯',
      breadcrumb: ['脉诊', '消费习惯分析'], permission: 'view',
    },
  },
  {
    path: '/insights/data-quality',
    name: 'insights-data-quality',
    component: () => import('@/views/insights/DataQualityView.vue'),
    meta: {
      title: '数据质量', menu: 'insights', mobileTitle: '数据质量',
      breadcrumb: ['脉诊', '数据质量'], permission: 'view',
    },
  },

  // ---- 设置 ----
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/settings/SettingsHomeView.vue'),
    meta: { title: '设置', menu: 'settings', mobileTitle: '设置', breadcrumb: ['设置'] },
  },
  {
    path: '/settings/profile',
    name: 'settings-profile',
    component: () => import('@/views/settings/SettingsProfileView.vue'),
    meta: { title: '个人资料', menu: 'settings', mobileTitle: '个人资料', breadcrumb: ['设置', '个人资料'] },
  },
  {
    path: '/settings/household',
    name: 'settings-household',
    component: () => import('@/views/settings/SettingsHouseholdView.vue'),
    meta: { title: '家庭与成员', menu: 'settings', mobileTitle: '家庭与成员', breadcrumb: ['设置', '家庭与成员'] },
  },
  {
    path: '/settings/employment',
    name: 'settings-employment',
    component: () => import('@/views/settings/SettingsEmploymentView.vue'),
    meta: { title: '工作与收入', menu: 'settings', mobileTitle: '工作与收入', breadcrumb: ['设置', '工作与收入'] },
  },
  {
    path: '/settings/categories',
    name: 'settings-categories',
    component: () => import('@/views/settings/SettingsCategoriesView.vue'),
    meta: { title: '分类管理', menu: 'settings', mobileTitle: '分类管理', breadcrumb: ['设置', '分类管理'] },
  },
  {
    path: '/settings/finance',
    name: 'settings-finance',
    component: () => import('@/views/settings/SettingsFinanceView.vue'),
    meta: { title: '财务口径', menu: 'settings', mobileTitle: '财务口径', breadcrumb: ['设置', '财务口径'] },
  },
  {
    path: '/settings/notifications',
    name: 'settings-notifications',
    component: () => import('@/views/settings/SettingsNotificationsView.vue'),
    meta: { title: '提醒通知', menu: 'settings', mobileTitle: '提醒通知', breadcrumb: ['设置', '提醒通知'] },
  },
  {
    path: '/settings/ocr',
    name: 'settings-ocr',
    component: () => import('@/views/settings/SettingsOcrView.vue'),
    meta: { title: '百度 OCR', menu: 'settings', mobileTitle: '百度 OCR', breadcrumb: ['设置', '百度 OCR'] },
  },
  {
    path: '/settings/beike',
    name: 'settings-beike',
    component: () => import('@/views/settings/SettingsBeikeView.vue'),
    meta: { title: '房产数据（贝壳）', menu: 'settings', mobileTitle: '房产数据', breadcrumb: ['设置', '房产数据（贝壳）'] },
  },
  {
    path: '/settings/local-ai',
    name: 'settings-local-ai',
    component: () => import('@/views/settings/SettingsLocalAiView.vue'),
    meta: { title: '本地 AI', menu: 'settings', mobileTitle: '本地 AI', breadcrumb: ['设置', '本地 AI'] },
  },
  {
    path: '/settings/ai-providers',
    name: 'settings-ai-providers',
    component: () => import('@/views/settings/SettingsAiProvidersView.vue'),
    meta: {
      title: '第三方 AI 接口', menu: 'settings', mobileTitle: '第三方 AI 接口',
      breadcrumb: ['设置', '第三方 AI 接口'],
    },
  },
  {
    path: '/settings/privacy',
    name: 'settings-privacy',
    component: () => import('@/views/settings/SettingsPrivacyView.vue'),
    meta: { title: '隐私与安全', menu: 'settings', mobileTitle: '隐私与安全', breadcrumb: ['设置', '隐私与安全'] },
  },
  {
    path: '/settings/backup',
    name: 'settings-backup',
    component: () => import('@/views/settings/SettingsBackupView.vue'),
    meta: {
      title: '数据备份与恢复', menu: 'settings', mobileTitle: '数据备份与恢复',
      breadcrumb: ['设置', '数据备份与恢复'],
    },
  },
  {
    path: '/settings/system',
    name: 'settings-system',
    component: () => import('@/views/settings/SettingsSystemView.vue'),
    meta: {
      title: '系统状态与审计', menu: 'settings', mobileTitle: '系统状态与审计',
      breadcrumb: ['设置', '系统状态与审计'], permission: 'audit_view',
    },
  },
]

export const allRoutes: AppRoute[] = [...systemRoutes, ...appRoutes]

/** 家庭台账内部页签（06 §1）：内部页不新增一级菜单 */
export const LEDGER_TABS: { label: string; path: string }[] = [
  { label: '总览', path: '/ledger' },
  { label: '账户', path: '/ledger/accounts' },
  { label: '对账', path: '/ledger/reconciliation' },
  { label: '资产', path: '/ledger/assets' },
  { label: '流水', path: '/ledger/transactions' },
  { label: '负债', path: '/ledger/liabilities' },
  { label: '投资', path: '/ledger/investments' },
  { label: '租赁', path: '/ledger/leases' },
  { label: '智能录入', path: '/ledger/capture' },
  { label: '提醒', path: '/ledger/reminders' },
]

/** 脉诊内部页签 */
export const INSIGHT_TABS: { label: string; path: string }[] = [
  { label: '综合脉诊', path: '/insights' },
  { label: '资产分析', path: '/insights/assets' },
  { label: '收入分析', path: '/insights/income' },
  { label: '消费分析', path: '/insights/expenses' },
  { label: '消费习惯', path: '/insights/spending-habits' },
  { label: '负债分析', path: '/insights/liabilities' },
  { label: '投资分析', path: '/insights/investments' },
  { label: '租赁分析', path: '/insights/leases' },
  { label: '数据质量', path: '/insights/data-quality' },
]

/** 设置内部页签 */
export const SETTINGS_TABS: { label: string; path: string }[] = [
  { label: '设置首页', path: '/settings' },
  { label: '家庭与成员', path: '/settings/household' },
  { label: '工作与收入', path: '/settings/employment' },
  { label: '分类管理', path: '/settings/categories' },
  { label: '财务口径', path: '/settings/finance' },
  { label: '提醒通知', path: '/settings/notifications' },
  { label: '百度 OCR', path: '/settings/ocr' },
  { label: '房产数据（贝壳）', path: '/settings/beike' },
  { label: '本地 AI', path: '/settings/local-ai' },
  { label: '第三方 AI 接口', path: '/settings/ai-providers' },
  { label: '隐私与安全', path: '/settings/privacy' },
  { label: '数据备份与恢复', path: '/settings/backup' },
  { label: '系统状态与审计', path: '/settings/system' },
]
