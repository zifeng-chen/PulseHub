<script setup lang="ts">
/**
 * 404：路由不存在。
 * 注意：不存在匹配路由时导航不得错误选中第一个菜单（21 §5 用例 7）。
 */
import { useRouter } from 'vue-router'
import AppButton from '@/components/ui/AppButton.vue'
import { MENUS } from '@/router/menu'

const router = useRouter()
</script>

<template>
  <div class="page">
    <div class="app-backdrop" aria-hidden="true" />
    <section class="card glass">
      <h1>页面不存在</h1>
      <p class="lead">没有找到与该地址匹配的页面。请确认链接是否正确，或从下方选择一个一级菜单继续。</p>
      <div class="menus">
        <AppButton v-for="m in MENUS" :key="m.key" variant="secondary" @click="router.push(m.home)">
          {{ m.label }}
        </AppButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page { min-height: 100vh; display: grid; place-items: center; padding: var(--space-6); }
.card { width: min(520px, 100%); padding: var(--space-6); border-radius: var(--glass-radius-panel); position: relative; z-index: 2; }
.card h1 { font-size: 20px; }
.lead { margin-top: var(--space-3); font-size: 13.5px; color: var(--ink-muted); line-height: 1.8; }
.menus { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-5); }
</style>
