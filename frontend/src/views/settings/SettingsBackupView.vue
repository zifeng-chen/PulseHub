<script setup lang="ts">
/**
 * 数据备份与恢复 /settings/backup（08 §11、22 §1）。
 * 备份不是导出功能；每个操作都需要二次确认；恢复需要输入确认短语。
 */
import { onMounted, ref } from 'vue'
import { DatabaseBackup, ShieldCheck, RotateCcw } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDataTable, { type Column } from '@/components/ui/AppDataTable.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Backup {
  id: string
  backup_no: string
  created_at: string
  data_cutoff_at: string | null
  db_version: string
  app_version: string
  migration_version: string
  file_count: number
  size_label: string
  checksum: string
  encrypted: boolean
  status: string
  status_label: string
  error: string | null
}

interface RestorePreview {
  backup_no: string
  app_version: string
  db_version: string
  data_cutoff_at: string | null
  file_count: number
  scope: string
  confirm_phrase_required: string
  reversible: boolean
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const rows = ref<Backup[]>([])
const creating = ref(false)
const previewOpen = ref(false)
const preview = ref<RestorePreview | null>(null)
const restoreTarget = ref<Backup | null>(null)
const phrase = ref('')
const restoring = ref(false)

const columns: Column[] = [
  { key: 'backup_no', label: '备份编号' },
  { key: 'created_at', label: '创建时间' },
  { key: 'size_label', label: '大小', align: 'right' },
  { key: 'file_count', label: '文件数', align: 'right' },
  { key: 'status_label', label: '状态' },
  { key: 'actions', label: '操作', align: 'right' },
]

const canConfigure = () => auth.can('configure')

function fmt(v: string | null) {
  if (!v) return '—'
  return new Date(v).toLocaleString('zh-CN')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get<{ items: Backup[] }>('/settings/backup')
    rows.value = res.items
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function createBackup() {
  const res = await ui.askConfirm({
    title: '确认创建数据备份',
    message: '将生成一致性快照：MySQL 业务数据、配置、用户权限、加密密文、审计记录、附件索引与附件文件。',
    detail: ['备份用于本地灾备与版本升级恢复，不生成供用户传播的报表文件。', '备份文件加密保存并带校验值。'],
    confirmText: '确认创建',
  })
  if (!res.ok) return
  creating.value = true
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'backup.create', object_type: 'backup', object_id: null,
    })
    await api.post('/settings/backup/create', { confirm_token: tokenRes.token })
    toast.success('备份已创建')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    creating.value = false
  }
}

