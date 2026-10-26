<script setup lang="ts">
/**
 * 期间选择器（日期区间）：用于列表筛选、账单周期、合同起止、保险有效期等。
 *
 * 界面是**两个独立输入框**（开始 / 结束），各自点击弹出同一个双月日历；
 * 也可在左侧常用期间里一键套用（近 7 天 / 本月 / 上月…）。
 * 先点开始框选起点，弹层不关、自动转为选终点，再点一个日期即完成；
 * 选了早于开始日期的终点会自动交换。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { CalendarRange, ChevronLeft, ChevronRight, X } from 'lucide-vue-next'
import {
  addDays, addMonths, monthGrid, monthLabel, parseDay, sameDay, toYmd, today,
} from '@/utils/calendar'

const props = withDefaults(
  defineProps<{
    start?: string | null
    end?: string | null
    placeholder?: string
    disabled?: boolean
    clearable?: boolean
    size?: 'md' | 'sm'
    id?: string
  }>(),
  {
    start: '',
    end: '',
    placeholder: '选择期间',
    disabled: false,
    clearable: true,
    size: 'md',
  },
)

const emit = defineEmits<{
  'update:start': [string]
  'update:end': [string]
  change: [string, string]
}>()

const root = ref<HTMLElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const open = ref(false)
const popStyle = ref<Record<string, string>>({})
const view = ref({ year: today().getFullYear(), month: today().getMonth() })
const picking = ref<'start' | 'end'>('start')
const hovered = ref<Date | null>(null)
const twoPanels = ref(true)

const startDate = computed(() => parseDay(props.start))
const endDate = computed(() => parseDay(props.end))
const todayDate = computed(() => today())

/** 两个框各自的空值文案：明确写「开始 / 结束」，不再沿用列表筛选的「全部日期」 */
const startText = computed(() => props.start || '开始日期')
const endText = computed(() => props.end || '结束日期')

const nextMonth = computed(() => addMonths(new Date(view.value.year, view.value.month, 1), 1))

const panels = computed(() => {
  const list = [view.value]
  if (twoPanels.value) list.push({ year: nextMonth.value.getFullYear(), month: nextMonth.value.getMonth() })
  return list
})

interface Cell {
  date: Date
  inMonth: boolean
}

const grids = computed<Cell[][]>(() =>
  panels.value.map((p) => monthGrid(p.year, p.month).map((c) => ({ date: c.date, inMonth: c.inMonth }))),
)

const presets = computed(() => {
  const t = today()
  const monthStart = new Date(t.getFullYear(), t.getMonth(), 1)
  const lastMonthEnd = new Date(t.getFullYear(), t.getMonth(), 0)
  const lastMonthStart = new Date(lastMonthEnd.getFullYear(), lastMonthEnd.getMonth(), 1)
  const quarterStartMonth = Math.floor(t.getMonth() / 3) * 3
  return [
    { label: '近 7 天', start: addDays(t, -6), end: t },
    { label: '近 30 天', start: addDays(t, -29), end: t },
    { label: '本月', start: monthStart, end: t },
    { label: '上月', start: lastMonthStart, end: lastMonthEnd },
    { label: '本季度', start: new Date(t.getFullYear(), quarterStartMonth, 1), end: t },
    { label: '今年', start: new Date(t.getFullYear(), 0, 1), end: t },
  ]
})

function inRange(d: Date): boolean {
  const s = startDate.value
  const e = endDate.value
  if (s && e) return d.getTime() >= s.getTime() && d.getTime() <= e.getTime()
  // 选终点时用悬停预览
  if (picking.value === 'end' && s && hovered.value) {
    const h = hovered.value
    const lo = Math.min(s.getTime(), h.getTime())
    const hi = Math.max(s.getTime(), h.getTime())
    return d.getTime() >= lo && d.getTime() <= hi
  }
  return false
}

function isEdge(d: Date): 'start' | 'end' | '' {
  if (sameDay(d, startDate.value)) return 'start'
  if (sameDay(d, endDate.value)) return 'end'
  if (picking.value === 'end' && hovered.value && sameDay(d, hovered.value)) return 'end'
  return ''
}

