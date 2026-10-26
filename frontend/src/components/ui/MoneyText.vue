<script setup lang="ts">
/** 金额显示（03 §3、23 §1）：金额以字符串返回，等宽数字，币种可标注。
 *  默认精度跟随家庭「金额精度」设置（08 §4，默认两位）；显式传 precision 时以传入为准。
 *
 *  动画：数值变化时做 600ms 的滚动过渡（用户要求「所有资金的金额增加动画」）。
 *  - 首次挂载从 0 滚到目标值；
 *  - 之后从当前显示值滚到新值，避免直接跳变；
 *  - 遵守系统的「减少动态效果」设置，开启时直接显示最终值。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'
import { formatAmount } from '@/utils/format'

const ui = useUiStore()
const theme = useThemeStore()

const props = withDefaults(
  defineProps<{
    value: string | number | null | undefined
    currency?: string
    showCurrency?: boolean
    precision?: number
    align?: 'left' | 'right'
    signed?: boolean
    /** 关掉滚动动画（例如需要逐行刷新的地方） */
    animated?: boolean
  }>(),
  { showCurrency: false, align: 'right', signed: false, animated: true },
)

/** 有效小数位：显式 precision 优先，否则用家庭配置（默认两位）。 */
const digits = computed(() => props.precision ?? ui.amountPrecision)

const reduceMotion = () =>
  theme.reducedMotion
  || (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )

/** 动画中的当前数值；null 表示「不动画，直接用原值」 */
const animatedValue = ref<number | null>(null)
let frame = 0

function stop() {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
}

watch(
  () => [props.value, props.animated, digits.value] as const,
  ([raw]) => {
    stop()
    const to = Number(raw)
    const disabled = !props.animated || raw === null || raw === undefined || raw === ''
      || Number.isNaN(to) || reduceMotion()
    if (disabled) {
      animatedValue.value = null
      return
    }
    const from = animatedValue.value ?? 0
    if (Math.abs(to - from) < 0.005) {
      animatedValue.value = to
      return
    }
    const started = performance.now()
    const duration = 600
    const step = (now: number) => {
      const progress = Math.min(1, (now - started) / duration)
      const eased = 1 - (1 - progress) ** 3   // easeOutCubic：先快后慢，收尾稳
      animatedValue.value = progress >= 1 ? to : from + (to - from) * eased
      if (progress < 1) frame = requestAnimationFrame(step)
      else frame = 0
    }
    frame = requestAnimationFrame(step)
  },
  { immediate: true },
)

onBeforeUnmount(stop)

/** 参与格式化显示的值 */
const shownValue = computed(() =>
  animatedValue.value === null ? props.value : animatedValue.value,
)

const text = computed(() => {
  const raw = formatAmount(shownValue.value, digits.value)
  // signed 模式自己控制正负号，先去掉格式化结果里的「-」，否则会出现「¥−-76.55」
  return props.signed ? raw.replace(/^-/, '') : raw
})

const symbol = computed(() => {
  const c = props.currency || ''
  return { CNY: '¥', USD: '$', EUR: '€', JPY: 'JP¥', HKD: 'HK$' }[c] || (c ? `${c} ` : '')
})

const sign = computed(() => {
  const n = Number(props.value)
  if (!props.signed || Number.isNaN(n) || n === 0) return ''
  return n > 0 ? '+' : '−'
})

const tone = computed(() => {
  if (!props.signed) return ''
  const n = Number(props.value)
  if (Number.isNaN(n) || n === 0) return ''
  // 中国习惯：涨为红、跌为绿
  return n > 0 ? 'up' : 'down'
})
</script>

<template>
  <span class="money" :class="[`align-${align}`, tone]">
    {{ sign }}<span v-if="showCurrency" class="sym">{{ symbol }}</span>{{ text }}
  </span>
</template>

<style scoped>
.money { font-variant-numeric: tabular-nums; font-feature-settings: 'tnum' 1; color: var(--ink-strong); }
.align-right { text-align: right; }
.sym { margin-right: 2px; color: var(--ink-muted); }
.up { color: #D64550; }
.down { color: var(--success); }
@media (prefers-reduced-motion: reduce) {
  .money { transition: none; }
}
</style>
