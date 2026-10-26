<script setup lang="ts">
/** 主题切换（03 §2.1、08 §1.1）：浅色 / 深色 / 跟随系统，切换立即生效并保存个人偏好。 */
import { computed } from 'vue'
import { Moon, Sun, MonitorSmartphone } from 'lucide-vue-next'
import { useThemeStore, type ThemeMode } from '@/stores/theme'
import { THEME_LABELS } from '@/i18n'

const theme = useThemeStore()

const options: { mode: ThemeMode; icon: unknown; label: string }[] = [
  { mode: 'light', icon: Sun, label: THEME_LABELS.light },
  { mode: 'dark', icon: Moon, label: THEME_LABELS.dark },
  { mode: 'system', icon: MonitorSmartphone, label: THEME_LABELS.system },
]

const current = computed(() => THEME_LABELS[theme.mode])

function choose(mode: ThemeMode) {
  theme.setMode(mode, true)
}
</script>

<template>
  <div class="theme-toggle" role="group" aria-label="主题模式">
    <button
      v-for="o in options"
      :key="o.mode"
      class="opt"
      :class="{ on: theme.mode === o.mode }"
      type="button"
      :aria-pressed="theme.mode === o.mode ? 'true' : 'false'"
      :title="o.label"
      @click="choose(o.mode)"
    >
      <component :is="o.icon" :size="15" />
      <span>{{ o.label }}</span>
    </button>
    <span class="sr-only">当前主题：{{ current }}</span>
  </div>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border-radius: var(--glass-radius-control);
  border: 1px solid var(--border);
  background: var(--glass-1);
}
.opt {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--ink-muted);
  font-size: 12.5px;
  cursor: pointer;
}
.opt:hover { color: var(--ink-strong); }
.opt.on {
  color: var(--ink-strong);
  background: var(--glass-3);
  box-shadow: var(--glass-inner-highlight);
}
@media (max-width: 768px) {
  .opt span { display: none; }
  .opt { padding: 0 10px; height: 34px; }
}
</style>
