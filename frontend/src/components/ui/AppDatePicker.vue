<script setup lang="ts">
/**
 * 统一日期选择器（替换所有手写 YYYY-MM-DD 文本输入）。
 *
 * 支持三种取值格式：
 *  - date：YYYY-MM-DD（默认）
 *  - datetime：YYYY-MM-DDTHH:mm:00（occurred_at 等时间字段，"账本墙钟"，不带时区偏移）
 *  - month：YYYY-MM（卡有效期等）
 *
 * 交互：点击触发器弹出日历；支持今天/昨天等快捷期间、清除、Esc 关闭、
 * 点击外部关闭；弹层 Teleport 到 body，避免被抽屉/弹窗的 overflow 裁剪。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight, X } from 'lucide-vue-next'
import {
  addDays, addMonths, fmtDisplay, monthGrid, monthLabel, pad2, parseDay, parseMoment,
  sameDay, toHm, toYm, today, weekDayLabel,
} from '@/utils/calendar'

const props = withDefaults(
  defineProps<{
    modelValue?: string | null
    placeholder?: string
    disabled?: boolean
    clearable?: boolean
    valueFormat?: 'date' | 'datetime' | 'month'
    withTime?: boolean
    size?: 'md' | 'sm'
    id?: string
    min?: string
    max?: string
  }>(),
  {
    modelValue: '',
    placeholder: '选择日期',
    disabled: false,
    clearable: true,
    valueFormat: 'date',
    withTime: false,
    size: 'md',
  },
)

const emit = defineEmits<{
  'update:modelValue': [string]
  change: [string]
}>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const open = ref(false)
const popStyle = ref<Record<string, string>>({})
const align = ref<'left' | 'right'>('left')

const selected = computed(() => {
  if (!props.modelValue) return null
  const d = props.valueFormat === 'date'
    ? parseDay(props.modelValue)
    : parseMoment(props.modelValue)
  return d ? new Date(d.getFullYear(), d.getMonth(), d.getDate()) : null
})

const selectedTime = computed(() => {
  if (!props.withTime || !props.modelValue) return ''
  const d = parseMoment(props.modelValue)
  return d ? toHm(d) : ''
})

const view = ref({ year: today().getFullYear(), month: today().getMonth() })
const timeValue = ref('00:00')

watch(
  () => props.modelValue,
  () => {
    const d = selected.value
    if (d) view.value = { year: d.getFullYear(), month: d.getMonth() }
    timeValue.value = selectedTime.value || '00:00'
  },
  { immediate: true },
)

const display = computed(() => {
  if (!props.modelValue) return ''
  if (props.valueFormat === 'month') {
    const d = parseDay(props.modelValue)
    return d ? `${d.getFullYear()}-${pad2(d.getMonth() + 1)}` : props.modelValue
  }
  const d = selected.value
  if (!d) return props.modelValue
  return fmtDisplay(d, props.withTime, selectedTime.value)
})

const cells = computed(() => monthGrid(view.value.year, view.value.month))
const todayDate = computed(() => today())
const minDay = computed(() => (props.min ? parseDay(props.min) : null))
const maxDay = computed(() => (props.max ? parseDay(props.max) : null))

const presets = computed(() => {
  const t = today()
  return [
    { label: '今天', date: t },
    { label: '昨天', date: addDays(t, -1) },
    { label: '一周前', date: addDays(t, -7) },
    { label: '本月 1 日', date: new Date(t.getFullYear(), t.getMonth(), 1) },
  ]
})

function isDisabled(d: Date): boolean {
  if (minDay.value && d.getTime() < minDay.value.getTime()) return true
  if (maxDay.value && d.getTime() > maxDay.value.getTime()) return true
  return false
}

function emitValue(date: Date) {
  let value = ''
  if (props.valueFormat === 'month') {
    value = toYm(date)
  } else if (props.valueFormat === 'datetime') {
    // 有时间输入时用用户选择的时间；否则保留原值里的时间，避免只改日期把时间清成 00:00
    const time = props.withTime ? (timeValue.value || '00:00') : (selectedTime.value || '00:00')
    // 不带时区偏移：业务时间是「账本本地墙钟」，带设备偏移会让出差/换时区时
    // 提交的时间被后端再折算一次（+8 小时后入账）。
    value = `${fmtDisplay(date)}T${time}:00`
  } else {
    value = fmtDisplay(date)
  }
  emit('update:modelValue', value)
  emit('change', value)
}

/**
 * 三级面板：日 → 月 → 年。
 * 逐月点箭头翻 30 年前的日期太痛苦，点标题就能直接跳年/跳月。
 */