function apply(nextStart: Date, nextEnd: Date) {
  emit('update:start', toYmd(nextStart))
  emit('update:end', toYmd(nextEnd))
  emit('change', toYmd(nextStart), toYmd(nextEnd))
  open.value = false
}

function clickDay(d: Date) {
  const ymd = toYmd(d)

  if (picking.value === 'start') {
    // 已有的结束日期若早于新开始日期，两者交换，避免出现「开始 > 结束」
    if (props.end && ymd > props.end) {
      emit('update:start', props.end)
      emit('update:end', ymd)
      emit('change', props.end, ymd)
      close()
      return
    }
    emit('update:start', ymd)
    emit('change', ymd, props.end || '')
    // 起点定了就接着选终点，弹层不关，少一次点击
    picking.value = 'end'
    hovered.value = null
    return
  }

  const s = props.start || ''
  if (s && ymd < s) {
    emit('update:start', ymd)
    emit('update:end', s)
    emit('change', ymd, s)
  } else {
    emit('update:end', ymd)
    emit('change', s, ymd)
  }
  close()
}

function clearStart() {
  emit('update:start', '')
  emit('change', '', props.end || '')
}

function clearEnd() {
  emit('update:end', '')
  emit('change', props.start || '', '')
}

function clear() {
  emit('update:start', '')
  emit('update:end', '')
  emit('change', '', '')
  open.value = false
}

function shift(step: number) {
  const next = addMonths(new Date(view.value.year, view.value.month, 1), step)
  view.value = { year: next.getFullYear(), month: next.getMonth() }
}

function shiftYear(step: number) {
  view.value = { year: view.value.year + step, month: view.value.month }
}

function position() {
  const el = root.value
  if (!el) return
  const margin = 8
  twoPanels.value = window.innerWidth >= 720
  const width = twoPanels.value ? 620 : Math.min(320, window.innerWidth - margin * 2)
  const height = twoPanels.value ? 396 : 384
  const rect = el.getBoundingClientRect()
  let left = rect.left
  if (left + width > window.innerWidth - margin) left = window.innerWidth - margin - width
  left = Math.max(margin, left)
  let top = rect.bottom + 6
  if (top + height > window.innerHeight - margin) top = Math.max(margin, rect.top - height - 6)
  popStyle.value = {
    position: 'fixed',
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    width: `${Math.round(width)}px`,
    zIndex: '2100',
  }
}

function markListeners(add: boolean) {
  const fn = add ? window.addEventListener : window.removeEventListener
  fn.call(window, 'scroll', position, true)
  fn.call(window, 'resize', position)
}

async function toggle(which: 'start' | 'end') {
  if (props.disabled) return
  if (open.value && picking.value === which) {
    close()
    return
  }
  picking.value = which
  const anchor =
    which === 'start'
      ? startDate.value || todayDate.value
      : endDate.value || startDate.value || todayDate.value
  view.value = { year: anchor.getFullYear(), month: anchor.getMonth() }
  hovered.value = null
  // 先算好位置再打开，避免弹层在 body 末尾闪一帧
  position()
  open.value = true
  await nextTick()
  position()
  markListeners(true)
}

function close() {
  if (!open.value) return
  open.value = false
  markListeners(false)
}

function onDocPointerDown(e: PointerEvent) {
  if (!open.value) return
  const target = e.target as Node
  if (root.value?.contains(target) || pop.value?.contains(target)) return
  close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && open.value) {
    e.stopPropagation()
    close()
  }
}

watch(open, (v) => {
  if (v) {
    document.addEventListener('pointerdown', onDocPointerDown, true)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('pointerdown', onDocPointerDown, true)
    document.removeEventListener('keydown', onKeydown)
    markListeners(false)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointerDown, true)
  document.removeEventListener('keydown', onKeydown)
  markListeners(false)
})
</script>

