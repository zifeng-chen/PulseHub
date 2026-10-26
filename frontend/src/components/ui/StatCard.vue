<script setup lang="ts">
/**
 * 顶部统计卡片（各页面统一样式）。
 *
 * 用户要求：账户 / 负债 / 资产 / 脉枢台的顶部卡片统一成同一种样式，
 * 且「图标与标题左右排列」（图标在左、标题在右），下面是金额与补充说明。
 * 金额走 MoneyText，自带滚动动画。
 */
import type { Component } from 'vue'
import MoneyText from '@/components/ui/MoneyText.vue'

withDefaults(
  defineProps<{
    icon: Component
    /** 配色：决定图标渐变，与资产页原有四种色调一致 */
    tone?: 'primary' | 'success' | 'warning' | 'danger' | 'ai' | 'neutral'
    label: string
    /** 金额（字符串或数字）；用 #value 插槽时可省略 */
    value?: string | number | null
    /** true 表示「纯数字」而非金额（如「12 项」），不套金额格式化 */
    plain?: boolean
    currency?: string
    showCurrency?: boolean
    signed?: boolean
    /** 数字后的单位，例如「项」「个」「笔」 */
    unit?: string
    /** 金额下方的一行说明 */
    hint?: string
    /** 可点击（如筛选、跳转） */
    clickable?: boolean
    active?: boolean
  }>(),
  {
    tone: 'primary',
    showCurrency: true,
    signed: false,
    plain: false,
    clickable: false,
    active: false,
  },
)

const emit = defineEmits<{ click: [] }>()

function onClick() {
  emit('click')
}
</script>

<template>
  <component
    :is="clickable ? 'button' : 'article'"
    class="stat-card"
    :class="[`tone-${tone}`, { clickable, active }]"
    :type="clickable ? 'button' : undefined"
    @click="clickable ? onClick() : undefined"
  >
    <!-- 图标在左、标题在右 -->
    <header class="sc-head">
      <span class="sc-ico"><component :is="icon" :size="16" /></span>
      <p class="sc-k">
        {{ label }}
        <slot name="label-extra" />
      </p>
    </header>

    <p class="sc-v">
      <slot name="value">
        <template v-if="plain">{{ value }}</template>
        <MoneyText
          v-else
          :value="value" :currency="currency"
          :show-currency="showCurrency" :signed="signed"
        />
        <small v-if="unit" class="sc-unit">{{ unit }}</small>
      </slot>
    </p>

    <p v-if="hint" class="sc-sub">{{ hint }}</p>
    <slot name="footer" />
  </component>
</template>

<style scoped>
.stat-card {
  display: flex; flex-direction: column; gap: 7px;
  padding: 13px 15px 14px;
  border-radius: var(--glass-radius-card);
  border: var(--glass-border-line);
  background: var(--glass-1);
  text-align: left;
  color: inherit;
  font: inherit;
  transition: border-color var(--motion-fast) var(--ease-out),
              transform var(--motion-fast) var(--ease-out),
              box-shadow var(--motion-fast) var(--ease-out);
}
.stat-card.clickable { cursor: pointer; }
.stat-card.clickable:hover { border-color: var(--primary); transform: translateY(-1px); }
.stat-card.active { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(53, 109, 255, 0.12); }

.sc-head { display: flex; align-items: center; gap: 9px; min-width: 0; }
.sc-ico {
  flex: none; width: 30px; height: 30px; border-radius: 10px;
  display: grid; place-items: center; color: #fff;
  background: linear-gradient(140deg, #9aa7bd, #6b7a92);
  box-shadow: 0 6px 14px -9px rgba(16, 32, 64, 0.6);
}
.tone-primary .sc-ico { background: linear-gradient(140deg, #6f97ff, #356dff); box-shadow: 0 8px 16px -9px rgba(53,109,255,.9); }
.tone-success .sc-ico { background: linear-gradient(140deg, #4fdcae, #17a86f); box-shadow: 0 8px 16px -9px rgba(23,168,111,.9); }
.tone-warning .sc-ico { background: linear-gradient(140deg, #fbc471, #e2962a); box-shadow: 0 8px 16px -9px rgba(226,150,42,.9); }
.tone-danger .sc-ico { background: linear-gradient(140deg, #f78ca1, #e05b74); box-shadow: 0 8px 16px -9px rgba(224,91,116,.9); }
.tone-ai .sc-ico { background: linear-gradient(140deg, #a58bff, #7c5cfc); box-shadow: 0 8px 16px -9px rgba(124,92,252,.9); }

.sc-k {
  margin: 0; min-width: 0; font-size: 12.5px; color: var(--ink-muted);
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
}
.sc-v {
  margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.01em;
  color: var(--ink-strong); line-height: 1.2;
  display: flex; align-items: baseline; gap: 4px; flex-wrap: wrap;
}
.sc-v :deep(.money) { font-size: inherit; font-weight: inherit; }
.sc-unit { font-size: 13px; font-weight: 500; color: var(--ink-muted); }
.sc-sub { margin: 0; font-size: 11.5px; line-height: 1.5; color: var(--ink-muted); }

@media (max-width: 640px) {
  .stat-card { padding: 11px 12px 12px; }
  .sc-v { font-size: 20px; }
  .sc-ico { width: 27px; height: 27px; border-radius: 9px; }
}
</style>