const panel = ref<'day' | 'month' | 'year'>('day')

function resetPanel() {
  panel.value = props.valueFormat === 'month' ? 'month' : 'day'
}

const yearRange = computed(() => {
  const base = Math.floor(view.value.year / 12) * 12
  return Array.from({ length: 12 }, (_, i) => base + i)
})

function togglePanel() {
  if (props.valueFormat === 'month') {
    panel.value = panel.value === 'year' ? 'month' : 'year'
    return
  }
  // 日 → 月 → 年 → 日 循环。之前 day 类型只在日/月之间来回，永远进不到年面板，
  // 表现就是「点标题想换年份，点了没反应」。
  if (panel.value === 'day') panel.value = 'month'
  else if (panel.value === 'month') panel.value = 'year'
  else panel.value = 'day'
}

/** 月面板里选月：月份类型直接落值，日期类型回到日面板 */
function chooseMonth(month: number) {
  view.value = { year: view.value.year, month }
  if (props.valueFormat === 'month') {
    pickMonth(month)
  } else {
    panel.value = 'day'
  }
}

function chooseYear(year: number) {
  view.value = { year, month: view.value.month }
  panel.value = 'month'
}

function shiftYear(step: number) {
  view.value = { year: view.value.year + step, month: view.value.month }
}

function shiftYearPage(step: number) {
  view.value = { year: view.value.year + step * 12, month: view.value.month }
}

function pick(date: Date) {
  if (isDisabled(date)) return
  emitValue(date)
  open.value = false
}

function pickMonth(month: number) {
  const date = new Date(view.value.year, month, 1)
  if (isDisabled(date)) return
  emitValue(date)
  open.value = false
}

function clear() {
  emit('update:modelValue', '')
  emit('change', '')
  open.value = false
}

function shift(step: number) {
  const next = addMonths(new Date(view.value.year, view.value.month, 1), step)
  view.value = { year: next.getFullYear(), month: next.getMonth() }
}

function onTimeInput(e: Event) {
  timeValue.value = (e.target as HTMLInputElement).value || '00:00'
  const d = selected.value
  if (d) emitValue(d)
}

function position() {
  const el = trigger.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const width = props.valueFormat === 'month' ? 268 : 296
  const height = props.valueFormat === 'month' ? 300 : (props.withTime ? 396 : 356)
  const margin = 8
  let left = rect.left
  if (left + width > window.innerWidth - margin) left = Math.max(margin, rect.right - width)
  let top = rect.bottom + 6
  if (top + height > window.innerHeight - margin) {
    top = Math.max(margin, rect.top - height - 6)
  }
  align.value = left < rect.left ? 'right' : 'left'
  popStyle.value = {
    position: 'fixed',
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    width: `${width}px`,
    zIndex: '2100',
  }
}

async function toggle() {
  if (props.disabled) return
  if (open.value) {
    open.value = false
    return
  }
  resetPanel()
  const d = selected.value
  view.value = d
    ? { year: d.getFullYear(), month: d.getMonth() }
    : { year: today().getFullYear(), month: today().getMonth() }
  timeValue.value = selectedTime.value || toHm(new Date())
  // 先算好位置再打开，避免弹层在 body 末尾闪一帧
  position()
  open.value = true
  await nextTick()
  position()
  window.addEventListener('scroll', position, true)
  window.addEventListener('resize', position)
}

