<script setup lang="ts">
/** 家庭与成员 /settings/household（08 §2）：家庭信息、用户列表、用户详情、角色、会话撤销。 */
import { computed, onMounted, ref } from 'vue'
import { UserPlus, Save } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppDataTable, { type Column } from '@/components/ui/AppDataTable.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Member {
  id: string
  user_id: string
  role: string
  role_label: string
  status: string
  display_name: string
  username: string
  joined_at: string | null
  last_login_at: string | null
}

interface HouseholdInfo {
  household: { id: string; name: string; description: string | null; base_currency: string; timezone: string; is_multi_member: boolean } | null
  members: Member[]
  roles: { code: string; label: string }[]
}

interface SessionRow {
  id: string
  device: string
  ip_summary: string
  login_at: string
  last_seen_at: string | null
  revoked: boolean
  current: boolean
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const data = ref<HouseholdInfo | null>(null)
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const info = ref({ name: '', description: '', timezone: 'Asia/Shanghai', is_multi_member: true })
const memberDrawer = ref(false)
const sessionsDrawer = ref(false)
const sessions = ref<SessionRow[]>([])
const activeMember = ref<Member | null>(null)

const memberForm = ref({ display_name: '', username: '', password: '', role: 'member' })

const columns: Column[] = [
  { key: 'display_name', label: '成员', primary: true },
  { key: 'username', label: '登录账号' },
  { key: 'role_label', label: '角色' },
  { key: 'status_label', label: '状态' },
  { key: 'last_login_at', label: '最近登录' },
  { key: 'actions', label: '操作', align: 'right' },
]

const roleOptions = computed(() => (data.value?.roles || []).map((r) => ({ value: r.code, label: r.label })))
const canConfigure = computed(() => auth.can('configure'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get<HouseholdInfo>('/settings/household')
    data.value = res
    if (res.household) {
      info.value = {
        name: res.household.name,
        description: res.household.description || '',
        timezone: res.household.timezone,
        is_multi_member: res.household.is_multi_member,
      }
    }
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function saveHousehold() {
  saving.value = true
  fieldErrors.value = {}
  try {
    await api.put('/settings/household', {
      name: info.value.name,
      description: info.value.description,
      timezone: info.value.timezone,
      is_multi_member: info.value.is_multi_member,
    })
    toast.success('家庭信息已更新')
    await load()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function createMember() {
  saving.value = true
  fieldErrors.value = {}
  try {
    await api.post('/settings/members', memberForm.value)
    toast.success('成员已创建')
    memberDrawer.value = false
    memberForm.value = { display_name: '', username: '', password: '', role: 'member' }
    await load()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function changeRole(member: Member, role: string) {
  try {
    await api.patch(`/settings/members/${member.id}`, { role })
    toast.success('成员角色已更新')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function disableMember(member: Member) {
  const res = await ui.askConfirm({
    title: '确认停用成员',
    message: `停用后该成员将无法登录，其所有会话会被立即撤销。目标成员：${member.display_name}。`,
    detail: ['停用不是删除，成员记录与历史数据会保留。', '停用属于高影响操作，会记录操作人、时间与原因。'],
    confirmText: '确认停用',
    danger: true,
    requireReason: true,
    reasonLabel: '停用原因（必填）',
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'member.disable',
      object_type: 'member',
      object_id: member.id,
    })
    await api.patch(`/settings/members/${member.id}`, {
      status: 'disabled',
      confirm_token: tokenRes.token,
      reason: res.reason,
    })
    toast.success('成员已停用，会话已撤销')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function openSessions(member: Member) {
  activeMember.value = member
  sessionsDrawer.value = true
  try {
    const res = await api.get<{ items: SessionRow[] }>('/auth/sessions')
    sessions.value = res.items
  } catch (err) {
    toast.error(errorMessage(err))
    sessions.value = []
  }
}

async function revokeSession(row: SessionRow) {
  const res = await ui.askConfirm({
    title: '确认撤销会话',
    message: `撤销后该设备需要重新登录。设备：${row.device}，登录时间：${row.login_at}。`,
    confirmText: '确认撤销',
  })
  if (!res.ok) return
  try {
    await api.delete(`/auth/sessions/${row.id}`)
    toast.success('会话已撤销')
    if (activeMember.value) await openSessions(activeMember.value)
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

function fmt(value: string | null) {
  if (!value) return '—'
  const d = new Date(value)
  return `${d.toLocaleDateString('zh-CN')} ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
}

onMounted(load)
</script>

<template>
  <div class="household">
    <PageHeader title="家庭与成员" subtitle="角色只拥有动作集合，成员范围再限制对象范围。">
      <AppButton v-if="canConfigure" variant="primary" @click="memberDrawer = true">
        <UserPlus :size="15" /> 新增成员
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="家庭信息">
        <div class="grid">
          <AppField label="家庭名称" :error="fieldErrors.name">
            <AppInput v-model="info.name" :disabled="!canConfigure" />
          </AppField>
          <AppField label="时区" hint="业务时间按家庭时区墙钟记录，只能选择与服务器同一 UTC 偏移的时区">
            <AppSelect v-model="info.timezone" :disabled="!canConfigure" :options="[
              { value: 'Asia/Shanghai', label: 'Asia/Shanghai（北京时间 UTC+8）' },
              { value: 'Asia/Hong_Kong', label: 'Asia/Hong_Kong（UTC+8）' },
            ]" />
          </AppField>
          <AppField label="基础币种" hint="修改基础币种属于高影响操作，需在财务口径页通过二次确认流程">
            <AppInput :model-value="data.household?.base_currency || 'CNY'" disabled />
          </AppField>
          <AppField label="启用多成员">
            <AppSelect :model-value="String(info.is_multi_member)" :disabled="!canConfigure" :options="[
              { value: 'true', label: '启用' }, { value: 'false', label: '不启用' },
            ]" @update:model-value="(v) => (info.is_multi_member = v === 'true')" />
          </AppField>
          <AppField label="家庭描述">
            <AppInput v-model="info.description" :disabled="!canConfigure" />
          </AppField>
        </div>
        <div v-if="canConfigure" class="form-foot">
          <AppButton variant="primary" :loading="saving" @click="saveHousehold"><Save :size="15" /> 保存</AppButton>
        </div>
      </AppCard>

      <AppCard title="成员列表">
        <AppDataTable :columns="columns" :rows="data.members as never" mobile-title-key="display_name">
          <template #status_label="{ row }">
            <StatusBadge
              :label="(row as unknown as Member).status === 'active' ? '正常' : '已停用'"
              :tone="(row as unknown as Member).status === 'active' ? 'success' : 'neutral'"
            />
          </template>
          <template #last_login_at="{ row }">{{ fmt((row as unknown as Member).last_login_at) }}</template>
          <template #actions="{ row }">
            <div class="row-actions">
              <select
                v-if="canConfigure"
                class="mini-select"
                :value="(row as unknown as Member).role"
                @change="changeRole(row as unknown as Member, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="r in roleOptions" :key="r.value" :value="r.value">{{ r.label }}</option>
              </select>
              <button class="act" type="button" @click="openSessions(row as unknown as Member)">会话</button>
              <button
                v-if="canConfigure && (row as unknown as Member).status === 'active'"
                class="act danger"
                type="button"
                @click="disableMember(row as unknown as Member)"
              >
                停用
              </button>
            </div>
          </template>
        </AppDataTable>
      </AppCard>

      <AppCard title="角色与动作集合" level="1">
        <ul class="roles">
          <li v-for="r in data.roles" :key="r.code">
            <strong>{{ r.label }}</strong>
            <span>{{ r.code }}</span>
          </li>
        </ul>
        <p class="note">
          权限拆成十类动作：查看、创建草稿、编辑草稿、确认、冲销、更正、归档、删除、配置、审计查看。
          后端每次请求同时校验用户、家庭、对象、动作与成员/资产范围。
        </p>
      </AppCard>

      <AppCard title="用户管理四区">
        <p class="note">
          用户列表（搜索、角色筛选、状态筛选、最近登录、逐项停用）、用户详情（基本资料、角色、家庭范围、
          可查看成员范围、可操作业务范围、登录设备与审计记录）、用户编辑与会话管理（设备、IP 摘要、登录时间、
          撤销单个或全部会话）。AI 在本页只解释权限与生成操作提示，不自动提权、停用或重置密码。
        </p>
      </AppCard>
    </template>

    <AppDrawer :open="memberDrawer" title="新增家庭成员" @close="memberDrawer = false">
      <div class="form">
        <AppField label="成员名称" required :error="fieldErrors.display_name">
          <AppInput v-model="memberForm.display_name" />
        </AppField>
        <AppField label="登录账号" required :error="fieldErrors.username">
          <AppInput v-model="memberForm.username" />
        </AppField>
        <AppField label="初始密码" required :error="fieldErrors.password">
          <AppInput v-model="memberForm.password" sensitive />
        </AppField>
        <AppField label="角色" :error="fieldErrors.role">
          <AppSelect v-model="memberForm.role" :options="roleOptions" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="memberDrawer = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="createMember">创建成员</AppButton>
      </template>
    </AppDrawer>

    <AppDrawer :open="sessionsDrawer" title="会话管理" @close="sessionsDrawer = false">
      <p class="note">设备、IP 摘要、登录时间与撤销入口。撤销后该设备需重新登录。</p>
      <ul class="sessions">
        <li v-for="s in sessions" :key="s.id">
          <div class="s-main">
            <strong>{{ s.device }}</strong>
            <small>{{ s.ip_summary }} · 登录于 {{ fmt(s.login_at) }}</small>
          </div>
          <div class="s-actions">
            <StatusBadge :label="s.current ? '当前会话' : s.revoked ? '已撤销' : '有效'" :tone="s.revoked ? 'neutral' : s.current ? 'info' : 'success'" />
            <button v-if="!s.revoked" class="act danger" type="button" @click="revokeSession(s)">撤销</button>
          </div>
        </li>
      </ul>
    </AppDrawer>
  </div>
</template>

<style scoped>
.household { display: flex; flex-direction: column; gap: var(--space-4); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: var(--space-4); }
.form-foot { display: flex; justify-content: flex-end; margin-top: var(--space-4); }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); align-items: center; }
.mini-select {
  height: 28px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; padding: 0 6px;
}
.act {
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.roles { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.roles li { display: flex; flex-direction: column; gap: 2px; padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); min-width: 150px; }
.roles strong { font-size: 13.5px; color: var(--ink-strong); }
.roles span { font-size: 12px; color: var(--ink-muted); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; }
.sessions { margin-top: var(--space-3); display: flex; flex-direction: column; gap: var(--space-3); }
.sessions li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); }
.s-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.s-main strong { font-size: 13.5px; color: var(--ink-strong); }
.s-main small { font-size: 12px; color: var(--ink-muted); }
.s-actions { display: flex; align-items: center; gap: var(--space-2); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
</style>
