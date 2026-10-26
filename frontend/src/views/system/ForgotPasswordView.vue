<script setup lang="ts">
/** 忘记 / 重置密码（04 §5）：本地部署管理员恢复流程，不伪造外部找回能力。 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorFields, errorMessage } from '@/api/client'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import { useToastStore } from '@/stores/toast'

const router = useRouter()
const toast = useToastStore()

const username = ref('')
const recoveryEmail = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const errors = ref<Record<string, string>>({})
const errorMsg = ref('')
const done = ref(false)

async function submit() {
  errors.value = {}
  errorMsg.value = ''
  if (newPassword.value !== confirmPassword.value) {
    errors.value.confirm_password = '两次输入的密码不一致'
    return
  }
  submitting.value = true
  try {
    await api.post('/auth/reset-password', {
      username: username.value.trim(),
      recovery_email: recoveryEmail.value || null,
      new_password: newPassword.value,
    })
    done.value = true
    toast.success('密码已重置，旧会话已全部失效')
  } catch (err) {
    errorMsg.value = errorMessage(err)
    errors.value = errorFields(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="fp-page">
    <div class="app-backdrop" aria-hidden="true" />
    <section class="card glass">
      <h1>重置密码</h1>
      <p class="lead">
        只有配置了恢复邮箱的管理员账号可以在线重置：校验恢复邮箱 → 设置新密码 → 失效旧会话 → 写入审计。
        未配置恢复邮箱时，任何人都不能在线重置；请在服务器本机执行
        <code>python -m scripts.reset_admin_password --username 管理员账号</code>。
      </p>

      <div v-if="done" class="done">
        <p>密码已重置，所有旧会话已失效。</p>
        <AppButton variant="primary" @click="router.push('/login')">返回登录</AppButton>
      </div>

      <form v-else class="form" @submit.prevent="submit">
        <AppField label="管理员账号" required :error="errors.username" for-id="fp-user">
          <AppInput id="fp-user" v-model="username" />
        </AppField>
        <AppField label="恢复邮箱" :error="errors.recovery_email" for-id="fp-mail">
          <AppInput id="fp-mail" v-model="recoveryEmail" type="email" />
        </AppField>
        <AppField label="新密码" required :error="errors.new_password" for-id="fp-pass">
          <AppInput id="fp-pass" v-model="newPassword" sensitive />
        </AppField>
        <AppField label="确认新密码" required :error="errors.confirm_password" for-id="fp-pass2">
          <AppInput id="fp-pass2" v-model="confirmPassword" sensitive />
        </AppField>
        <p v-if="errorMsg" class="err" role="alert">{{ errorMsg }}</p>
        <AppButton variant="primary" block type="submit" :loading="submitting">重置密码</AppButton>
      </form>

      <footer class="foot"><RouterLink to="/login">返回登录</RouterLink></footer>
    </section>
  </div>
</template>

<style scoped>
.fp-page { min-height: 100vh; display: grid; place-items: center; padding: calc(var(--space-6) + var(--safe-top)) var(--space-4) var(--space-6); }
.card { width: min(520px, 100%); padding: var(--space-6); border-radius: var(--glass-radius-panel); position: relative; z-index: 2; }
.card h1 { font-size: 20px; }
.lead { margin-top: var(--space-3); font-size: 13px; color: var(--ink-muted); line-height: 1.8; }
.form { margin-top: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }
.err { font-size: 13px; color: var(--danger); }
.done { margin-top: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); align-items: flex-start; }
.foot { margin-top: var(--space-5); font-size: 13px; }
</style>