function close() {
  if (!open.value) return
  open.value = false
  window.removeEventListener('scroll', position, true)
  window.removeEventListener('resize', position)
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
    window.removeEventListener('scroll', position, true)
    window.removeEventListener('resize', position)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointerDown, true)
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('scroll', position, true)
  window.removeEventListener('resize', position)
})
</script>

<template>
  <div ref="root" class="dp" :class="[`size-${size}`]">
    <button
      :id="id"
      ref="trigger"
      type="button"
      class="trigger"
      :class="{ empty: !modelValue, open }"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="dialog"
      @click="toggle"
    >
      <CalendarDays :size="size === 'sm' ? 13 : 15" class="ico" />
      <span class="txt">{{ display || placeholder }}</span>
      <span
        v-if="modelValue && clearable && !disabled"
        class="clear"
        role="button"
        tabindex="-1"
        aria-label="清除日期"
        @click.stop="clear"
      >
        <X :size="12" />
      </span>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="pop"
        class="dp-pop"
        :class="{ right: align === 'right' }"
        :style="popStyle"
        role="dialog"
        :aria-label="valueFormat === 'month' ? '选择月份' : '选择日期'"
      >
        <header class="pop-head">
          <button type="button" class="nav" aria-label="上一页"
                  @click="panel === 'year' ? shiftYearPage(-1) : shift(valueFormat === 'month' ? -12 : -1)">
            <ChevronLeft :size="16" />
          </button>
          <!-- 标题可点：日 → 月 → 年，直接跳年/跳月，不用逐月翻 -->
          <div class="title">
            <button type="button" class="title-btn" :aria-expanded="panel !== 'day'"
                    @click="togglePanel">
              <template v-if="panel === 'year'">{{ yearRange[0] }} – {{ yearRange[11] }}</template>
              <template v-else-if="panel === 'month' || valueFormat === 'month'">{{ view.year }} 年</template>
              <template v-else>{{ monthLabel(view.year, view.month) }}</template>
              <ChevronDown :size="13" class="td" />
            </button>
          </div>
          <button type="button" class="nav" aria-label="下一页"
                  @click="panel === 'year' ? shiftYearPage(1) : shift(valueFormat === 'month' ? 12 : 1)">
            <ChevronRight :size="16" />
          </button>
        </header>

        <!-- 年面板：一次看 12 年 -->
        <div v-if="panel === 'year'" class="months year-grid">
          <button
            v-for="y in yearRange"
            :key="y"
            type="button"
            class="month-cell"
            :class="{ active: view.year === y }"
            @click="chooseYear(y)"
          >
            {{ y }}
          </button>
        </div>

        <!-- 月面板 -->
        <div v-else-if="panel === 'month'" class="months">
          <button
            v-for="m in 12"
            :key="m"
            type="button"
            class="month-cell"
            :class="{ active: selected && selected.getFullYear() === view.year && selected.getMonth() === m - 1 }"
            @click="chooseMonth(m - 1)"
          >
            {{ m }} 月
          </button>
        </div>

        <template v-else>
          <div class="week-row" aria-hidden="true">
            <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
          </div>
          <div class="grid">
            <button
              v-for="cell in cells"
              :key="cell.date.getTime()"
              type="button"
              class="day"
              :class="{
                muted: !cell.inMonth,
                today: sameDay(cell.date, todayDate),
                active: sameDay(cell.date, selected),
                disabled: isDisabled(cell.date),
              }"
              :disabled="isDisabled(cell.date)"
              @click="pick(cell.date)"
            >
              {{ cell.date.getDate() }}
            </button>
          </div>

          <div v-if="withTime" class="time-row">
            <span class="time-label">时间</span>
            <input class="time-input" type="time" :value="timeValue" step="60" @input="onTimeInput" />
            <span class="time-hint">{{ selected ? weekDayLabel(selected) : '' }}</span>
          </div>
        </template>

        <footer class="pop-foot">
          <div class="presets">
            <button
              v-for="p in presets"
              :key="p.label"
              type="button"
              class="ghost"
              :disabled="isDisabled(p.date)"
              @click="pick(p.date)"
            >
              {{ p.label }}
            </button>
          </div>
          <button v-if="clearable" type="button" class="ghost danger" @click="clear">清除</button>
        </footer>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.dp { display: block; position: relative; }
