import { describe, expect, it } from 'vitest'
import { buildAnalysisBrief, buildObjectBrief, buildPageBrief } from './ai-brief'

describe('buildAnalysisBrief', () => {
  it('把结论 / 关注 / 建议拼成可带走的三行', () => {
    const brief = buildAnalysisBrief({
      scope_label: '现金流',
      bullets: [
        { label: '结论', text: '手头有18万' },
        { label: '关注', text: '有个月花超了' },
        { label: '建议', text: '先还信用卡' },
      ],
      facts: [{ label: '账户余额合计', value: '186420.00 CNY' }],
    })
    expect(brief).toContain('AI 现金流分析')
    expect(brief).toContain('结论：手头有18万')
    expect(brief).toContain('要注意：有个月花超了')
    expect(brief).toContain('建议：先还信用卡')
    expect(brief).toContain('关键数据：账户余额合计 186420.00 CNY')
  })

  it('模型整段返回（没分出行）时原样带上', () => {
    const brief = buildAnalysisBrief({
      scope_label: '负债',
      summary: '家里的债不算多，但下个月要还两笔。',
    })
    expect(brief).toContain('家里的债不算多，但下个月要还两笔。')
  })

  it('模型不可用时退回确定性那句话', () => {
    const brief = buildAnalysisBrief({
      scope_label: '脉枢台',
      plain_summary: '把欠的钱都扣掉，家里实际有 860000.00 CNY。',
    })
    expect(brief).toContain('把欠的钱都扣掉')
  })

  it('关键数据最多带 6 条，避免上下文变成一张表', () => {
    const facts = Array.from({ length: 10 }, (_, i) => ({ label: `指标${i}`, value: `${i}` }))
    const brief = buildAnalysisBrief({ scope_label: '账户', facts })
    expect(brief).toContain('指标5')
    expect(brief).not.toContain('指标6')
  })

  it('体检发现只带标题，不带长描述', () => {
    const brief = buildAnalysisBrief({
      scope_label: '租约体检',
      checks: [{ title: '缺少租客联系方式', detail: '很长的说明文字' }],
    })
    expect(brief).toContain('缺少租客联系方式')
    expect(brief).not.toContain('很长的说明文字')
  })

  it('超长时截断，不把 system 撑爆', () => {
    const brief = buildAnalysisBrief({
      scope_label: '现金流',
      summary: '很'.repeat(1200),
    })
    expect(brief.length).toBeLessThanOrEqual(901)
    expect(brief.endsWith('…')).toBe(true)
  })

  it('完全没有内容时仍给一句可用的开头', () => {
    const brief = buildAnalysisBrief({ scope_label: '现金流' })
    expect(brief).toContain('AI 现金流分析')
  })
})

describe('buildObjectBrief', () => {
  it('把对象关键字段带过去，空值自动跳过', () => {
    const brief = buildObjectBrief('这张信用卡', '招行白金', [
      ['欠款', '23000.00 CNY'],
      ['额度', ''],
      ['状态', null],
    ])
    expect(brief).toContain('这张信用卡「招行白金」')
    expect(brief).toContain('- 欠款：23000.00 CNY')
    expect(brief).not.toContain('额度')
    expect(brief).not.toContain('状态')
    expect(brief).toContain('不要泛泛而谈')
  })

  it('名称为空时也不出现空白引号', () => {
    expect(buildObjectBrief('这项资产', '', [])).toContain('「未命名」')
  })

  it('超长时截断', () => {
    const long = Array.from({ length: 80 }, (_, i) => ['指标' + i, '值'.repeat(20)] as [string, string])
    const brief = buildObjectBrief('这项资产', 'X', long)
    expect(brief.length).toBeLessThanOrEqual(701)
  })
})

describe('buildPageBrief', () => {
  it('带上页面名与概况', () => {
    const brief = buildPageBrief('车位', [['数量', '3 个'], ['总值', '450000.00 CNY']])
    expect(brief).toContain('「车位」页面')
    expect(brief).toContain('- 数量：3 个')
    expect(brief).toContain('- 总值：450000.00 CNY')
  })
})
