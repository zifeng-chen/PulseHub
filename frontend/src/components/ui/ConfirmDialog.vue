<script setup lang="ts">
/**
 * 全局统一二次确认（03 §5.1、11 §5、20 §5–§6）。
 * 危险动作必须显示影响范围、是否可恢复、操作原因与审计说明；
 * 确认按钮使用明确中文动作（确认删除 / 确认停用 / 确认冲销 …），不使用模糊的"确定"。
 */
import { computed, ref, watch } from 'vue'
import { useUiStore } from '@/stores/ui'
import AppModal from './AppModal.vue'

const ui = useUiStore()
const reason = ref('')
const submitting = ref(false)

const options = computed(() => ui.confirm)
const needsReason = computed(() => Boolean(options.value?.requireReason))

watch(
  () => ui.confirm,
  () => {
    reason.value = ''
    submitting.value = false
  },
)

function cancel() {
  ui.resolveConfirm({ ok: false })
}

function confirm() {
  if (needsReason.value && !reason.value.trim()) return
  submitting.value = true
  ui.resolveConfirm({ ok: true, reason: reason.value.trim() })
}
</script>

<template>
  <AppModal
    :open="Boolean(options)"
    :title="options?.title || '确认操作'"
    :danger="Boolean(options?.danger)"
    size="sm"
    :mask-closable="false"
    :esc-closable="true"
    :confirm-text="options?.confirmText || '确认'"
    :cancel-text="options?.cancelText || '取消'"
    :confirm-loading="submitting"
    @close="cancel"
    @confirm="confirm"
  >
    <p class="msg">{{ options?.message }}</p>
    <ul v-if="options?.detail?.length" class="details">
      <li v-for="(d, i) in options.detail" :key="i">{{ d }}</li>
    </ul>
    <p v-if="options?.irreversible" class="irreversible">
      此操作不可逆，系统会记录操作人、时间与原因并写入审计。
    </p>
    <div v-if="needsReason" class="reason">
      <label for="confirm-reason">{{ options?.reasonLabel || '操作原因（必填）' }}</label>
      <textarea id="confirm-reason" v-model="reason" rows="2" placeholder="请简要说明操作原因" />
    </div>
  </AppModal>
</template>

<style scoped>
.msg { color: var(--ink-strong); font-size: 14px; }
.details {
  margin-top: var(--space-3);
  padding-left: 18px;
  list-style: disc;
  color: var(--ink);
  font-size: 13px;
}
.details li { margin-bottom: 4px; }
.irreversible {
  margin-top: var(--space-3);
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: rgba(232, 93, 117, 0.1);
  border: 1px solid rgba(232, 93, 117, 0.32);
  color: var(--danger);
  font-size: 12.5px;
}
.reason { margin-top: var(--space-3); display: flex; flex-direction: column; gap: 6px; }
.reason label { font-size: 13px; color: var(--ink); }
.reason textarea {
  width: 100%;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--glass-1);
  color: var(--ink-strong);
  font-family: inherit;
  font-size: 14px;
  resize: vertical;
}
</style>
