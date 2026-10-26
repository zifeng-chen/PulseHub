/**
 * 刷新率探测与低帧降级。
 *
 * 浏览器在 120Hz 设备上会自动用更高频率调度 `requestAnimationFrame`，
 * Web 侧没法「开启」高刷；能做的是两件事：
 * 1. 测出当前实际刷新率（写进 `data-refresh-rate`，便于在手机上确认是否真的跑在高刷）；
 * 2. 如果实测明显低于 60（低端机/省电模式），打上 `data-low-fps`，
 *    由 CSS 进一步停掉装饰动效、去掉阴影，优先保证滚动跟手。
 *
 * 只在启动时采样 1.2 秒，之后不再跑循环，避免探测本身耗电。
 */
let cached = 0

export function detectRefreshRate(durationMs = 1200): Promise<number> {
  if (cached) return Promise.resolve(cached)
  if (typeof window === 'undefined' || typeof requestAnimationFrame !== 'function') {
    return Promise.resolve(60)
  }
  return new Promise((resolve) => {
    const samples: number[] = []
    let last = performance.now()
    const started = last
    const tick = (now: number) => {
      const delta = now - last
      last = now
      if (delta > 0 && delta < 100) samples.push(delta)
      if (now - started < durationMs) {
        requestAnimationFrame(tick)
        return
      }
      samples.sort((a, b) => a - b)
      const median = samples[Math.floor(samples.length / 2)] ?? 16.7
      cached = Math.round(1000 / median)
      resolve(cached)
    }
    requestAnimationFrame(tick)
  })
}

export async function applyRefreshRateHint(): Promise<void> {
  const fps = await detectRefreshRate()
  const root = document.documentElement
  root.dataset.refreshRate = String(fps)
  // 明显低于 60 才判定掉帧，避免启动瞬间的抖动误伤
  if (fps > 0 && fps < 50) root.dataset.lowFps = 'true'
  else delete root.dataset.lowFps
}
