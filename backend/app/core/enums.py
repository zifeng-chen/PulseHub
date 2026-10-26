"""核心枚举与中文显示映射（23 §3、03 §3.1）。

内部值可用英文技术标识；用户可见一律使用中文显示。
新增枚举必须同时提供中文显示、说明、允许动作、是否进入统计、是否允许 AI 推荐。
"""

from __future__ import annotations

from typing import Any


class EnumMeta(dict):
    """枚举元数据：中文显示 + 说明 + 允许动作 + 是否进统计 + 是否允许 AI 推荐。"""

    def display(self, value: str | None) -> str:
        if value is None:
            return ""
        item = self.get(value)
        return item["zh"] if item else str(value)


def _e(zh: str, desc: str = "", actions: list[str] | None = None,
       counts: bool = False, ai: bool = False) -> dict[str, Any]:
    return {"zh": zh, "desc": desc, "actions": actions or [], "counts": counts, "ai": ai}


# ---- 事件状态（11 §2）----
EVENT_STATUS = EnumMeta({
    "draft": _e("草稿", "尚未确认，不计入任何统计", ["edit", "confirm", "cancel"], counts=False, ai=True),
    "confirmed": _e("已确认", "已形成正式业务事实", ["reverse"], counts=True, ai=True),
    "cancelled": _e("已取消", "草稿被取消，终态", [], counts=False),
    "reversed": _e("已冲销", "已确认后被冲销，保留历史", [], counts=False),
})

# ---- 事件类型（23 §3）----
EVENT_TYPE = EnumMeta({
    "income": _e("收入", "已确认收入才计入收入统计", counts=True, ai=True),
    "expense": _e("支出", "已确认消费/利息/手续费", counts=True, ai=True),
    "refund": _e("退款", "关联原支出并冲减支出统计，不计入收入", counts=True, ai=True),
    "transfer": _e("内部转账", "只改变账户余额，不改变收支", counts=False, ai=True),
    "asset_acquire": _e("资产购置", "形成资产与资金减少，口径由事件显示区分", counts=False),
    "asset_dispose": _e("资产处置", "资产减少与资金增加", counts=False),
    "liability_draw": _e("借款到账", "不算收入", counts=False),
    "liability_repay": _e("负债还款", "本金不重复计支出", counts=True),
    "investment_buy": _e("投资买入", "形成持仓与资金减少", counts=False),
    "investment_sell": _e("投资卖出", "持仓减少与资金增加", counts=False),
    "rent_receivable": _e("租金应收", "不改变账户余额，不进收入", counts=False),
    "rent_received": _e("租金实收", "计入收入，必须关联到账账户", counts=True),
    "deposit_receive": _e("押金收取", "不算收入，属待退还资金", counts=False),
    "deposit_return": _e("押金退还", "不算支出", counts=False),
    "receivable_repay": _e("借出款回款", "本金回笼，不计入收入", counts=False),
})

# ---- 借出款 ----
LEND_TYPE = EnumMeta({
    "cash": _e("现金出借", "从现金账户借出"),
    "bank_card": _e("银行卡出借", "从储蓄卡转账借出"),
    "credit_card": _e("信用卡出借", "刷卡或取现借出，可能产生手续费"),
    "e_wallet": _e("电子钱包出借", "支付宝 / 微信等平台借出"),
    "other": _e("其他出借", ""),
})

# 借出款的还款方式与负债不同：借出多是"到期一次还本/分期/先息后本"这类约定。
# 注意：这个枚举曾与负债的 REPAYMENT_METHOD 同名，后者覆盖前者，
# 导致借出款选「分期还款」「按月付息」时后端校验失败——两个必须分开命名。
LEND_REPAYMENT_METHOD = EnumMeta({
    "lump_sum": _e("到期一次还本", ""),
    "installments": _e("分期还款", ""),
    "monthly_interest": _e("按月付息、到期还本", ""),
    "other": _e("其他方式", ""),
})

FEE_BEARER = EnumMeta({
    "borrower": _e("借款人承担", "手续费由借款人支付，不影响出借人"),
    "self": _e("自己承担", "手续费计入自己的支出"),
})

# ---- 通用生命周期 ----
LIFECYCLE_STATUS = EnumMeta({
    "active": _e("正常"),
    "archived": _e("已归档"),
    "disabled": _e("已停用"),
    "failed": _e("失败"),
})

