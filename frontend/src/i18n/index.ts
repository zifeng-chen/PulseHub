/**
 * 中文界面字典（03 §3.1、23 §3）。
 * 数据库/接口/路由/代码可用英文标识，但所有用户可见文案必须是中文。
 * 本地内置一份与后端一致的枚举映射作为兜底，启动后可被 /meta/enums 覆盖。
 */

export const MENU_LABELS: Record<string, string> = {
  dashboard: '脉枢台',
  ledger: '家庭台账',
  insights: '脉诊',
  settings: '设置',
}

export const ENUM_ZH: Record<string, Record<string, string>> = {
  event_status: { draft: '草稿', confirmed: '已确认', cancelled: '已取消', reversed: '已冲销' },
  event_type: {
    income: '收入', expense: '支出', transfer: '内部转账', asset_acquire: '资产购置',
    asset_dispose: '资产处置', liability_draw: '借款到账', liability_repay: '负债还款',
    investment_buy: '投资买入', investment_sell: '投资卖出', rent_receivable: '租金应收',
    rent_received: '租金实收', deposit_receive: '押金收取', deposit_return: '押金退还',
  },
  lifecycle_status: { active: '正常', archived: '已归档', disabled: '已停用', failed: '失败' },
  suggestion_status: { pending: '待处理', accepted: '已接受', rejected: '已拒绝', expired: '已过期' },
  asset_status: {
    active: '持有中', rented: '出租中', idle: '闲置', mortgaged: '在贷',
    disposed: '已出售', scrapped: '已报废', archived: '已归档',
  },
  asset_type: {
    real_estate: '不动产', parking: '车位', vehicle: '车辆',
    precious_metal: '贵金属', other: '其他资产',
  },
  asset_liquidity: { current: '流动资产', non_current: '非流动资产' },
  property_type: {
    residence: '住宅', shop: '商铺', office: '办公', garage: '车库', other: '其他',
  },
  parking_type: { ownership: '产权车位', usage_right: '使用权车位', lease: '租赁车位' },
  vehicle_energy_type: {
    fuel: '燃油', electric: '纯电', phev: '插电混动', hybrid: '油电混动', other: '其他',
  },
  vehicle_usage_status: { in_use: '自用', shared: '家庭共用', rented: '出租', idle: '闲置' },
  insurance_type: {
    compulsory: '交强险', commercial: '商业险', damage: '车损险',
    third_party: '第三者责任险', occupant: '车上人员责任险', other: '其他',
  },
  insurance_renewal_status: {
    pending: '待续保', renewed: '已续保', expired: '已过期', cancelled: '已取消',
  },
  metal_category: { gold: '黄金', silver: '白银', platinum: '铂金', palladium: '钯金', other: '其他' },
  metal_form: {
    bar: '条料', coin: '币类', jewelry: '首饰', account_metal: '账户贵金属', ornament: '饰品',
  },
  metal_unit: { g: '克', kg: '千克', oz: '盎司' },
  utility_type: {
    water: '水费', electricity: '电费', gas: '燃气费', heating: '暖气费',
    property: '物业费', network: '网络费', other: '其他',
  },
  billing_cycle: {
    monthly: '按月', quarterly: '按季', half_yearly: '半年', yearly: '按年', one_time: '一次性',
  },
  depreciation_method: {
    none: '不折旧', straight_line: '直线法', double_declining: '双倍余额递减法',
    sum_of_years: '年数总和法', other: '其他',
  },
  valuation_source: {
    manual: '手工估值', appraisal: '评估报告', market: '市场行情参考',
    contract: '合同价格', other: '其他',
  },
  account_type: { bank: '银行卡', cash: '现金', e_wallet: '电子钱包', other: '其他账户' },
  transfer_status: { draft: '草稿', confirmed: '已确认', reversed: '已冲销' },
  liability_status: { active: '正常', overdue: '逾期', settled: '已结清' },
  liability_type: {
    mortgage: '房贷', vehicle_loan: '车贷', consumer_loan: '消费贷', business_loan: '经营贷',
    personal: '亲友借款', credit_card: '信用卡', other: '其他负债',
  },
  loan_method: {
    commercial: '商业贷款', provident_fund: '公积金贷款', combined: '组合贷款', other: '其他方式',
  },
  rate_type: { fixed: '固定利率', floating: '浮动利率', lpr_plus: 'LPR 加点', other: '其他' },
  repayment_method: {
    equal_installment: '等额本息', equal_principal: '等额本金', interest_first: '先息后本',
    lump_sum: '一次性还本付息', other: '其他',
  },
  payment_frequency: {
    monthly: '按月', quarterly: '按季', half_yearly: '半年', yearly: '按年',
    weekly: '每周', one_time: '一次性',
  },
  bill_status: { pending: '待还款', partial: '部分已还', paid: '已还清', overdue: '已逾期' },
  investment_account_type: {
    securities: '证券账户', fund: '基金账户', bank_wm: '银行理财', other: '其他',
  },
  investment_trade_type: { buy: '买入', sell: '卖出', dividend: '分红', fee: '费用' },
  lease_status: { draft: '草稿', active: '生效中', expired: '已到期', terminated: '已终止' },
  receivable_status: { pending: '待收', paid: '已收' },
  deposit_status: { held: '待退还', returned: '已退还' },
  repair_bearer: { landlord: '房东承担', tenant: '租客承担', both: '双方约定' },
  material_type: { receipt: '票据', bill: '账单', bank_statement: '流水截图', contract: '合同', other: '其他' },
  ocr_status: { pending: '识别中', success: '识别成功', failed: '识别失败' },
  reminder_status: { pending: '待处理', completed: '已完成', snoozed: '已延后', dismissed: '已忽略' },
  delivery_status: { pending: '待发送', sent: '已发送', failed: '发送失败' },
  file_status: { uploaded: '已上传', scanning: '安全检查中', ready: '可用', failed: '失败' },
  conclusion_type: { fact: '事实', computed: '计算', inference: '推断', suggestion: '建议' },
  permission_action: {
    view: '查看', create_draft: '创建草稿', edit_draft: '编辑草稿', confirm: '确认', reverse: '冲销',
    correct: '更正', archive: '归档', delete: '删除', configure: '配置', audit_view: '审计查看',
  },
  role: { owner: '家庭所有者', admin: '系统管理员', member: '家庭成员', readonly: '只读成员' },
  source_method: { manual: '手工录入', ocr: 'OCR 识别', ai: 'AI 建议', api: '接口调用', reconciliation: '对账调整', system: '系统' },
  issue_level: { critical: '严重', warning: '警告', info: '提示' },
}