<template>
  <div ref="root" class="dr" :class="[`size-${size}`]">
    <div class="range-fields">
      <button
        :id="id"
        type="button"
        class="field"
        :class="{ empty: !start, active: open && picking === 'start' }"
        :disabled="disabled"
        :aria-expanded="open && picking === 'start'"
        aria-haspopup="dialog"
        :aria-label="start ? `开始日期 ${start}` : '选择开始日期'"
        @click="toggle('start')"
      >
        <CalendarRange :size="size === 'sm' ? 12 : 14" class="ico" />
        <span class="txt">{{ startText }}</span>
        <span
          v-if="start && clearable && !disabled"
          class="clear"
          role="button"
          tabindex="-1"
          aria-label="清除开始日期"
          @click.stop="clearStart"
        >
          <X :size="11" />
        </span>
      </button>

      <span class="sep" aria-hidden="true">~</span>

      <button
        type="button"
        class="field"
        :class="{ empty: !end, active: open && picking === 'end' }"
        :disabled="disabled"
        :aria-expanded="open && picking === 'end'"
        aria-haspopup="dialog"
        :aria-label="end ? `结束日期 ${end}` : '选择结束日期'"
        @click="toggle('end')"
      >
        <span class="txt">{{ endText }}</span>
        <span
          v-if="end && clearable && !disabled"
          class="clear"
          role="button"
          tabindex="-1"
          aria-label="清除结束日期"
          @click.stop="clearEnd"
        >
          <X :size="11" />
        </span>
      </button>
    </div>

    <Teleport to="body">
      <div
        v-if="open"
        ref="pop"
        class="dr-pop"
        :style="popStyle"
        role="dialog"
        aria-label="选择日期期间"
      >
        <aside class="presets">
          <p class="p-title">常用期间</p>
          <button
            v-for="p in presets"
            :key="p.label"
            type="button"
            class="preset"
            :class="{ active: sameDay(startDate, p.start) && sameDay(endDate, p.end) }"
            @click="apply(p.start, p.end)"
          >
            {{ p.label }}
          </button>
          <button v-if="clearable" type="button" class="preset clear-all" @click="clear">清空期间</button>
        </aside>

        <section class="cal">
          <header class="cal-head">
            <button type="button" class="nav" aria-label="上一年" @click="shiftYear(-1)">
              <ChevronLeft :size="14" /><ChevronLeft :size="14" class="double" />
            </button>
            <button type="button" class="nav" aria-label="上一月" @click="shift(-1)">
              <ChevronLeft :size="16" />
            </button>
            <div class="cal-title">
              <span>{{ monthLabel(view.year, view.month) }}</span>
              <span v-if="twoPanels" class="second">{{ monthLabel(nextMonth.getFullYear(), nextMonth.getMonth()) }}</span>
            </div>
            <button type="button" class="nav" aria-label="下一月" @click="shift(1)">
              <ChevronRight :size="16" />
            </button>
            <button type="button" class="nav" aria-label="下一年" @click="shiftYear(1)">
              <ChevronRight :size="14" /><ChevronRight :size="14" class="double" />
            </button>
          </header>

          <div class="panels" :class="{ single: !twoPanels }">
            <div v-for="(grid, gi) in grids" :key="gi" class="panel">
              <div class="week-row" aria-hidden="true">
                <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
              </div>
              <div class="grid">
                <button
                  v-for="cell in grid"
                  :key="cell.date.getTime()"
                  type="button"
                  class="day"
                  :class="{
                    muted: !cell.inMonth,
                    today: sameDay(cell.date, todayDate),
                    edge: isEdge(cell.date) !== '',
                    start: isEdge(cell.date) === 'start',
                    end: isEdge(cell.date) === 'end',
                    range: inRange(cell.date),
                  }"
                  @click="clickDay(cell.date)"
                  @mouseenter="hovered = cell.date"
                  @mouseleave="hovered = null"
                >
                  {{ cell.date.getDate() }}
                </button>
              </div>
            </div>
          </div>

          <footer class="cal-foot">
            <span class="hint">
              {{ picking === 'start' ? '先点开始日期，再点结束日期' : '再点结束日期；早于开始日期会自动交换' }}
            </span>
            <button v-if="picking === 'end'" type="button" class="ghost" @click="picking = 'start'; hovered = null">
              重新选择
            </button>
          </footer>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.dr { display: block; position: relative; }