# ---- AI 建议状态 ----
SUGGESTION_STATUS = EnumMeta({
    "pending": _e("待处理", "", ["accept", "reject"], ai=True),
    "accepted": _e("已接受", "终态，不可重复操作"),
    "rejected": _e("已拒绝", "终态，不可重复操作"),
    "expired": _e("已过期", "终态"),
})

# ---- 资产状态（11 §2）----
ASSET_STATUS = EnumMeta({
    "active": _e("持有中"),
    "rented": _e("出租中"),
    "idle": _e("闲置"),
    "mortgaged": _e("在贷"),
    "disposed": _e("已出售"),
    "scrapped": _e("已报废"),
    "archived": _e("已归档"),
})

# ---- 资产类型（15 §1、23 §5）----
ASSET_TYPE = EnumMeta({
    "real_estate": _e("不动产", "住宅、车位、商铺、办公等房产", counts=True, ai=True),
    # 车位已并入「不动产 / 车位」分类；此类型仅保留历史数据兼容，前端不再提供新增入口
    "parking": _e("车位", "产权/使用权/租赁车位（历史数据兼容）", counts=True, ai=True),
    "vehicle": _e("车辆", "机动车与非机动车", counts=True, ai=True),
    "precious_metal": _e("贵金属", "金/银/铂金/钯金等实物与账户贵金属", counts=True, ai=True),
    "other": _e("其他资产", "数字设备、家具家电、收藏品、知识产权、保险现金价值等", counts=True, ai=True),
})

# ---- 流动性归类（06 §2 顶部统计口径；由资产类型推导，不新增字段）----
ASSET_LIQUIDITY = EnumMeta({
    "current": _e("流动资产", "可快速变现；当前口径：贵金属"),
    "non_current": _e("非流动资产", "长期持有；当前口径：不动产、车位、车辆、其他资产"),
})

# ---- 房产类型（15 §3.1）----
PROPERTY_TYPE = EnumMeta({
    "residence": _e("住宅"),
    "shop": _e("商铺"),
    "office": _e("办公"),
    "garage": _e("车库"),
    "other": _e("其他"),
})

# ---- 车位类型（15 §4.1）----
PARKING_TYPE = EnumMeta({
    "ownership": _e("产权车位"),
    "usage_right": _e("使用权车位"),
    "lease": _e("租赁车位"),
})

# ---- 车辆能源类型（15 §5.1）----
VEHICLE_ENERGY_TYPE = EnumMeta({
    "fuel": _e("燃油"),
    "electric": _e("纯电"),
    "phev": _e("插电混动"),
    "hybrid": _e("油电混动"),
    "other": _e("其他"),
})

# ---- 车辆使用状态（15 §5.1）----
VEHICLE_USAGE_STATUS = EnumMeta({
    "in_use": _e("自用"),
    "shared": _e("家庭共用"),
    "rented": _e("出租"),
    "idle": _e("闲置"),
})

# ---- 保险类型（15 §5.3）----
INSURANCE_TYPE = EnumMeta({
    "compulsory": _e("交强险"),
    "commercial": _e("商业险"),
    "damage": _e("车损险"),
    "third_party": _e("第三者责任险"),
    "occupant": _e("车上人员责任险"),
    "other": _e("其他"),
})

# ---- 续保状态（15 §5.3）----
INSURANCE_RENEWAL_STATUS = EnumMeta({
    "pending": _e("待续保"),
    "renewed": _e("已续保"),
    "expired": _e("已过期"),
    "cancelled": _e("已取消"),
})

# ---- 贵金属品类（15 §6.1）----
METAL_CATEGORY = EnumMeta({
    "gold": _e("黄金"),
    "silver": _e("白银"),
    "platinum": _e("铂金"),
    "palladium": _e("钯金"),
    "other": _e("其他"),
})

# ---- 贵金属形式（15 §6.1）----
METAL_FORM = EnumMeta({
    "bar": _e("条料", "金条、银条、金砖等"),
    "coin": _e("币类", "金币、银币等"),
    "jewelry": _e("首饰"),
    "account_metal": _e("账户贵金属"),
    "ornament": _e("饰品"),
})

# ---- 贵金属计量单位（15 §6.1）----
METAL_UNIT = EnumMeta({
    "g": _e("克"),
    "kg": _e("千克"),
    "oz": _e("盎司"),
})

