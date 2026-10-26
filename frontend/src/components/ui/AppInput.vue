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
      :class="{ 'has-suffix': sensitive || type === 'password', 'has-caps': capsLock && (sensitive || type === 'password') }"
      @input="onInput"
      @keydown="syncCaps"
      @keyup="syncCaps"
      @keyup.enter="emit('enter')"
      @blur="capsLock = false"
    />
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