.trigger {
  width: 100%;
  height: 38px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px 0 11px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
  transition: border-color var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) var(--ease-out);
}
.size-sm .trigger { height: 34px; font-size: 13px; padding: 0 8px 0 9px; }
.trigger:hover { border-color: var(--primary); }
.trigger.open { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(53, 109, 255, 0.18); }
.trigger:disabled { opacity: 0.6; cursor: not-allowed; }
.trigger.empty .txt { color: var(--ink-muted); }
.ico { color: var(--ink-muted); flex: none; }
.trigger.open .ico { color: var(--primary); }
.txt { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.clear {
  flex: none;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--ink-muted);
  background: transparent;
}
.clear:hover { background: var(--surface-subtle); color: var(--danger); }

.dp-pop {
  border-radius: 16px;
  border: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow-float);
  padding: 12px;
  color: var(--ink-strong);
  font-size: 13px;
}

/* 可点的年月标题：日 → 月 → 年 逐级跳 */
.title-btn {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 8px; border: 1px solid transparent;
  background: none; cursor: pointer;
  font-weight: 600; font-size: 14px; color: var(--ink-strong);
  transition: background 0.15s ease, border-color 0.15s ease;
}
.title-btn:hover { background: var(--glass-1); border-color: var(--border); }
.title-btn .td { color: var(--ink-muted); transition: transform 0.16s ease; }
.title-btn[aria-expanded="true"] .td { transform: rotate(180deg); }
/* 年面板：4 列，一次看 12 年 */
.months.year-grid { grid-template-columns: repeat(4, 1fr); }
.months.year-grid .month-cell { height: 38px; font-size: 13px; }
.pop-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.title { font-weight: 600; font-size: 14px; }
.nav {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  cursor: pointer;
}
.nav:hover { background: var(--surface-subtle); color: var(--primary); }
.week-row { display: grid; grid-template-columns: repeat(7, 1fr); margin-bottom: 4px; }
.week-row span { text-align: center; font-size: 11px; color: var(--ink-muted); padding: 2px 0; }
.grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
.day {
  height: 32px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--ink-strong);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.day:hover { background: var(--surface-subtle); }
.day.muted { color: var(--ink-muted); opacity: 0.65; }
.day.today { box-shadow: inset 0 0 0 1px var(--primary); color: var(--primary); font-weight: 600; }
.day.active { background: var(--primary); color: #fff; font-weight: 600; box-shadow: none; }
.day.disabled { opacity: 0.3; cursor: not-allowed; }
.months { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 4px 0 8px; }
.month-cell {
  height: 44px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--surface-subtle);
  color: var(--ink-strong);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.month-cell:hover { border-color: var(--primary); color: var(--primary); }
.month-cell.active { background: var(--primary); color: #fff; }
.time-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border); }
.time-label { font-size: 12px; color: var(--ink-muted); }
.time-input {
  height: 30px;
  padding: 0 6px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  font-family: inherit;
}
.time-hint { margin-left: auto; font-size: 12px; color: var(--ink-muted); }
.pop-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.presets { display: flex; flex-wrap: wrap; gap: 4px; flex: 1; }
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
.ghost:hover { color: var(--primary); background: rgba(53, 109, 255, 0.1); }
.ghost.danger:hover { color: var(--danger); background: rgba(232, 93, 117, 0.1); }
.ghost:disabled { opacity: 0.4; cursor: not-allowed; }
@media (max-width: 420px) {
  .dp-pop { padding: 10px; }
}
</style>