# ---- 公用事业类型（15 §3.4）----
UTILITY_TYPE = EnumMeta({
    "water": _e("水费"),
    "electricity": _e("电费"),
    "gas": _e("燃气费"),
    "heating": _e("暖气费"),
    "property": _e("物业费"),
    "network": _e("网络费"),
    "other": _e("其他"),
})

# ---- 缴费周期（15 §3.4 / 15 §4.1）----
BILLING_CYCLE = EnumMeta({
    "monthly": _e("按月"),
    "quarterly": _e("按季"),
    "half_yearly": _e("半年"),
    "yearly": _e("按年"),
    "one_time": _e("一次性"),
})

# ---- 折旧方式（06 §2 资产表单）----
DEPRECIATION_METHOD = EnumMeta({
    "none": _e("不折旧"),
    "straight_line": _e("直线法"),
    "double_declining": _e("双倍余额递减法"),
    "sum_of_years": _e("年数总和法"),
    "other": _e("其他"),
})

# ---- 估值来源（15 §3.1、15 §6.2：参考单价必须标明来源与日期）----
VALUATION_SOURCE = EnumMeta({
    "manual": _e("手工估值", "由家庭成员手工填写，无外部依据"),
    "appraisal": _e("评估报告", "第三方评估机构出具"),
    "market": _e("市场行情参考", "参考公开行情，必须同时记录日期与来源说明"),
    "contract": _e("合同价格", "以合同/成交价为准"),
    "beike": _e("贝壳估价", "贝壳开放平台房屋价值分析结果"),
    "other": _e("其他"),
})

# ---- 负债类型（06 §6、15 §3.2/§5.2）----
LIABILITY_TYPE = EnumMeta({
    "mortgage": _e("房贷", "必须关联具体不动产；余额与利息由负债模块统一计算", counts=True, ai=True),
    "vehicle_loan": _e("车贷", "必须关联具体车辆", counts=True, ai=True),
    "consumer_loan": _e("消费贷", "装修、教育、耐用品等个人消费贷款", counts=True, ai=True),
    "business_loan": _e("经营贷", "经营周转、小微经营贷款", counts=True, ai=True),
    "personal": _e("亲友借款", "向亲友借入，通常无息或低息", counts=True, ai=True),
    "credit_card": _e("信用卡", "循环信用，账单与还款独立管理", counts=True, ai=True),
    "other": _e("其他负债", "", counts=True, ai=False),
})

# ---- 房贷贷款方式（商业 / 公积金 / 组合）----
LOAN_METHOD = EnumMeta({
    "commercial": _e("商业贷款", "银行商业按揭"),
    "provident_fund": _e("公积金贷款", "住房公积金贷款，利率通常更低"),
    "combined": _e("组合贷款", "公积金 + 商业；建议拆成两笔分别记录，利率与年限更准确"),
    "other": _e("其他方式", ""),
})

# ---- 利率类型（15 §3.2）----
RATE_TYPE = EnumMeta({
    "fixed": _e("固定利率"),
    "floating": _e("浮动利率"),
    "lpr_plus": _e("LPR 加点"),
    "other": _e("其他"),
})

# ---- 还款方式（15 §3.2）----
REPAYMENT_METHOD = EnumMeta({
    "equal_installment": _e("等额本息"),
    "equal_principal": _e("等额本金"),
    "interest_first": _e("先息后本"),
    "lump_sum": _e("一次性还本付息"),
    "other": _e("其他"),
})

# ---- 还款频率（06 §6 新增字段：还款频率）----
PAYMENT_FREQUENCY = EnumMeta({
    "monthly": _e("按月"),
    "quarterly": _e("按季"),
    "half_yearly": _e("半年"),
    "yearly": _e("按年"),
    "weekly": _e("每周"),
    "one_time": _e("一次性"),
})

# ---- 信用卡账单状态（06 §7）----
BILL_STATUS = EnumMeta({
    "pending": _e("待还款"),
    "partial": _e("部分已还"),
    "paid": _e("已还清"),
    "overdue": _e("已逾期"),
})

# ---- 投资账户类型（06 §8）----
INVESTMENT_ACCOUNT_TYPE = EnumMeta({
    "securities": _e("证券账户"),
    "fund": _e("基金账户"),
    "bank_wm": _e("银行理财"),
    "other": _e("其他"),
})