async function verify(row: Backup) {
  try {
    const res = await api.post<{ integrity: boolean; file_count: number }>(`/settings/backup/${row.id}/verify`)
    if (res.integrity) toast.success(`完整性验证通过，共 ${res.file_count} 个文件`)
    else toast.error('完整性验证失败：校验值不一致或目录缺失')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function openRestore(row: Backup) {
  restoreTarget.value = row
  previewOpen.value = true
  phrase.value = ''
  try {
    preview.value = await api.get<RestorePreview>(`/settings/backup/${row.id}/restore-preview`)
  } catch (err) {
    toast.error(errorMessage(err))
    preview.value = null
  }
}

async function doRestore() {
  if (!restoreTarget.value || !preview.value) return
  if (phrase.value !== preview.value.confirm_phrase_required) {
    toast.error(`请输入确认短语：${preview.value.confirm_phrase_required}`)
    return
  }
  restoring.value = true
  try {
    await api.post(`/settings/backup/${restoreTarget.value.id}/restore`, { confirm_phrase: phrase.value })
    toast.success('恢复请求已提交；实际回滚由运维在维护窗口执行离线脚本')
    previewOpen.value = false
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    restoring.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="backup">
    <PageHeader title="数据备份与恢复" subtitle="备份不是导出功能，用于本地灾备与版本升级恢复。">
      <AppButton v-if="canConfigure()" variant="primary" :loading="creating" @click="createBackup">
        <DatabaseBackup :size="15" /> 创建备份
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else>
      <AppCard title="备份列表">
        <StateBlock
          v-if="!rows.length"
          state="empty"
          title="还没有备份"
          description="建议在数据库迁移、恢复、密钥轮换与关键升级前手动创建备份。"
        />
        <AppDataTable v-else :columns="columns" :rows="rows as never" mobile-title-key="backup_no">
          <template #created_at="{ row }">{{ fmt((row as unknown as Backup).created_at) }}</template>
          <template #status_label="{ row }">
            <StatusBadge
              :label="(row as unknown as Backup).status_label"
              :tone="(row as unknown as Backup).status === 'verified' ? 'success' : (row as unknown as Backup).status === 'failed' ? 'danger' : 'info'"
            />
          </template>
          <template #actions="{ row }">
            <div class="row-actions">
              <button class="act" type="button" @click="verify(row as unknown as Backup)">
                <ShieldCheck :size="13" /> 验证
              </button>
              <button class="act" type="button" @click="openRestore(row as unknown as Backup)">
                <RotateCcw :size="13" /> 恢复预览
              </button>
            </div>
          </template>
        </AppDataTable>
      </AppCard>

      <AppCard title="备份内容与恢复流程" level="1">
        <ul class="rules">
          <li>备份内容：业务表、配置表、用户权限、审计、AI 会话与建议、附件索引与内容、数据库迁移版本、应用版本、加密密钥版本与备份元数据。</li>
          <li>恢复流程：选择恢复点 → 查看影响范围 → 输入确认短语 → 二次确认 → 生成待执行记录 → 维护窗口执行 `deploy/restore.py` 回滚 → 校验迁移版本与关键表 → 写入恢复审计 → 重新登录。</li>
          <li>网页端**不会自动执行回滚**：提交只生成「已提交（待运维执行）」记录；执行前可用 dry-run 预览每张表的行数变化，恢复前会自动做安全快照，失败整体回滚。</li>
          <li>恢复前自动保留当前快照；恢复失败必须回滚，不允许半恢复运行。</li>
          <li>不建设后台备份任务、Worker 或任务队列；备份由管理员手动触发或部署系统调用明确接口触发。</li>
        </ul>
      </AppCard>
    </template>

    <AppDrawer :open="previewOpen" title="恢复预览" @close="previewOpen = false">
      <template v-if="preview">
        <ul class="preview">
          <li><span class="k">备份编号</span><span class="v">{{ preview.backup_no }}</span></li>
          <li><span class="k">应用版本</span><span class="v">{{ preview.app_version }}</span></li>
          <li><span class="k">数据库版本</span><span class="v">{{ preview.db_version }}</span></li>
          <li><span class="k">数据截止时间</span><span class="v">{{ fmt(preview.data_cutoff_at) }}</span></li>
          <li><span class="k">附件数量</span><span class="v">{{ preview.file_count }}</span></li>
          <li><span class="k">是否可回滚</span><span class="v">{{ preview.reversible ? '可回滚' : '不可回滚' }}</span></li>
        </ul>
        <p class="scope">{{ preview.scope }}</p>
        <AppField label="确认短语" required :hint="`请输入「${preview.confirm_phrase_required}」以继续`">
          <AppInput v-model="phrase" :placeholder="preview.confirm_phrase_required" />
        </AppField>
        <div class="warn">
          恢复会覆盖当前业务数据，系统将进入维护锁定，所有用户需要重新登录。
        </div>
      </template>
      <template #footer>
        <AppButton variant="secondary" @click="previewOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="restoring" @click="doRestore">提交恢复请求</AppButton>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.backup { display: flex; flex-direction: column; gap: var(--space-4); }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act {
  display: inline-flex; align-items: center; gap: 4px;
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.rules { display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; }
.preview { display: flex; flex-direction: column; gap: 2px; border: 1px solid var(--border); border-radius: var(--glass-radius-card); overflow: hidden; margin-bottom: var(--space-4); }
.preview li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; font-size: 13px; }
.preview li:nth-child(odd) { background: var(--glass-1); }
.preview .k { color: var(--ink-muted); }
.preview .v { color: var(--ink-strong); text-align: right; }
.scope { font-size: 12.5px; color: var(--ink); margin-bottom: var(--space-4); }
.warn {
  margin-top: var(--space-4); padding: 10px 12px; border-radius: var(--radius-sm);
  background: rgba(232, 93, 117, 0.1); border: 1px solid rgba(232, 93, 117, 0.32);
  color: var(--danger); font-size: 12.5px;
}
</style>
