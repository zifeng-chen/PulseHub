/**
 * 请求时序守卫：快速切换筛选/页签时，旧请求的慢响应不能覆盖新结果。
 * 用法：
 *   const latest = useLatestRequest()
 *   async function load() {
 *     const seq = latest.begin()
 *     const data = await api.get(...)
 *     if (!latest.isCurrent(seq)) return
 *     rows.value = data
 *   }
 * 比手写 `let loadSeq = 0` 更统一，也便于统一收口（M-1/L-15）。
 */

export function useLatestRequest() {
  let seq = 0
  function begin(): number {
    return ++seq
  }
  function isCurrent(token: number): boolean {
    return token === seq
  }
  return { begin, isCurrent }
}
