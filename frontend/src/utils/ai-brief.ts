/**
 * 把页面级 AI 分析的结果压成一段「可带走的上下文」。
 *
 * 背景（用户反馈）：「接着问 AI 助手」点开后助手完全不知道刚才分析了什么，
 * 等于从零开始问，用户得把结论再复述一遍。
 *
 * 这里只做纯函数转换，便于单测：输入是分析接口的返回，输出是发给模型的
 * `context` 正文（会进入 system 提示词）。刻意保持简短——上下文不是数据快照，
 * 太长反而稀释重点，也会拖慢小模型。
 */

export interface BriefBullet {
  label: string
  text: string
}

export interface BriefFact {
  label: string
  value: string
}

export interface BriefCheck {
  title: string
  detail?: string
}

export interface BriefSource {
  scope_label: string
  /** 模型分好行的三条 */
  bullets?: BriefBullet[]
  /** 模型整段返回（没分出行时才有） */
  summary?: string | null
  /** 确定性兜底那句话 */
  plain_summary?: string | null
  facts?: BriefFact[]
  checks?: BriefCheck[]
}

/** 指标最多带 6 条：够模型引用，又不至于把上下文写成一张表 */
const MAX_FACTS = 6
/** 总长度上限：超过就截断，避免把 system 撑爆 */
const MAX_LEN = 900

function bulletText(data: BriefSource, label: string): string {
  return (data.bullets || []).find((b) => b.label === label)?.text?.trim() || ''
}

export function buildAnalysisBrief(data: BriefSource): string {  const label = (data.scope_label || '页面').trim()
  const lines: string[] = [`以下是我刚在「AI ${label}分析」里看到的结果，请基于它回答：`]

  const conclusion = bulletText(data, '结论')
  const attention = bulletText(data, '关注')
  const advice = bulletText(data, '建议')

  if (conclusion) {
    lines.push(`结论：${conclusion}`)
    if (attention) lines.push(`要注意：${attention}`)
    if (advice) lines.push(`建议：${advice}`)
  } else {
    // 模型整段返回时把它原样带上（已经是一段人话），再补兜底摘要
    const fallback = (data.summary || data.plain_summary || '').trim()
    if (fallback) lines.push(fallback)
  }

  const facts = (data.facts || [])
    .filter((f) => f.label && f.value)
    .slice(0, MAX_FACTS)
    .map((f) => `${f.label} ${f.value}`)
  if (facts.length) lines.push(`关键数据：${facts.join('；')}`)

  const checks = (data.checks || []).map((c) => c.title).filter(Boolean).slice(0, 4)
  if (checks.length) lines.push(`体检发现：${checks.join('；')}`)

  const text = lines.join('\n')
  return text.length > MAX_LEN ? `${text.slice(0, MAX_LEN)}…` : text
}

export interface InsightSource {
  title: string
  highlight?: string | null
  summary?: string | null
  caliber?: string | null
  period_label?: string | null
}

/**
 * 洞察卡片的「让 AI 继续分析」：把这条结论本身带过去。
 * 只传标题的话，助手得从零把这条结论再讲一遍，等于没有上下文。
 */
export function buildInsightBrief(c: InsightSource): string {
  const lines: string[] = [`以下是我刚看到的一条台账结论，请围绕它继续分析：`, `结论：${c.title}`]
  const detail = (c.highlight || c.summary || '').trim()
  if (detail) lines.push(`说明：${detail}`)
  const meta = [c.period_label ? `期间 ${c.period_label}` : '', c.caliber ? `口径 ${c.caliber}` : '']
    .filter(Boolean)
    .join(' · ')
  if (meta) lines.push(meta)
  const text = lines.join('\n')
  return text.length > MAX_LEN ? `${text.slice(0, MAX_LEN)}…` : text
}

export type BriefPair = [label: string, value: string | number | null | undefined]

const MAX_OBJECT_LEN = 700

function joinPairs(pairs: BriefPair[]): string[] {
  const lines: string[] = []
  for (const [label, value] of pairs) {
    const text = value === null || value === undefined ? '' : String(value).trim()
    if (text) lines.push(`- ${label}：${text}`)
  }
  return lines
}

/**
 * 详情抽屉里的「问 AI」：把这一条记录的关键字段带过去。
 *
 * 只传名字的话，助手并不知道这张卡欠多少、这份租约什么时候到期，
 * 只能回一些放之四海而皆准的话——用户会觉得"它没看懂我在看什么"。
 */
export function buildObjectBrief(
  kind: string,
  name: string | null | undefined,
  pairs: BriefPair[],
): string {
  const lines = [`我正在看${kind}「${(name || '未命名').trim()}」，已知信息：`]
  lines.push(...joinPairs(pairs))
  lines.push('请围绕这条记录回答，不要泛泛而谈。')
  const text = lines.join('\n')
  return text.length > MAX_OBJECT_LEN ? `${text.slice(0, MAX_OBJECT_LEN)}…` : text
}

/** 列表页的「问 AI」：带上这一页的概况（数量、总额等） */
export function buildPageBrief(page: string, pairs: BriefPair[]): string {
  const lines = [`我当前在「${page}」页面，这一页的概况：`]
  lines.push(...joinPairs(pairs))
  lines.push('请结合这一页回答我的问题。')
  const text = lines.join('\n')
  return text.length > MAX_OBJECT_LEN ? `${text.slice(0, MAX_OBJECT_LEN)}…` : text
}
