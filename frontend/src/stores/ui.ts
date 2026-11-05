/** 界面状态：侧栏折叠、AI 侧栏、确认弹窗。 */

import { defineStore } from 'pinia'

export interface ConfirmOptions {
  title: string
  message: string
  detail?: string[]
  confirmText?: string
  cancelText?: string
  danger?: boolean
  irreversible?: boolean
  requireReason?: boolean
  reasonLabel?: string
}

export const useUiStore = defineStore('ui', {
  state: () => ({
    sidebarCollapsed: localStorage.getItem('pulsehub.sidebar.collapsed') === '1',
    aiPanelOpen: false,
    /** 面板里展示的「上下文」短标签（如「AI 现金流分析」） */
    aiPanelContext: '' as string,
    /** 真正发给模型的上下文正文（可含刚生成的解读与关键指标），比标签长得多 */
    aiPanelBrief: '' as string,
    /** 每次打开自增：面板据此判断「又被打开了」，即使内容相同也能触发 */
    aiPanelNonce: 0,
    /** 本次打开是否要开一个新对话（从「接着问」进来时必须新开，避免和旧话题串味） */
    aiPanelFresh: false,
    /** 页面级 AI 分析（就地生成并展示，不再跳转 AI 助手） */
    aiAnalysisOpen: false,
    aiAnalysisScope: 'dashboard' as string,
    aiAnalysisTitle: '' as string,
    aiAnalysisPeriod: 'current_month' as string,
    aiAnalysisTargetId: '' as string,
    mobileMenuOpen: false,
    welcomeVisible: false,
    /** 金额显示精度（位）：跟随后端家庭设置，默认两位（08 §4）。 */
    amountPrecision: 2,
    /** AI 模型偏好（全局）：auto=本地优先 / local=仅本地 / third_party=第三方。 */
    aiModel: (localStorage.getItem('pulsehub.ai.model') || 'auto') as 'auto' | 'local' | 'third_party',
    confirm: null as (ConfirmOptions & { resolve: (v: { ok: boolean; reason?: string }) => void }) | null,
  }),
  actions: {
    /** 设置金额显示精度（非法值回落为两位）。 */
    setAmountPrecision(value: unknown) {
      const n = Number(value)
      this.amountPrecision = Number.isFinite(n) && n >= 0 && n <= 4 ? Math.trunc(n) : 2
    },
    /** 设置 AI 模型偏好并持久化（全局切换：所有 AI 调用读取此值）。 */
    setAiModel(value: 'auto' | 'local' | 'third_party') {
      if (!['auto', 'local', 'third_party'].includes(value)) value = 'auto'
      this.aiModel = value
      localStorage.setItem('pulsehub.ai.model', value)
    },
    /** 循环切换：自动 → 仅本地 → 第三方 → 自动…（右上角快捷键） */
    cycleAiModel() {
      const order: ('auto' | 'local' | 'third_party')[] = ['auto', 'local', 'third_party']
      const idx = order.indexOf(this.aiModel)
      this.setAiModel(order[(idx + 1) % order.length])
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      localStorage.setItem('pulsehub.sidebar.collapsed', this.sidebarCollapsed ? '1' : '0')
    },
    /**
     * 打开 AI 助手面板。
     *
     * @param context 展示用的短标签（页面名 / 分析名）
     * @param options.brief  发给模型的完整上下文（刚生成的解读、关键指标等）
     * @param options.fresh  是否开一个新对话（「接着问 AI 助手」要新开，
     *                       否则会接在上一次无关对话后面，等于没有上下文）
     */
    openAiPanel(context = '', options: { brief?: string; fresh?: boolean } = {}) {
      this.aiPanelContext = context
      this.aiPanelBrief = options.brief || ''
      this.aiPanelFresh = Boolean(options.fresh)
      this.aiPanelNonce += 1
      this.aiPanelOpen = true
    },
    /**
     * 打开页面级 AI 分析（用户反馈：点「AI 摘要」「AI 现金流分析」应直接出结果）。
     * scope 决定后端聚合哪些事实，title 只是弹窗标题的补充说明。
     */
    openAiAnalysis(scope: string, title = '', period = 'current_month', targetId = '') {
      this.aiAnalysisScope = scope
      this.aiAnalysisTitle = title
      this.aiAnalysisPeriod = period
      this.aiAnalysisTargetId = targetId
      this.aiAnalysisOpen = true
    },
    closeAiAnalysis() {
      this.aiAnalysisOpen = false
    },
    closeAiPanel() {
      this.aiPanelOpen = false
    },
    /** 统一二次确认（03 §5.1、20 §6）：返回是否确认以及可选的填写原因。 */
    askConfirm(options: ConfirmOptions) {
      return new Promise<{ ok: boolean; reason?: string }>((resolve) => {
        this.confirm = { ...options, resolve }
      })
    },
    resolveConfirm(result: { ok: boolean; reason?: string }) {
      this.confirm?.resolve(result)
      this.confirm = null
    },
  },
})
