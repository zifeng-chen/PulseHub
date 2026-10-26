/**
 * 弹层栈（抽屉 / 弹窗共用）。
 *
 * 解决的问题：
 * - 抽屉里再开确认弹窗时，按 Esc 会同时关闭两层，未保存表单直接丢失；
 * - 每层各自改写 body.overflow，后关闭的一层会把仍打开的层解锁。
 *
 * 规则：只有栈顶响应 Esc；滚动锁定按层计数，最后一层关闭才恢复。
 */
const stack: symbol[] = []

export function pushOverlay(id: symbol): void {
  if (!stack.includes(id)) stack.push(id)
  document.body.style.overflow = 'hidden'
}

export function popOverlay(id: symbol): void {
  const index = stack.lastIndexOf(id)
  if (index >= 0) stack.splice(index, 1)
  if (stack.length === 0) document.body.style.overflow = ''
}

export function isTopOverlay(id: symbol): boolean {
  return stack.length > 0 && stack[stack.length - 1] === id
}
