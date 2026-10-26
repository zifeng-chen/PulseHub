<script setup lang="ts">
/**
 * 统一弹窗（03 §5.1、20 §6）。
 * 统一标题左对齐、右上角关闭、底部按钮"取消在左、确认在右"。
 * 点击遮罩是否关闭由类型决定：信息弹窗可关闭；未保存表单与危险确认不可误关闭。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { isTopOverlay, popOverlay, pushOverlay } from '@/composables/overlayStack'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    size?: 'sm' | 'md' | 'lg' | 'full'
    danger?: boolean
    maskClosable?: boolean
    escClosable?: boolean
    confirmText?: string
    cancelText?: string
    confirmLoading?: boolean
    hideFooter?: boolean
  }>(),
  {
    size: 'md',
    danger: false,
    maskClosable: true,
    escClosable: true,
    confirmText: '确认',
    cancelText: '取消',
    confirmLoading: false,
    hideFooter: false,
  },
)

const emit = defineEmits<{ close: []; confirm: [] }>()
const panel = ref<HTMLElement | null>(null)
const overlayId = Symbol('modal')
let previousFocus: HTMLElement | null = null

function onMaskClick() {
  if (props.maskClosable) emit('close')
}

function onKeydown(e: KeyboardEvent) {
  if (!props.open || !isTopOverlay(overlayId)) return
  if (e.key === 'Escape' && props.escClosable) emit('close')
  if (e.key === 'Tab' && panel.value) {
    const focusables = panel.value.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    if (!focusables.length) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    } else if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    }
  }
}

// 弹窗打开后焦点进入弹窗，关闭后回到触发按钮（11 §6）
watch(
  () => props.open,
  (v) => {
    if (v) {
      previousFocus = document.activeElement as HTMLElement
      pushOverlay(overlayId)
      requestAnimationFrame(() => {
        panel.value?.querySelector<HTMLElement>('button, input, select, textarea')?.focus()
      })
    } else {
      popOverlay(overlayId)
      previousFocus?.focus()
    }
  },
  { immediate: true },
)

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  popOverlay(overlayId)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="modal-mask" :style="{ background: 'var(--scrim)' }" @click.self="onMaskClick">
        <div
          ref="panel"
          class="modal-card"
          :class="[`is-${size}`, { 'is-danger': danger }]"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <div v-if="danger" class="danger-edge" aria-hidden="true" />
          <header class="modal-head">
            <h3>{{ title }}</h3>
            <button class="close" type="button" aria-label="关闭" @click="emit('close')">×</button>
          </header>
          <div class="modal-body"><slot /></div>
          <footer v-if="!hideFooter" class="modal-foot">
            <slot name="footer">
              <button class="btn-cancel" type="button" @click="emit('close')">{{ cancelText }}</button>
              <button
                class="btn-confirm"
                :class="{ danger }"
                type="button"
                :disabled="confirmLoading"
                @click="emit('confirm')"
              >
                {{ confirmLoading ? '处理中…' : confirmText }}
              </button>
            </slot>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: grid;
  place-items: center;
  padding: var(--space-4);
}
.modal-card {
  position: relative;
  width: 100%;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--glass-3);
  border: var(--glass-border-line);
  border-radius: var(--glass-radius-panel);
  box-shadow: var(--glass-shadow-heavy), var(--glass-inner-highlight);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  overflow: hidden;
}
.is-sm { max-width: 420px; }
.is-md { max-width: 560px; }
.is-lg { max-width: 760px; }
.is-full { max-width: min(1080px, 96vw); height: 88vh; }
.is-danger { border-color: rgba(232, 93, 117, 0.5); }
.danger-edge { height: 3px; background: linear-gradient(90deg, var(--danger), rgba(232, 93, 117, 0.2)); }

.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border);
}
.modal-head h3 { text-align: left; }
.close {
  width: 30px;
  height: 30px;
  border: none;
  background: transparent;
  color: var(--ink-muted);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  border-radius: var(--radius-sm);
}
.close:hover { color: var(--ink-strong); background: var(--glass-1); }

.modal-body { padding: var(--space-5); overflow: auto; }
.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--border);
}
.btn-cancel,
.btn-confirm {
  height: 38px;
  padding: 0 18px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-2);
  color: var(--ink-strong);
  cursor: pointer;
  font-size: 14px;
}
.btn-confirm {
  border-color: transparent;
  color: #fff;
  background: linear-gradient(135deg, var(--primary), var(--accent-ai));
}
.btn-confirm.danger { background: var(--danger); }
.btn-confirm:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
