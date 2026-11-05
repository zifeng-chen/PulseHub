/**
 * AI 回复的 Markdown 渲染（防 XSS）。
 *
 * 助手回复可能来自第三方模型或本地模型，都属于「不可信输入」，直接 v-html 会
 * 被注入 <script>/onerror 之类；所以这里 fixed 规则是「marked 解析 → DOMPurify 清洗」，
 * 顺序不能反，也不能只解析不清洗。
 */
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({
  gfm: true, // 表格 / 任务列表 / 删除线
  breaks: true, // 单换行也转 <br>，贴合聊天里「一句话一行」的写法（等价旧版 pre-wrap）
})

// 外链一律新开标签 + 断开引用，避免在面板里被跳转或拿到 opener 权限
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export function renderMarkdown(text: string): string {
  if (!text) return ''
  const html = marked.parse(text, { async: false }) as string
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } })
}
