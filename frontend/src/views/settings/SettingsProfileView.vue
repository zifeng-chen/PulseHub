<script setup lang="ts">
/**
 * 个人资料 /settings/profile（用户反馈：不能改头像、不能改密码）。
 *
 * 头像不做上传：附件存在受控目录里需要鉴权才能读，直接塞进 <img> 会 401，
 * 而 avatar 字段只有 255 字符也放不下 base64。这里给一组预设渐变头像
 * （存 `preset:<key>`，前端渲染），既好看又不涉及文件权限。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { KeyRound, Save } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

const auth = useAuthStore()
const toast = useToastStore()
const router = useRouter()

const AVATARS = [
  { key: 'aurora', label: '极光', css: 'linear-gradient(140deg,#6f97ff,#7c5cfc)' },
  { key: 'sunset', label: '日落', css: 'linear-gradient(140deg,#ffb26b,#ff6b8a)' },
  { key: 'forest', label: '森林', css: 'linear-gradient(140deg,#4fdcae,#17a86f)' },
  { key: 'ocean', label: '海洋', css: 'linear-gradient(140deg,#4ad3e8,#0f7fd1)' },
  { key: 'berry', label: '莓果', css: 'linear-gradient(140deg,#f78ca8,#c14d9b)' },
  { key: 'slate', label: '石墨', css: 'linear-gradient(140deg,#9aa7bd,#5b6878)' },
]

const form = reactive({
  display_name: '',
  email: '',
  phone: '',
  avatar: '' as string,
})
const errors = ref<Record<string, string>>({})
const saving = ref(false)

const pwd = reactive({ old_password: '', new_password: '', confirm: '' })
const pwdErrors = ref<Record<string, string>>({})
const changing = ref(false)

const initial = computed(() => (form.display_name || auth.user?.username || '?').slice(0, 1).toUpperCase())
const currentCss = computed(
  () => AVATARS.find((a) => form.avatar === `preset:${a.key}`)?.css ?? 'linear-gradient(140deg,#9aa7bd,#5b6878)',
)

onMounted(() => {
  const u = auth.user
  if (!u) return
  form.display_name = u.display_name || ''
  form.email = u.email || ''
  form.phone = u.phone || ''
  // 非预设值（历史数据）不选中任何色块，直接用默认灰
  form.avatar = u.avatar || ''
})

async function saveProfile() {
  saving.value = true
  errors.value = {}
  try {
    await api.patch('/auth/profile', {
      display_name: form.display_name.trim(),
      email: form.email.trim(),
      phone: form.phone.trim(),
      avatar: form.avatar,
    })
    await auth.loadMe()
    toast.success('个人资料已更新')
  } catch (err) {
    errors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function changePassword() {
  pwdErrors.value = {}
  if (pwd.new_password !== pwd.confirm) {
    pwdErrors.value = { confirm: '两次输入的新密码不一致' }
    return
  }
  changing.value = true
  try {
    await api.post('/auth/change-password', {
      old_password: pwd.old_password,
      new_password: pwd.new_password,
    })
    toast.success('密码已修改，所有设备需重新登录')
    // 后端会撤销全部会话，这里直接回登录页
    await auth.logout().catch(() => {})
    router.push('/login')
  } catch (err) {
    pwdErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    changing.value = false
  }
}
</script>

<template>
  <div class="profile">
    <PageHeader title="个人资料" subtitle="头像、昵称与联系方式；登录密码也在这里修改。" />

    <AppCard title="基本信息">
      <div class="avatar-row">
        <div class="avatar-big" :style="{ background: currentCss }">{{ initial }}</div>
        <div class="picks">
          <p class="pick-k">选择头像</p>
          <div class="pick-grid">
            <button
              v-for="a in AVATARS" :key="a.key" type="button" class="pick"
              :class="{ on: form.avatar === `preset:${a.key}` }"
              :style="{ background: a.css }" :aria-label="a.label" :title="a.label"
              @click="form.avatar = `preset:${a.key}`"
            />
          </div>
        </div>
      </div>

      <div class="fields">
        <AppField label="昵称" required :error="errors.display_name">
          <AppInput v-model="form.display_name" placeholder="显示在侧栏与日志里" />
        </AppField>
        <AppField label="邮箱" :error="errors.email" hint="用于找回密码与接收提醒（可选）">
          <AppInput v-model="form.email" type="email" placeholder="name@example.com" />
        </AppField>
        <AppField label="手机号" :error="errors.phone" hint="可选">
          <AppInput v-model="form.phone" placeholder="13800138000" />
        </AppField>
      </div>

      <AppButton variant="primary" :loading="saving" @click="saveProfile">
        <Save :size="15" /> 保存资料
      </AppButton>
    </AppCard>

    <AppCard title="修改密码">
      <p class="tip">密码至少 8 位。修改成功后所有设备都会退出登录，需要用新密码重新进入。</p>
      <div class="fields">
        <AppField label="当前密码" required :error="pwdErrors.old_password">
          <AppInput v-model="pwd.old_password" type="password" autocomplete="current-password" />
        </AppField>
        <AppField label="新密码" required :error="pwdErrors.new_password">
          <AppInput v-model="pwd.new_password" type="password" autocomplete="new-password" />
        </AppField>
        <AppField label="确认新密码" required :error="pwdErrors.confirm">
          <AppInput v-model="pwd.confirm" type="password" autocomplete="new-password" />
        </AppField>
      </div>
      <AppButton variant="secondary" :loading="changing" @click="changePassword">
        <KeyRound :size="15" /> 修改密码
      </AppButton>
    </AppCard>
  </div>
</template>

<style scoped>
.profile { display: flex; flex-direction: column; gap: var(--space-4); }
.avatar-row { display: flex; align-items: center; gap: var(--space-4); flex-wrap: wrap; margin-bottom: var(--space-4); }
.avatar-big {
  width: 64px; height: 64px; border-radius: 20px; flex: none;
  display: grid; place-items: center;
  color: #fff; font-size: 26px; font-weight: 700;
  box-shadow: 0 10px 22px -12px rgba(16, 32, 64, 0.7);
}
.pick-k { margin: 0 0 7px; font-size: 12px; color: var(--ink-muted); }
.pick-grid { display: flex; gap: 9px; flex-wrap: wrap; }
.pick {
  width: 30px; height: 30px; border-radius: 10px; border: 2px solid transparent;
  cursor: pointer; transition: transform 0.15s ease, border-color 0.15s ease;
}
.pick:hover { transform: translateY(-1px); }
.pick.on { border-color: var(--ink-strong); box-shadow: 0 0 0 2px var(--glass-2); }
.fields { display: flex; flex-direction: column; gap: var(--space-3); margin-bottom: var(--space-4); max-width: 460px; }
.tip { margin: 0 0 var(--space-3); font-size: 12px; line-height: 1.6; color: var(--ink-muted); }
</style>