.range-fields { display: flex; align-items: center; gap: 5px; }
.field {
  position: relative;
  flex: 1 1 0;
  min-width: 0;
  height: 38px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 20px 0 9px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  font-size: 13.5px;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
  transition: border-color var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) var(--ease-out);
}
.size-sm .field { height: 34px; font-size: 12.5px; padding: 0 18px 0 8px; }
.field:hover { border-color: var(--primary); }
.field.active { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(53, 109, 255, 0.18); }
.field:disabled { opacity: 0.6; cursor: not-allowed; }
.field.empty .txt { color: var(--ink-muted); }
.ico { color: var(--ink-muted); flex: none; }
.field.active .ico { color: var(--primary); }
.field .txt {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.sep { flex: none; font-size: 11.5px; color: var(--ink-muted); }
.clear {
  position: absolute;
  right: 3px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--ink-muted);
}
.clear:hover { background: var(--surface-subtle); color: var(--danger); }

.dr-pop {
  display: flex;
  border-radius: 18px;
  border: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow-float);
  overflow: hidden;
  color: var(--ink-strong);
  font-size: 13px;
}
.presets {
  width: 132px;
  flex: none;
  padding: 12px 10px;
  background: var(--surface-subtle);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.p-title { margin: 0 0 4px; font-size: 11px; color: var(--ink-muted); letter-spacing: 0.04em; }
.preset {
  height: 30px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  font-family: inherit;
  font-size: 12.5px;
  text-align: left;
  padding: 0 9px;
  cursor: pointer;
}
.preset:hover { background: rgba(53, 109, 255, 0.1); color: var(--primary); }
.preset.active { background: var(--primary); color: #fff; }
.clear-all { margin-top: auto; color: var(--ink-muted); }
.clear-all:hover { background: rgba(232, 93, 117, 0.1); color: var(--danger); }
.cal { flex: 1; min-width: 0; padding: 12px; }
.cal-head { display: flex; align-items: center; gap: 2px; margin-bottom: 8px; }
.cal-title { flex: 1; display: flex; justify-content: space-around; font-weight: 600; font-size: 13.5px; }
.cal-title .second { color: var(--ink-muted); font-weight: 500; }
.nav {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--ink);
  cursor: pointer;
}
.nav:hover { background: var(--surface-subtle); color: var(--primary); }
.nav .double + .double { margin-left: -5px; }
.panels { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.panels.single { grid-template-columns: 1fr; }
.week-row { display: grid; grid-template-columns: repeat(7, 1fr); }
.week-row span { text-align: center; font-size: 11px; color: var(--ink-muted); padding: 2px 0 4px; }
.grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px; }
.day {
  position: relative;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--ink-strong);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  border-radius: 0;
}
.day.muted { color: var(--ink-muted); opacity: 0.55; }
.day:hover { background: var(--surface-subtle); }
.day.range { background: rgba(53, 109, 255, 0.12); color: var(--primary); }
.day.edge { background: var(--primary); color: #fff; font-weight: 600; }
.day.start { border-radius: 9px 0 0 9px; }
.day.end { border-radius: 0 9px 9px 0; }
.day.start.end { border-radius: 9px; }
.day.today:not(.edge) { box-shadow: inset 0 0 0 1px var(--primary); color: var(--primary); }
.cal-foot { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.hint { font-size: 11.5px; color: var(--ink-muted); flex: 1; }
.ghost {
  border: none;
  border-radius: 8px;
  background: var(--surface-subtle);
  color: var(--ink);
  font-family: inherit;
  font-size: 12px;
  padding: 4px 8px;
  cursor: pointer;
}
.ghost:hover { color: var(--primary); }
@media (max-width: 719px) {
  .dr-pop { flex-direction: column; max-height: calc(100vh - 32px); overflow: auto; }
  .presets { width: auto; flex-direction: row; flex-wrap: wrap; border-right: none; border-bottom: 1px solid var(--border); padding: 8px; }
  .p-title { display: none; }
  .preset { height: 26px; }
  .clear-all { margin-top: 0; }
  .cal-title { justify-content: center; }
  .cal-title .second { display: none; }
}
</style>
