<script setup lang="ts">
/**
 * 首次登录引导（04 §6）：一次性提示，关闭后不再自动出现。
 * 采用右下角非模态浮层，不遮罩、不阻塞，登录后能直接看到脉枢台。
 */
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()

const steps = [
  { n: '1', t: '录入数据', d: '新增账户、记录收入支出，或上传账单由智能录入识别。' },
  { n: '2', t: '查看台账', d: '在家庭台账中核对资产、负债、投资、租赁与提醒。' },
  { n: '3', t: '让 AI 分析', d: '随时打开全局 AI 助手，基于确定性统计获得解释与分析。' },
]

function go(path: string, message: string) {
  auth.dismissWelcome()
  emit('close')
  toast.info(message)
  router.push(path)
}

function close() {
  auth.dismissWelcome()
  emit('close')
}
</script>

<template>
  <Transition name="welcome-pop">
    <aside v-if="props.open" class="welcome-card" role="dialog" aria-label="新手引导">
      <header class="head">
        <strong>三步开始你的家庭财务台账</strong>
        <button class="x" type="button" aria-label="关闭引导" @click="close">×</button>
      </header>
      <ol class="steps">
        <li v-for="s in steps" :key="s.n">
          <span class="num">{{ s.n }}</span>
          <div>
            <strong>{{ s.t }}</strong>
            <p>{{ s.d }}</p>
          </div>
        </li>
      </ol>
      <div class="entries">
        <button class="entry" type="button" @click="go('/ledger/accounts', '已打开账户页，可新增第一个账户')">
          新增第一个账户
        </button>
        <button class="entry" type="button" @click="go('/ledger/capture', '已打开智能录入，可上传账单')">
          上传第一张账单
        </button>
        <button class="entry" type="button" @click="go('/dashboard', '已在脉枢台，可用右上角 AI 助手提问')">
          打开 AI 助手
        </button>
      </div>
      <div class="foot">
        <button class="skip" type="button" @click="close">知道了，不再提示</button>
      </div>
    </aside>
  </Transition>
</template>

<style scoped>
.welcome-card {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 1200;
  width: min(360px, calc(100vw - 32px));
  max-height: min(72vh, 560px);
  overflow: auto;
  padding: var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--glass-radius-card, 16px);
  background: var(--glass-2, rgba(255, 255, 255, 0.86));
  backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.head strong { font-size: 13.5px; color: var(--ink-strong); }
.x {
  width: 26px; height: 26px; flex: none; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--border); background: var(--glass-1);
  color: var(--ink-muted); font-size: 16px; line-height: 1;
}
.x:hover { color: var(--ink-strong); border-color: var(--primary); }
.steps { display: flex; flex-direction: column; gap: var(--space-3); }
.steps li { display: flex; gap: var(--space-3); }
.num {
  width: 24px; height: 24px; flex: 0 0 24px;
  display: grid; place-items: center; border-radius: 50%;
  background: linear-gradient(135deg, var(--primary), var(--accent-ai));
  color: #fff; font-size: 12px; font-weight: 600;
}
.steps strong { font-size: 13px; color: var(--ink-strong); }
.steps p { font-size: 12px; color: var(--ink-muted); line-height: 1.6; }
.entries { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.entry {
  height: 30px; padding: 0 12px; border-radius: var(--glass-radius-control);
  border: 1px solid var(--border); background: var(--glass-1);
  color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.entry:hover { border-color: var(--primary); color: var(--primary); }
.foot { display: flex; justify-content: flex-end; }
.skip { border: none; background: transparent; color: var(--ink-muted); font-size: 12.5px; cursor: pointer; }
.skip:hover { color: var(--ink-strong); }

/* 移动端避开底部四项导航 */
@media (max-width: 720px) {
  .welcome-card { right: 12px; left: 12px; bottom: 74px; width: auto; max-height: 58vh; }
}

.welcome-pop-enter-active, .welcome-pop-leave-active {
  transition: opacity 0.24s ease, transform 0.24s ease;
}
.welcome-pop-enter-from, .welcome-pop-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
@media (prefers-reduced-motion: reduce) {
  .welcome-pop-enter-active, .welcome-pop-leave-active { transition: none; }
}
</style>
