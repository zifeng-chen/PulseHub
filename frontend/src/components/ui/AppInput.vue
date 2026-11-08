<script setup lang="ts">
/** 统一输入控件：文本 / 密码 / 数字（03 §5、17 §6.3）。 */
import { computed, ref } from 'vue'
import { ArrowBigUp, Eye, EyeOff } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    modelValue: string | number | null | undefined
    type?: 'text' | 'password' | 'number' | 'email'
    placeholder?: string
    disabled?: boolean
    sensitive?: boolean
    id?: string
    autocomplete?: string
    min?: number | string
    max?: number | string
    step?: number | string
    /** 密码掩码样式：默认圆点；coin 用金币图形（仅视觉层，底层仍是 password） */
    mask?: 'disc' | 'coin'
  }>(),
  { type: 'text', sensitive: false, disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [string]; enter: [] }>()
const revealed = ref(false)
// 大写锁定提示：Windows / macOS 的原生提示图标大小不受控，自己画一个固定尺寸的
const capsLock = ref(false)

function syncCaps(e: KeyboardEvent | MouseEvent) {
  if (typeof e.getModifierState !== 'function') return
  capsLock.value = e.getModifierState('CapsLock')
}

const inputType = computed(() => {
  if (props.type === 'password' || props.sensitive) return revealed.value ? 'text' : 'password'
  return props.type
})

const valueLength = computed(() => String(props.modelValue ?? '').length)
/** 金币掩码只在「密码类型 + 未点显示 + 有内容」时接管视觉 */
const coinMasked = computed(() =>
  props.mask === 'coin'
  && (props.type === 'password' || props.sensitive)
  && !revealed.value
  && valueLength.value > 0,
)
const coinCount = computed(() => Math.min(valueLength.value, 26))

function onInput(e: Event) {
  emit('update:modelValue', (e.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="wrap">
    <input
      :id="id"
      class="input"
      :type="inputType"
      :value="modelValue ?? ''"
      :placeholder="placeholder"
      :disabled="disabled"
      :autocomplete="autocomplete"
      :min="min"
      :max="max"
      :step="step"
      :class="{
        'has-suffix': sensitive || type === 'password',
        'has-caps': capsLock && (sensitive || type === 'password'),
        'masked-coins': coinMasked,
      }"
      @input="onInput"
      @keydown="syncCaps"
      @keyup="syncCaps"
      @keyup.enter="emit('enter')"
      @blur="capsLock = false"
    />
    <span v-if="coinMasked" class="coins" aria-hidden="true">
      <i v-for="n in coinCount" :key="n" class="coin" />
    </span>
    <span
      v-if="capsLock && (sensitive || type === 'password')"
      class="caps" aria-label="大写锁定已开启" title="大写锁定已开启"
    >
      <ArrowBigUp :size="13" />
    </span>
    <button
      v-if="sensitive || type === 'password'"
      class="reveal"
      type="button"
      :aria-label="revealed ? '隐藏内容' : '显示内容'"
      @click="revealed = !revealed"
    >
      <component :is="revealed ? EyeOff : Eye" :size="15" />
    </button>
  </div>
</template>

<style scoped>
.wrap { position: relative; display: block; }
.input {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  font-size: 14px;
  font-family: inherit;
  transition: border-color var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) var(--ease-out);
}
.input.has-suffix { padding-right: 38px; }
.input.has-caps { padding-right: 56px; }
.input::placeholder { color: var(--ink-muted); }
.input:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(53, 109, 255, 0.18);
}
.input:disabled { opacity: 0.6; cursor: not-allowed; }

/* 金币掩码：底层输入文字完全透明，由上层金币接管视觉。
   光标一并隐藏——金币按固定间距排布，光标位置无法与之对齐。 */
.input.masked-coins {
  color: transparent;
  -webkit-text-fill-color: transparent;
  caret-color: transparent;
}
.input.masked-coins:-webkit-autofill {
  -webkit-text-fill-color: transparent !important;
}
.coins {
  position: absolute;
  left: 13px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  gap: 5px;
  max-width: calc(100% - 58px);
  overflow: hidden;
  pointer-events: none;
}
.coin {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: radial-gradient(circle at 32% 28%, #FFF3C9 0%, #F4CE62 38%, #D9A520 72%, #A9761A 100%);
  box-shadow: inset 0 0 0 1px rgba(146, 96, 12, 0.5), 0 0 6px rgba(244, 206, 98, 0.45);
  animation: coin-pop 0.26s var(--ease-out) both;
}
@keyframes coin-pop {
  from { transform: scale(0.3); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}
.caps {
  position: absolute;
  top: 50%;
  right: 32px;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  color: var(--warning);
  pointer-events: none;
}
.caps :deep(svg), .reveal :deep(svg) { width: 15px; height: 15px; flex: none; display: block; }
.reveal {
  position: absolute;
  top: 50%;
  right: 8px;
  transform: translateY(-50%);
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: none;
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
}
.reveal:hover { color: var(--ink-strong); }
</style>
