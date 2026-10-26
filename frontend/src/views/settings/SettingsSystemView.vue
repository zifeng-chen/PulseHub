<script setup lang="ts">
/**
 * 系统状态与审计 /settings/system（08 §10）。
 * 显示版本、数据库、迁移版本、文件存储、各提供商状态；审计支持字段级前后差异，不显示密钥与敏感字段。
 */
import { onMounted, ref } from 'vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDataTable, { type Column } from '@/components/ui/AppDataTable.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface SystemStatus {
  app_version: string
  env: string
  database: { label: string; connected: boolean }
  migration_version: string
  file_storage: string
  key_version: string
  maintenance: { active: boolean; reason: string | null; started_at: string | null }
  providers: Record<string, { status: string; status_label: string; detail?: string; enabled_count?: number }>
  counts: { users: number; audit_records: number }
  household: { name: string; base_currency: string; timezone: string } | null
  recent_audit: AuditRow[]
  audit_total: number
}

interface AuditRow {
  id: string
  created_at: string
  actor_name: string | null
  role_label: string
  action: string
  object_type: string | null
  object_id: string | null
  source_method_label: string
  source_page: string | null
  result: string
  request_id: string
  ip_summary: string | null
}

interface AuditDetail extends AuditRow {
  related_object_id: string | null
  reason: string | null
  error_code: string | null
  duration_ms: number | null
  external_provider: string | null
  desensitized: boolean
  before_summary: string | null
  after_summary: string | null
  changes: { field_name: string; field_label: string; before_value: string; after_value: string }[]
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const data = ref<SystemStatus | null>(null)
const detailOpen = ref(false)
const detail = ref<AuditDetail | null>(null)

const columns: Column[] = [
  { key: 'created_at', label: '时间' },
  { key: 'actor_name', label: '操作人' },
  { key: 'action', label: '动作', primary: true },
  { key: 'object_type', label: '对象类型' },
  { key: 'result', label: '结果' },
  { key: 'actions', label: '操作', align: 'right' },
]

const canConfigure = () => auth.can('configure')
const canAudit = () => auth.can('audit_view')

/** 供应商状态配色：已连接=绿；失败/不可达=红；已配置未测试=橙（提示去验证）；未配置/停用=灰 */
function providerTone(status: string): 'success' | 'danger' | 'warning' | 'neutral' {
  if (status === 'connected') return 'success'
  if (status === 'failed' || status === 'unreachable') return 'danger'
  if (status === 'untested' || status === 'testing') return 'warning'
  return 'neutral'
}

function fmt(v: string | null) {
  if (!v) return '—'
  return new Date(v).toLocaleString('zh-CN')
}

function actionLabel(action: string) {
  const map: Record<string, string> = {
    'auth.login': '用户登录', 'auth.logout': '退出登录', 'auth.change_password': '修改密码',
    'setup.completed': '完成系统初始化', 'setup.admin_created': '创建管理员',
    'setup.database_saved': '保存数据库配置', 'setup.household_created': '创建家庭基础信息',
    'event.create_draft': '创建业务事件草稿', 'event.confirm': '确认业务事件', 'event.reverse': '冲销业务事件',
    'category.create': '新增分类', 'category.update': '编辑分类', 'category.delete': '删除分类',
    'member.create': '新增家庭成员', 'member.update': '更新成员',
    'backup.create': '创建数据备份', 'backup.verify': '验证备份完整性', 'backup.restore_preview': '提交恢复请求',
    'privacy.update': '更新隐私与安全', 'finance.update': '更新财务口径',
    'finance.change_base_currency': '修改基础币种', 'system.maintenance_enter': '进入维护模式',
    'system.maintenance_exit': '退出维护模式', 'preference.update': '更新个人偏好',
  }
  return map[action] || action
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<SystemStatus>('/settings/system')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function openDetail(row: AuditRow) {
  detailOpen.value = true
  try {
    detail.value = await api.get<AuditDetail>(`/audit/${row.id}`)
  } catch (err) {
    toast.error(errorMessage(err))
    detail.value = null
  }
}

async function toggleMaintenance() {
  if (!data.value) return
  if (data.value.maintenance.active) {
    const res = await ui.askConfirm({
      title: '确认退出维护模式',
      message: '退出前系统会执行健康检查、迁移版本检查、关键统计检查与附件检查，全部通过后恢复写入。',
      confirmText: '确认退出',
    })
    if (!res.ok) return
    try {
      await api.post('/system/maintenance/exit')
      toast.success('已退出维护模式')
      await load()
    } catch (err) {
      toast.error(errorMessage(err))
    }
    return
  }
  const res = await ui.askConfirm({
    title: '确认进入维护模式',
    message: '进入后普通用户不能创建、确认、修改或删除业务数据，只能查看必要状态。',
    detail: ['用于数据库迁移、恢复、密钥轮换与关键升级。', '维护结束后需通过全部检查才能恢复写入。'],
    confirmText: '确认进入',
    danger: true,
    requireReason: true,
    reasonLabel: '维护原因（必填）',
  })
  if (!res.ok) return
  try {
    await api.post('/system/maintenance/enter', { reason: res.reason })
    toast.success('已进入维护模式')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

onMounted(load)
</script>

<template>
  <div class="system">
    <PageHeader title="系统状态与审计" subtitle="审计记录只允许追加；不显示密钥与完整敏感字段。">
      <AppButton v-if="canConfigure()" :variant="data?.maintenance.active ? 'primary' : 'secondary'" @click="toggleMaintenance">
        {{ data?.maintenance.active ? '退出维护模式' : '进入维护模式' }}
      </AppButton>
      <AppButton variant="secondary" @click="load">刷新</AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard v-if="data.maintenance.active" level="3">
        <div class="maint">
          <StatusBadge label="维护模式进行中" tone="danger" />
          <p>原因：{{ data.maintenance.reason || '系统维护' }} · 开始时间：{{ fmt(data.maintenance.started_at) }}</p>
          <p class="note">维护期间普通用户不能写入业务数据，只能查看必要状态。</p>
        </div>
      </AppCard>

      <section class="status-grid">
        <AppCard title="版本与环境" level="1">
          <ul class="kv">
            <li><span class="k">应用版本</span><span class="v">{{ data.app_version }}</span></li>
            <li><span class="k">运行环境</span><span class="v">{{ data.env }}</span></li>
            <li><span class="k">迁移版本</span><span class="v">{{ data.migration_version }}</span></li>
            <li><span class="k">密钥版本</span><span class="v">{{ data.key_version }}</span></li>
          </ul>
        </AppCard>
        <AppCard title="存储与数据库" level="1">
          <ul class="kv">
            <li><span class="k">数据库</span><span class="v">{{ data.database.label }}</span></li>
            <li><span class="k">连接状态</span><span class="v">{{ data.database.connected ? '正常' : '异常' }}</span></li>
            <li><span class="k">附件目录</span><span class="v small">{{ data.file_storage }}</span></li>
            <li><span class="k">用户数 / 审计记录</span><span class="v">{{ data.counts.users }} / {{ data.counts.audit_records }}</span></li>
          </ul>
        </AppCard>
      </section>

      <AppCard title="能力提供商状态" level="1">
        <ul class="providers">
          <li v-for="(p, key) in data.providers" :key="key">
            <span class="pl">
              {{ { local_ai: '本地 AI', ocr: '百度 OCR', notification: '提醒通知', third_party_ai: '第三方 AI 接口' }[key as 'local_ai'] || key }}
              <small v-if="p.detail" class="pd">{{ p.detail }}</small>
            </span>
            <StatusBadge :label="p.status_label" :tone="providerTone(p.status)" />
          </li>
        </ul>
      </AppCard>

      <AppCard v-if="canAudit()" title="审计记录（最近）">
        <StateBlock v-if="!data.recent_audit.length" state="empty" title="暂无审计记录" description="系统会在高影响操作后写入审计。" />
        <AppDataTable v-else :columns="columns" :rows="data.recent_audit as never" mobile-title-key="action">
          <template #created_at="{ row }">{{ fmt((row as unknown as AuditRow).created_at) }}</template>
          <template #actor_name="{ row }">
            {{ (row as unknown as AuditRow).actor_name || '—' }}
            <small class="role">（{{ (row as unknown as AuditRow).role_label }}）</small>
          </template>
          <template #action="{ row }">{{ actionLabel((row as unknown as AuditRow).action) }}</template>
          <template #result="{ row }">
            <StatusBadge
              :label="(row as unknown as AuditRow).result === 'success' ? '成功' : '失败'"
              :tone="(row as unknown as AuditRow).result === 'success' ? 'success' : 'danger'"
            />
          </template>
          <template #actions="{ row }">
            <button class="act" type="button" @click="openDetail(row as unknown as AuditRow)">详情</button>
          </template>
        </AppDataTable>
        <p class="note">共 {{ data.audit_total }} 条审计记录。列表使用中文动作名称与中文对象名称，不直接显示内部英文枚举。</p>
      </AppCard>

      <AppCard v-else title="审计记录">
        <StateBlock state="forbidden" description="当前角色没有「审计查看」动作权限，请联系家庭所有者或系统管理员授权。" />
      </AppCard>
    </template>

    <AppDrawer :open="detailOpen" title="审计详情" :width="560" @close="detailOpen = false">
      <template v-if="detail">
        <ul class="kv detail">
          <li><span class="k">操作时间</span><span class="v">{{ fmt(detail.created_at) }}</span></li>
          <li><span class="k">操作人 / 角色</span><span class="v">{{ detail.actor_name || '—' }} / {{ detail.role_label }}</span></li>
          <li><span class="k">请求编号</span><span class="v small">{{ detail.request_id }}</span></li>
          <li><span class="k">IP 摘要</span><span class="v">{{ detail.ip_summary || '—' }}</span></li>
          <li><span class="k">来源页面</span><span class="v small">{{ detail.source_page || '—' }}</span></li>
          <li><span class="k">来源方式</span><span class="v">{{ detail.source_method_label }}</span></li>
          <li><span class="k">动作</span><span class="v">{{ actionLabel(detail.action) }}</span></li>
          <li><span class="k">对象类型 / ID</span><span class="v small">{{ detail.object_type || '—' }} / {{ detail.object_id || '—' }}</span></li>
          <li><span class="k">结果 / 错误码</span><span class="v">{{ detail.result === 'success' ? '成功' : '失败' }} / {{ detail.error_code || '—' }}</span></li>
          <li><span class="k">耗时</span><span class="v">{{ detail.duration_ms ?? '—' }} ms</span></li>
          <li><span class="k">外部提供商</span><span class="v">{{ detail.external_provider || '—' }}</span></li>
          <li><span class="k">脱敏状态</span><span class="v">{{ detail.desensitized ? '已脱敏' : '未脱敏' }}</span></li>
          <li><span class="k">原因</span><span class="v">{{ detail.reason || '—' }}</span></li>
        </ul>

        <h4 class="sec-title">字段级差异</h4>
        <StateBlock v-if="!detail.changes.length" state="empty" title="本次操作没有字段级变更" />
        <table v-else class="changes">
          <thead>
            <tr><th>字段</th><th>变更前</th><th>变更后</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in detail.changes" :key="c.field_name">
              <td>{{ c.field_label }}</td>
              <td class="mono">{{ c.before_value || '—' }}</td>
              <td class="mono">{{ c.after_value || '—' }}</td>
            </tr>
          </tbody>
        </table>

        <h4 class="sec-title">变更前后摘要</h4>
        <pre class="summary">{{ detail.before_summary || '—' }}</pre>
        <pre class="summary">{{ detail.after_summary || '—' }}</pre>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.system { display: flex; flex-direction: column; gap: var(--space-4); }
.maint p { font-size: 13px; color: var(--ink); margin-top: 6px; }
.status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: var(--space-4); }
.kv { display: flex; flex-direction: column; gap: 2px; }
.kv li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 8px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.kv li:last-child { border-bottom: none; }
.kv .k { color: var(--ink-muted); }
.kv .v { color: var(--ink-strong); text-align: right; word-break: break-all; }
.kv .v.small { font-size: 11.5px; }
.providers { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-3); }
.providers li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); }
.pl { display: flex; flex-direction: column; gap: 3px; min-width: 0; font-size: 13.5px; color: var(--ink-strong); }
.pl .pd { font-size: 11.5px; font-weight: 400; color: var(--ink-muted); line-height: 1.45; }
.role { color: var(--ink-muted); font-size: 11.5px; }
.act { height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer; }
.act:hover { border-color: var(--primary); color: var(--primary); }
.note { font-size: 12px; color: var(--ink-muted); margin-top: var(--space-3); }
.sec-title { margin-top: var(--space-5); margin-bottom: var(--space-2); font-size: 13.5px; color: var(--ink-strong); }
.changes { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.changes th, .changes td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.changes th { color: var(--ink-muted); font-weight: 500; }
.mono { font-family: ui-monospace, Consolas, monospace; color: var(--ink); word-break: break-all; }
.summary {
  background: var(--glass-1); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 10px; font-size: 11.5px; color: var(--ink); white-space: pre-wrap; word-break: break-all; margin-bottom: 8px;
}
</style>
