<script setup lang="ts">
/** 统一状态块：加载 / 空 / 错误 / 无权限 / 未启用（03 §8、11 §1）。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    state: 'loading' | 'empty' | 'error' | 'forbidden' | 'success' | 'pending'
    title?: string
    description?: string
    hint?: string
    retryable?: boolean
  }>(),
  { retryable: false },
)

const emit = defineEmits<{ retry: [] }>()

const defaults = computed(() => ({
  loading: { title: '正在加载', description: '正在获取数据，请稍候。' },
  empty: { title: '暂无数据', description: '当前条件下没有记录。' },
  error: { title: '加载失败', description: '请检查网络后重试。' },
  forbidden: { title: '无访问权限', description: '当前账号没有查看该内容的权限，可联系管理员申请授权。' },
  success: { title: '操作成功', description: '' },
  pending: { title: '模块尚未启用', description: '该模块将在对应交付阶段启用，当前不可写入。' },
}))
</script>

<template>
  <div class="state-block" :class="`is-${state}`" role="status">
    <div v-if="state === 'loading'" class="skeleton" aria-hidden="true">
      <span /><span /><span />
    </div>
    <div v-else class="icon" aria-hidden="true">
      <template v-if="state === 'empty'">○</template>
      <template v-else-if="state === 'error'">!</template>
      <template v-else-if="state === 'forbidden'">×</template>
      <template v-else-if="state === 'pending'">◔</template>
      <template v-else>✓</template>
    </div>
    <h4 class="title">{{ title || defaults[state].title }}</h4>
    <p v-if="description || defaults[state].description" class="desc">
      {{ description || defaults[state].description }}
    </p>
    <p v-if="hint" class="hint">{{ hint }}</p>
    <div class="actions">
      <slot name="actions" />
      <button v-if="retryable || state === 'error'" class="retry" type="button" @click="emit('retry')">
        重新加载
      </button>
    </div>
  </div>
</template>

<style scoped>
.state-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-7) var(--space-5);
  text-align: center;
  color: var(--ink-muted);
}
.icon {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 20px;
  border: 1px solid var(--border);
  color: var(--ink);
}
.is-error .icon { color: var(--danger); border-color: var(--danger); }
.is-forbidden .icon { color: var(--warning); border-color: var(--warning); }
.is-success .icon { color: var(--success); border-color: var(--success); }
.title { color: var(--ink-strong); font-size: 15px; }
.desc, .hint { font-size: 13px; max-width: 520px; }
.hint { color: var(--ink-muted); }
.actions { display: flex; gap: var(--space-2); margin-top: var(--space-2); }
.retry {
  height: 32px;
  padding: 0 14px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-2);
  color: var(--ink-strong);
  cursor: pointer;
}
.retry:hover { border-color: var(--primary); color: var(--primary); }

.skeleton { width: min(420px, 90%); display: flex; flex-direction: column; gap: 10px; }
.skeleton span {
  height: 12px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--glass-1), var(--glass-3), var(--glass-1));
  background-size: 200% 100%;
  animation: shimmer 1.4s var(--ease-in-out) infinite;
}
.skeleton span:nth-child(2) { width: 78%; }
.skeleton span:nth-child(3) { width: 56%; }
@keyframes shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .skeleton span { animation: none; }
}
</style>
