import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

describe('renderMarkdown', () => {
  it('加粗与列表', () => {
    const html = renderMarkdown('**重点**\n- 甲\n- 乙')
    expect(html).toContain('<strong>重点</strong>')
    expect(html).toContain('<li>甲</li>')
  })

  it('单换行转 <br>，贴合聊天排版', () => {
    const html = renderMarkdown('第一行\n第二行')
    expect(html).toContain('<br>')
  })

  it('剥离脚本与事件属性，防 XSS', () => {
    const html = renderMarkdown('你好 <script>alert(1)</script> <img src=x onerror=alert(1)>')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onerror')
  })

  it('链接新开标签并断开引用', () => {
    const html = renderMarkdown('[链接](https://example.com)')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('空串返回空', () => {
    expect(renderMarkdown('')).toBe('')
  })
})
