<script setup lang="ts">
/** 403 无权限：显示原因与联系管理员入口（03 §8、11 §3）。 */
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppButton from '@/components/ui/AppButton.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
</script>

<template>
  <div class="page">
    <div class="app-backdrop" aria-hidden="true" />
    <section class="card glass">
      <h1>无访问权限</h1>
      <p class="lead">
        当前账号（{{ auth.displayName }} · {{ auth.roleLabel }}）没有访问该页面的权限。
      </p>
      <p v-if="route.query.from" class="from">请求页面：{{ route.query.from }}</p>
      <ul class="tips">
        <li>该操作需要更高角色权限，具体权限由角色动作集合与成员范围共同决定。</li>
        <li>如需授权，请联系家庭所有者或系统管理员在「设置 / 家庭与成员」中调整角色。</li>
      </ul>
      <div class="actions">
        <AppButton variant="secondary" @click="router.back()">返回上一页</AppButton>
        <AppButton variant="primary" @click="router.push('/dashboard')">返回脉枢台</AppButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page { min-height: 100vh; display: grid; place-items: center; padding: var(--space-6); }
.card { width: min(520px, 100%); padding: var(--space-6); border-radius: var(--glass-radius-panel); position: relative; z-index: 2; }
.card h1 { font-size: 20px; }
.lead { margin-top: var(--space-3); font-size: 13.5px; color: var(--ink); }
.from { margin-top: 6px; font-size: 12.5px; color: var(--ink-muted); word-break: break-all; }
.tips { margin-top: var(--space-4); display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: var(--ink-muted); }
.tips li { padding-left: 14px; position: relative; }
.tips li::before { content: '·'; position: absolute; left: 4px; color: var(--primary); font-weight: 700; }
.actions { display: flex; gap: var(--space-2); margin-top: var(--space-5); }
</style>
