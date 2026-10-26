<script setup lang="ts">
/**
 * ECharts 封装（03 §7、07 §8）：
 * 图表使用 ECharts 配置白名单，遵守全局动效与减少动效设置；
 * 无数据时由调用方展示空状态，不渲染伪造数据。
 *
 * 动画策略：
 * - 首次渲染播放完整入场动画（折线描画、柱状生长、饼图展开）；
 * - 数据更新（切换期间、刷新）走 `animationDurationUpdate`，平滑过渡而不是整图重播；
 * - 系统开启「减少动态效果」时自动关闭动画。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, TitleComponent, DatasetComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useThemeStore } from '@/stores/theme'

echarts.use([
  LineChart, BarChart, PieChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent, DatasetComponent,
  CanvasRenderer,
])

const props = defineProps<{ option: Record<string, unknown>; height?: number }>()
const emit = defineEmits<{ (e: 'click', payload: { name?: string; value?: number; seriesName?: string }): void }>()
const el = ref<HTMLElement | null>(null)
const theme = useThemeStore()
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null
let raf = 0
let media: MediaQueryList | null = null
// 首帧必须走标准渲染，否则 replaceMerge 会跳过入场动画（实测：canvas 一帧画完）
let hasRendered = false
let lastWidth = 0
let lastHeight = 0

function prefersReducedMotion(): boolean {
  // 项目设置「减少动效」优先，其次才是系统偏好
  return theme.reducedMotion || (media?.matches ?? false)
}

/** 统一动画基线：单个图表可在 option 里覆盖 */
function withMotion(option: Record<string, unknown>): Record<string, unknown> {
  const reduced = prefersReducedMotion()
  return {
    animation: !reduced,
    animationDuration: 760,
    animationEasing: 'cubicOut',
    animationDelay: (idx: number) => idx * 24,
    animationDurationUpdate: 520,
    animationEasingUpdate: 'cubicInOut',
    ...option,
  }
}

function render() {
  if (!el.value) return
  if (!chart) {
    chart = echarts.init(el.value, undefined, {
      renderer: 'canvas',
      // 只重绘脏区：高刷下每帧重绘面积直接决定能否压进 8.3ms
      useDirtyRect: true,
      // 3x DPR + 120Hz 时像素填充是主要瓶颈，图表限制到 2x 肉眼几乎无差
      devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
    })
  }
  // 首帧：notMerge 触发完整入场动画（折线描画、柱状生长、饼图展开）
  // 后续：replaceMerge 只替换结构，数据变化走更新动画，不会整图重播
  const opts = hasRendered
    ? { replaceMerge: ['series', 'xAxis', 'yAxis', 'grid'], lazyUpdate: true }
    : { notMerge: true }
  chart.setOption(withMotion(props.option) as never, opts as never)
  hasRendered = true
  // 点击图表元素触发下钻（07 §8：点击类别/柱/点进入对应明细）
  chart.off('click')
  chart.on('click', (params: { name?: unknown; value?: unknown; seriesName?: unknown }) => {
    emit('click', {
      name: typeof params.name === 'string' ? params.name : undefined,
      value: typeof params.value === 'number' ? params.value : undefined,
      seriesName: typeof params.seriesName === 'string' ? params.seriesName : undefined,
    })
  })
}

/** 同一帧内多次 option 变化只渲染一次，避免连续 setOption 打断动画 */
function scheduleRender() {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(render)
}

onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  media.addEventListener('change', scheduleRender)
  render()
  if (el.value) {
    // ResizeObserver 首次观察会立刻回调一次；无脑 resize 会把刚启动的入场动画直接 finish，
    // 表现就是「图表一帧画完、没有动画」。这里只在尺寸真的变化时才 resize。
    lastWidth = el.value.clientWidth
    lastHeight = el.value.clientHeight
    observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      if (Math.abs(rect.width - lastWidth) < 1 && Math.abs(rect.height - lastHeight) < 1) return
      lastWidth = rect.width
      lastHeight = rect.height
      chart?.resize()
    })
    observer.observe(el.value)
  }
})

watch(() => props.option, scheduleRender, { deep: true })
// 深浅色切换时重建图表，确保图表调色板同步（03 §2.1）
watch(() => theme.resolved, () => {
  chart?.dispose()
  chart = null
  hasRendered = false
  render()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  media?.removeEventListener('change', scheduleRender)
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" class="echart" :style="{ height: `${height || 260}px` }" />
</template>

<style scoped>
.echart { width: 100%; }
</style>