# ---- 投资交易类型（06 §8）----
INVESTMENT_TRADE_TYPE = EnumMeta({
    "buy": _e("买入", "形成持仓与资金减少", counts=False, ai=True),
    "sell": _e("卖出", "持仓减少与资金增加", counts=False, ai=True),
    "dividend": _e("分红", "投资账户资金增加，同时计入已实现收益", counts=False, ai=True),
    "fee": _e("费用", "管理费、手续费等投资账户资金减少", counts=False, ai=False),
})

# ---- 账户类型（M1 核心账务引入）----
# 信用卡/负债类账户由 M3 负债与信用卡模块以 liabilities.credit_cards 承载，
# 此处不纳入，避免同一笔债务被重复统计。
ACCOUNT_TYPE = EnumMeta({
    "bank": _e("银行卡", "银行储蓄卡/存折等银行结算账户", counts=True, ai=True),
    "cash": _e("现金", "手持现金与零钱", counts=True, ai=True),
    "e_wallet": _e("电子钱包", "支付宝、微信零钱等第三方支付余额", counts=True, ai=True),
    "other": _e("其他账户", "不属于以上类型的资金账户", counts=True, ai=False),
})

# ---- 在职状态（工作档案，用于现金流风险预警）----
EMPLOYMENT_STATUS = EnumMeta({
    "employed": _e("在职", "正常在职，有稳定工资收入"),
    "probation": _e("试用期", "试用期内，收入稳定性较低"),
    "unemployed": _e("失业", "当前无工作，现金流存在断档风险"),
    "retired": _e("退休", "已退休，收入来源转为养老金等"),
    "freelance": _e("自由职业", "自由职业/灵活就业，收入波动较大"),
})

# ---- 转账组状态 ----
TRANSFER_STATUS = EnumMeta({
    "draft": _e("草稿", "尚未确认，不改变任何账户余额"),
    "confirmed": _e("已确认", "已生成两条资金变动，仅改变账户余额"),
    "reversed": _e("已冲销", "保留历史，不再计入余额"),
})

# ---- 负债状态 ----
LIABILITY_STATUS = EnumMeta({
    "active": _e("正常"),
    "overdue": _e("逾期"),
    "settled": _e("已结清"),
})

# ---- 租约状态 ----
LEASE_STATUS = EnumMeta({
    "draft": _e("草稿"),
    "active": _e("生效中"),
    "expired": _e("已到期"),
    "terminated": _e("已终止"),
})

# ---- 提醒状态 ----
REMINDER_STATUS = EnumMeta({
    "pending": _e("待处理", "", ["complete", "snooze", "dismiss"]),
    "completed": _e("已完成"),
    "snoozed": _e("已延后"),
    "dismissed": _e("已忽略"),
})

# ---- 通知发送状态（不是后台任务）----
DELIVERY_STATUS = EnumMeta({
    "pending": _e("待发送"),
    "sent": _e("已发送"),
    "failed": _e("发送失败", "", ["retry"]),
})

# ---- 文件状态 ----
FILE_STATUS = EnumMeta({
    "uploaded": _e("已上传"),
    "scanning": _e("安全检查中"),
    "ready": _e("可用"),
    "failed": _e("失败", "", ["retry"]),
})

# ---- 结论类型（22 §3.2）----
CONCLUSION_TYPE = EnumMeta({
    "fact": _e("事实", "直接来自确定性统计服务"),
    "computed": _e("计算", "由确定性规则计算得出"),
    "inference": _e("推断", "AI 根据多个事实归纳，不是确定事实"),
    "suggestion": _e("建议", "需要用户判断，不自动执行"),
})

# ---- 权限动作（23 §6 十类）----
PERMISSION_ACTION = EnumMeta({
    "view": _e("查看"),
    "create_draft": _e("创建草稿"),
    "edit_draft": _e("编辑草稿"),
    "confirm": _e("确认"),
    "reverse": _e("冲销"),
    "correct": _e("更正"),
    "archive": _e("归档"),
    "delete": _e("删除"),
    "configure": _e("配置"),
    "audit_view": _e("审计查看"),
})

# ---- 角色 ----
ROLE = EnumMeta({
    "owner": _e("家庭所有者"),
    "admin": _e("系统管理员"),
    "member": _e("家庭成员"),
    "readonly": _e("只读成员"),
})

# ---- 来源方式（审计）----
SOURCE_METHOD = EnumMeta({
    "manual": _e("手工录入"),
    "ocr": _e("OCR 识别"),
    "ai": _e("AI 建议"),
    "api": _e("接口调用"),
    "reconciliation": _e("对账调整", "由对账差额生成的调整事件"),
    "system": _e("系统"),
})

