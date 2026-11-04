/**
 * 深链定位（`?focus=id1,id2`）。
 *
 * 场景：台账总览的「待办时间轴」「待补齐」点进来时，光跳到列表页没用——
 * 一屏几十条，用户还得自己找哪一条是要处理的。这里把「要处理的那几条」
 * 通过 query 带过去，目标页负责：
 *   1. `isFocused(id)` → 给对应行加 `.focus-hit`（描边 + 呼吸），一眼看见；
 *   2. `reveal()` → 数据渲染完把第一条目标滚到视野中间。
 *
 * 约定：`focus` 是逗号分隔的 id 列表（缺口可能命中多条，队列通常一条）。
 * 链接由 `focusLink()` 统一拼，避免各页自己拼出 `?focus=` / `&focus=` 两种写法。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

/** 目标过多时不高亮，避免整屏都在闪（后端也会截断，这里再兜一层） */
const MAX_FOCUS = 60

/** 把页面路径与 focus 拼成可直接 push 的地址，保留原 query */
export function focusLink(to: string, focus: string | null | undefined): string {
  const ids = String(focus || '').split(',').map((s) => s.trim()).filter(Boolean).slice(0, MAX_FOCUS)
  if (!ids.length) return to
  const [path, rawQuery = ''] = to.split('?')
  const params = new URLSearchParams(rawQuery)
  params.set('focus', ids.join(','))
  return `${path}?${params.toString()}`
}

export function useFocusTargets() {
  const route = useRoute()

  function parse(raw: unknown): string[] {
    if (typeof raw !== 'string') return []
    return raw.split(',').map((s) => s.trim()).filter(Boolean).slice(0, MAX_FOCUS)
  }

  /**
   * 目标 id 记成 ref 而不是直接跟随 query：
   * 有些列表页（如流水）会把筛选条件写回 URL，一次 replace 就把 `focus` 抹掉了。
   * 只在新值非空时更新，这样「页面自己清理 query」不会把高亮一起清掉。
   */
  const focusIds = ref<string[]>(parse(route.query.focus))
  watch(
    () => route.query.focus,
    (raw) => {
      const next = parse(raw)
      if (next.length) focusIds.value = next
    },
  )
  const focusSet = computed(() => new Set(focusIds.value))

  /** 该记录是否是本次要处理的目标 */
  function isFocused(id: unknown): boolean {
    if (id == null || id === '') return false
    return focusSet.value.has(String(id))
  }

  let timer: number | undefined

  /**
   * 数据渲染完成后调用：把第一条高亮滚进视野。
   * 只滚一次，之后用户正常浏览，不再抢滚动位置。
   */
  async function reveal(): Promise<void> {
    if (!focusIds.value.length || typeof document === 'undefined') return
    await nextTick()
    const el = document.querySelector<HTMLElement>('.focus-hit')
    if (!el) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' })
    // 滚动动画结束后把焦点交给它，键盘用户可直接回车打开
    window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      if (document.activeElement === document.body) el.focus?.({ preventScroll: true })
    }, reduced ? 0 : 420)
  }

  onBeforeUnmount(() => window.clearTimeout(timer))

  return { focusIds, isFocused, reveal }
}