export const CATEGORY_DOMAIN_ZH: Record<string, string> = {
  income: '收入', expense: '支出', asset: '资产',
  liability: '负债', investment: '投资', lease: '租赁', account: '账户',
}

export const THEME_LABELS: Record<string, string> = {
  light: '浅色模式', dark: '深色模式', system: '跟随系统',
}

export const PROVIDER_STATUS_ZH: Record<string, string> = {
  unconfigured: '未配置',
  testing: '检测中',
  connected: '已连接',
  disabled: '已停用',
  failed: '测试失败',
}

let serverDictionary: Record<string, Record<string, string>> = {}

export function mergeDictionary(registry: Record<string, Record<string, string>> | undefined) {
  if (registry && typeof registry === 'object') {
    serverDictionary = registry
  }
}

/** 枚举中文显示：优先服务端字典，其次内置兜底，最后回退原值。 */
export function zh(namespace: string, value?: string | null): string {
  if (!value) return ''
  return serverDictionary?.[namespace]?.[value] ?? ENUM_ZH[namespace]?.[value] ?? value
}

/** 通用 UI 文案 */
export const UI = {
  loading: '加载中',
  retry: '重新加载',
  empty: '暂无数据',
  error: '加载失败',
  noPermission: '无访问权限',
  save: '保存',
  cancel: '取消',
  close: '关闭',
  confirm: '确认',
  submit: '提交',
  search: '搜索',
  reset: '重置',
  next: '下一步',
  prev: '上一步',
  finish: '完成',
  back: '返回',
  backendUnavailable: '后端服务不可用',
  backendUnavailableHint: '请确认后端服务已启动（默认 http://127.0.0.1:8000）。',
}