# ---- 数据质量问题等级 ----
ISSUE_LEVEL = EnumMeta({
    "critical": _e("严重"),
    "warning": _e("警告"),
    "info": _e("提示"),
})

# ---- 租金应收状态（06 §9；逾期为派生，不入库）----
RECEIVABLE_STATUS = EnumMeta({
    "pending": _e("待收"),
    "paid": _e("已收"),
})

# ---- 押金状态（06 §9：押金单独显示为待退还资金）----
DEPOSIT_STATUS = EnumMeta({
    "held": _e("待退还"),
    "returned": _e("已退还"),
})

# ---- 物业/维修承担方（15 §3.3）----
REPAIR_BEARER = EnumMeta({
    "landlord": _e("房东承担"),
    "tenant": _e("租客承担"),
    "both": _e("双方约定"),
})

# ---- 智能录入材料类型（06 §10）----
MATERIAL_TYPE = EnumMeta({
    "receipt": _e("票据"),
    "bill": _e("账单"),
    "bank_statement": _e("流水截图"),
    "contract": _e("合同"),
    "other": _e("其他"),
})

# ---- OCR 调用状态（10 §5、11 §2）----
OCR_STATUS = EnumMeta({
    "pending": _e("识别中"),
    "success": _e("识别成功"),
    "failed": _e("识别失败", "", ["retry"]),
})

REGISTRY: dict[str, EnumMeta] = {
    "event_status": EVENT_STATUS,
    "event_type": EVENT_TYPE,
    "lend_type": LEND_TYPE,
    "lend_repayment_method": LEND_REPAYMENT_METHOD,
    "fee_bearer": FEE_BEARER,
    "lifecycle_status": LIFECYCLE_STATUS,
    "suggestion_status": SUGGESTION_STATUS,
    "asset_status": ASSET_STATUS,
    "asset_type": ASSET_TYPE,
    "asset_liquidity": ASSET_LIQUIDITY,
    "property_type": PROPERTY_TYPE,
    "parking_type": PARKING_TYPE,
    "vehicle_energy_type": VEHICLE_ENERGY_TYPE,
    "vehicle_usage_status": VEHICLE_USAGE_STATUS,
    "insurance_type": INSURANCE_TYPE,
    "insurance_renewal_status": INSURANCE_RENEWAL_STATUS,
    "metal_category": METAL_CATEGORY,
    "metal_form": METAL_FORM,
    "metal_unit": METAL_UNIT,
    "utility_type": UTILITY_TYPE,
    "billing_cycle": BILLING_CYCLE,
    "depreciation_method": DEPRECIATION_METHOD,
    "valuation_source": VALUATION_SOURCE,
    "account_type": ACCOUNT_TYPE,
    "employment_status": EMPLOYMENT_STATUS,
    "transfer_status": TRANSFER_STATUS,
    "liability_status": LIABILITY_STATUS,
    "liability_type": LIABILITY_TYPE,
    "loan_method": LOAN_METHOD,
    "rate_type": RATE_TYPE,
    "repayment_method": REPAYMENT_METHOD,
    "payment_frequency": PAYMENT_FREQUENCY,
    "bill_status": BILL_STATUS,
    "investment_account_type": INVESTMENT_ACCOUNT_TYPE,
    "investment_trade_type": INVESTMENT_TRADE_TYPE,
    "lease_status": LEASE_STATUS,
    "reminder_status": REMINDER_STATUS,
    "delivery_status": DELIVERY_STATUS,
    "file_status": FILE_STATUS,
    "conclusion_type": CONCLUSION_TYPE,
    "permission_action": PERMISSION_ACTION,
    "role": ROLE,
    "source_method": SOURCE_METHOD,
    "issue_level": ISSUE_LEVEL,
    "receivable_status": RECEIVABLE_STATUS,
    "deposit_status": DEPOSIT_STATUS,
    "repair_bearer": REPAIR_BEARER,
    "material_type": MATERIAL_TYPE,
    "ocr_status": OCR_STATUS,
}


def zh(namespace: str, value: str | None) -> str:
    meta = REGISTRY.get(namespace)
    return meta.display(value) if meta else (value or "")


def dump_registry() -> dict[str, Any]:
    """输出全部枚举中文映射，供前端字典与测试核对。"""
    return {ns: {k: v["zh"] for k, v in meta.items()} for ns, meta in REGISTRY.items()}
