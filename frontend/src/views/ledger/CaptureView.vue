<script setup lang="ts">
/**
 * 智能录入 /ledger/capture（06 §10、09 §47、24 §4）。
 * 流程：选材料类型 → 上传图片/PDF → OCR 识别 → 原文+可编辑字段 → 保存草稿/确认。
 * OCR 失败可手工录入；低置信度不默认选中；未配置 OCR 显示可配置入口。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { FileUp, RefreshCw, ScanLine, Sparkles } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import { toLocalDay } from '@/utils/calendar'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppDataTable, { type Column } from '@/components/ui/AppDataTable.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { accountLabel } from '@/utils/account'

interface Attachment {
  id: string
  file_name: string
  material_type: string | null
  material_type_label: string | null
  mime_type: string
  size_bytes: number
  status_label: string
  created_at: string | null
  /** 已被业务记录引用：confirmed=已入库，draft=草稿引用中，bound=上传时直接绑定 */
  referenced: boolean
  reference_status: string | null
  reference_label: string | null
  reference_id: string | null
}
interface OcrLog { id: string; attachment_id: string | null; material_type_label: string | null; status: string; status_label: string; raw_text: string | null; structured: { amount: string | null; occurred_at: string | null; confidence: number; low_confidence: boolean } | null; confidence: number | null; error_message: string | null; retry_count: number; created_at: string | null }
interface Account { id: string; name: string; currency: string; status: string }
interface Category { id: string; name: string; domain: string }

const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const tab = ref<'capture' | 'attachments' | 'logs' | 'import' | 'text'>('capture')
const loading = ref(false)
const error = ref('')

const attachments = ref<Attachment[]>([])
const logs = ref<OcrLog[]>([])
const accounts = ref<Account[]>([])
const categories = ref<Category[]>([])

const assetCertInput = ref<HTMLInputElement | null>(null)
const assetCertBusy = ref(false)

const materialType = ref('receipt')
const materialOptions = ['receipt', 'bill', 'bank_statement', 'contract', 'other']
  .map((v) => ({ value: v, label: zh('material_type', v) }))

const canCreate = computed(() => auth.can('create_draft'))
const canConfirm = computed(() => auth.can('confirm'))

/** 附件是否已入库（被已确认的业务记录引用） */
function isArchived(a: Attachment): boolean {
  return Boolean(a.referenced && a.reference_status === 'confirmed')
}
/** 待处理附件：已被确认引用的不再出现在识别选择里，避免重复录入 */
const pendingAttachments = computed(() => attachments.value.filter((a) => !isArchived(a)))
/** 附件页签默认只列待处理，可切到「全部」查看已入库的 */
const showArchived = ref(false)
const archivedCount = computed(() => attachments.value.filter(isArchived).length)
const visibleAttachments = computed(() => (
  showArchived.value ? attachments.value : pendingAttachments.value
))

function referenceBadge(a: Attachment): { label: string; tone: 'success' | 'warning' | 'info' } | null {
  if (!a.referenced) return null
  if (a.reference_status === 'confirmed') return { label: '已入库', tone: 'success' }
  if (a.reference_status === 'draft') return { label: '草稿引用中', tone: 'warning' }
  return { label: '已绑定', tone: 'info' }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [att, logsRes, acc, cat] = await Promise.all([
      api.get<{ items: Attachment[] }>('/ledger/capture/attachments'),
      api.get<{ items: OcrLog[] }>('/ledger/capture/ocr-logs'),
      api.get<{ items: Account[] }>('/ledger/accounts'),
      api.get<{ items: Category[] }>('/ledger/categories'),
    ])
    attachments.value = att.items
    logs.value = logsRes.items
    accounts.value = acc.items.filter((a) => a.status === 'active')
    categories.value = cat.items
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ---- 数据导入（24 §7）----
const importContent = ref('')
const importFileName = ref('流水导入.csv')
const importPreview = ref<Record<string, any> | null>(null)
const importPreviewing = ref(false)
const importBatches = ref<Record<string, any>[]>([])
const importLoading = ref(false)
/** 用户确认「与已有流水是同一笔」要跳过的行号（1 开始），默认：疑似重复行全部跳过 */
const importSkipRows = ref<Set<number>>(new Set())

async function loadImports() {
  importLoading.value = true
  try {
    const res = await api.get<{ items: Record<string, any>[] }>('/ledger/imports')
    importBatches.value = res.items
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    importLoading.value = false
  }
}

async function previewImport() {
  importPreviewing.value = true
  importPreview.value = null
  try {
    const result = await api.post<Record<string, any>>('/ledger/imports/preview', {
      content: importContent.value,
    })
    importPreview.value = result
    // 疑似重复默认「跳过」：宁可少记一笔让用户手动补，也不要自动记两笔
    const skip = new Set<number>()
    ;(result.rows || []).forEach((r: Record<string, any>, idx: number) => {
      if (r.suspected_duplicate) skip.add(idx + 1)
    })
    importSkipRows.value = skip
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    importPreviewing.value = false
  }
}

async function createImport() {
  try {
    const skipped = [...importSkipRows.value]
    await api.post('/ledger/imports', {
      file_name: importFileName.value || '流水导入.csv',
      content: importContent.value,
      skip_rows: skipped,
    })
    toast.success(skipped.length
      ? `导入批次已创建，已跳过 ${skipped.length} 行疑似重复`
      : '导入批次已创建，成功行已生成草稿，确认后才入账')
    importContent.value = ''
    importPreview.value = null
    importSkipRows.value = new Set()
    await loadImports()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function confirmImport(batchId: string) {
  const res = await ui.askConfirm({
    title: '确认导入',
    message: '将批量确认该批次所有草稿事件，确认后才计入账户余额与统计。',
    detail: ['错误行与重复行不会入账。', '确认前可到「流水」页逐条核对草稿。'],
    confirmText: '确认导入',
  })
  if (!res.ok) return
  try {
    await api.post(`/ledger/imports/${batchId}/confirm`)
    toast.success('已批量确认')
    await loadImports()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

// ---- 对话式录入（账单 / 聊天记录 → 草稿建议）----
interface TextDraft {
  raw: string
  event_type: string
  title: string
  amount: string
  occurred_at: string | null
  account_id: string | null
  account_name: string | null
  category_id: string | null
  category_name: string | null
  confidence: string
  errors: string[]
  ok: boolean
}
const textInput = ref('')
const textItems = ref<TextDraft[]>([])
const textParsing = ref(false)
const textCreating = ref(false)
/** 本次解析实际走的是本地 AI 还是规则兜底（由后端返回，不额外增加入口） */
const textParser = ref<'ai' | 'rule' | ''>('')
const textCaliber = ref('')
const textAiError = ref('')

function normDate(v: string | null): string {
  // OCR 可能返回带时区的时间，统一转成本地日期，避免东八区少一天
  return toLocalDay(v)
}

async function parseText() {
  if (!textInput.value.trim()) {
    toast.error('请先输入或粘贴文本')
    return
  }
  textParsing.value = true
  textItems.value = []
  try {
    const res = await api.post<{
      items: TextDraft[]
      parser: 'ai' | 'rule'
      parser_label: string
      caliber: string
      ai_error?: string
    }>('/ledger/parse-text', { text: textInput.value })
    textItems.value = res.items.map((i) => ({ ...i, occurred_at: normDate(i.occurred_at) }))
    textParser.value = res.parser
    textCaliber.value = res.caliber || ''
    textAiError.value = res.ai_error || ''
    toast.success(res.parser === 'ai'
      ? `本地 AI 解析完成，共 ${res.items.length} 条`
      : `解析完成（${res.items.length} 条）· 本地 AI 未参与，已用规则解析`)
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    textParsing.value = false
  }
}

async function createDraftsFromText() {
  const usable = textItems.value.filter((i) => i.ok)
  if (!usable.length) {
    toast.error('没有可生成的有效记录（请先补充金额）')
    return
  }
  const res = await ui.askConfirm({
    title: '生成草稿',
    message: `将为 ${usable.length} 条有效记录生成草稿事件，确认后才计入账户余额与统计。`,
    detail: ['草稿不进入任何统计。', '可到「流水」页逐条核对并确认。'],
    confirmText: '生成草稿',
  })
  if (!res.ok) return
  textCreating.value = true
  let done = 0
  try {
    for (const item of usable) {
      await api.post('/ledger/events', {
        event_type: item.event_type,
        title: item.title,
        amount: item.amount,
        occurred_at: item.occurred_at || null,
        account_id: item.account_id,
        category_id: item.category_id,
        source: 'manual',
      })
      done += 1
    }
    toast.success(`已生成 ${done} 条草稿，请到「流水」页核对确认`)
    textItems.value = []
    textInput.value = ''
    await load()
  } catch (err) {
    // 已成功的条目立即从待生成列表移除，避免用户重试时重复建草稿（M-2）
    if (done > 0) {
      textItems.value = textItems.value.slice(done)
    }
    toast.error(`生成中断（已完成 ${done} 条）：${errorMessage(err)}`)
  } finally {
    textCreating.value = false
  }
}

// ---- 上传 ----
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const selectedFile = ref<File | null>(null)

function pickFile() { fileInput.value?.click() }
function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  selectedFile.value = input.files?.[0] || null
}
function pickAssetCert() { assetCertInput.value?.click() }

/** 房产证 → 资产：识别后把字段带到资产页的新增表单，由用户核对保存 */
async function onAssetCert(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  assetCertBusy.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res = await api.post<{ fields: Record<string, string>; filled: number }>(
      '/ledger/assets/ocr-extract', fd,
    )
    sessionStorage.setItem('fam.asset.prefill', JSON.stringify(res.fields))
    toast.success(`识别完成，提取到 ${res.filled} 个字段，已带入资产表单`)
    router.push('/ledger/assets?ai=1')
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    assetCertBusy.value = false
    input.value = ''
  }
}

async function upload() {
  if (!selectedFile.value) { toast.warning('请先选择文件'); return }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', selectedFile.value)
    fd.append('material_type', materialType.value)
    await api.post('/ledger/capture/attachments', fd)
    toast.success('附件已上传')
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    uploading.value = false
  }
}

// ---- OCR 识别 ----
const ocrTargetId = ref('')
const recognizing = ref(false)
const ocrResult = ref<OcrLog | null>(null)
const ocrError = ref('')

async function recognize(attachmentId?: string) {
  const id = attachmentId || ocrTargetId.value
  if (!id) { toast.warning('请先选择要识别的附件'); return }
  recognizing.value = true
  ocrError.value = ''
  ocrResult.value = null
  try {
    ocrResult.value = await api.post<OcrLog>('/ledger/capture/ocr', {
      attachment_id: id, material_type: materialType.value,
    })
    toast.success('OCR 识别完成，请审核结构化结果')
    await load()
  } catch (err) {
    ocrError.value = errorMessage(err)
    if ((err as { code?: string }).code === 'PROVIDER_NOT_CONFIGURED') {
      // 未配置：显示可操作配置入口
    } else {
      toast.error(errorMessage(err))
    }
  } finally {
    recognizing.value = false
  }
}

async function retryLog(logId: string) {
  try {
    await api.post(`/ledger/capture/ocr/${logId}/retry`, {})
    toast.success('OCR 重试成功')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

// ---- 结构化结果 → 草稿事件 ----
const eventType = ref('expense')
const eventForm = reactive({ title: '', amount: '', occurred_at: '', account_id: '', category_id: '', note: '' })
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

function fillFromOcr() {
  const s = ocrResult.value?.structured
  if (!s) return
  eventForm.amount = s.amount || ''
  eventForm.occurred_at = s.occurred_at || ''
  eventForm.title = 'OCR 票据识别'
  eventForm.note = '由智能录入 OCR 生成，请核对后确认'
}

const accountOptions = computed(() => [
  { value: '', label: '请选择账户' },
  ...accounts.value.map((a) => ({ value: a.id, label: accountLabel(a, ui.amountPrecision) })),
])
const categoryOptions = computed(() => [
  { value: '', label: '未选择分类' },
  ...categories.value.filter((c) => c.domain === eventType.value)
    .map((c) => ({ value: c.id, label: c.name })),
])

async function saveDraft(confirm = false) {
  saving.value = true
  fieldErrors.value = {}
  try {
    const created = await api.post<{ id: string }>('/ledger/events', {
      event_type: eventType.value,
      title: eventForm.title,
      amount: eventForm.amount || '0',
      occurred_at: eventForm.occurred_at || null,
      account_id: eventForm.account_id || null,
      category_id: eventForm.category_id || null,
      note: eventForm.note || null,
      // 记住来源附件：入库后该附件标记为「已入库」，不再出现在待处理列表
      attachment_id: ocrResult.value?.attachment_id || null,
      source: 'ocr',
    })
    if (confirm) {
      await api.post(`/ledger/events/${created.id}/confirm`, {})
      toast.success('已确认并计入统计，该附件标记为已入库')
    } else {
      toast.success('草稿已保存（source=ocr），确认后计入统计')
    }
    ocrResult.value = null
    await load()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function removeAttachment(row: Attachment) {
  const { ok, reason } = await ui.askConfirm({
    title: '删除附件',
    message: `确定删除「${row.file_name}」吗？已关联业务的附件不能无痕删除。`,
    confirmText: '确认删除',
    danger: true,
    requireReason: true,
    reasonLabel: '删除原因',
  })
  if (!ok || !reason) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'attachment.delete', object_type: 'attachment', object_id: row.id,
    })
    await api.delete(`/ledger/capture/attachments/${row.id}`, undefined, {
      reason, confirm_token: tokenRes.token,
    })
    toast.success('附件已删除（历史保留）')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function downloadAttachment(row: Attachment) {
  try {
    const token = localStorage.getItem('fam.access_token') || ''
    const resp = await fetch(`/api/v1/ledger/capture/attachments/${row.id}/download`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!resp.ok) { toast.error('下载失败，请检查权限'); return }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = row.file_name
    a.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    toast.error('下载失败')
  }
}

const attachmentColumns: Column[] = [
  { key: 'file_name', label: '文件名', primary: true },
  { key: 'material_type_label', label: '材料类型' },
  { key: 'reference', label: '入库状态' },
  { key: 'size_bytes', label: '大小', align: 'right' },
  { key: 'actions', label: '操作', align: 'right' },
]
const logColumns: Column[] = [
  { key: 'created_at', label: '时间', primary: true },
  { key: 'material_type_label', label: '材料' },
  { key: 'status_label', label: '状态' },
  { key: 'confidence', label: '置信度', align: 'right' },
  { key: 'actions', label: '操作', align: 'right' },
]

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}
function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  return String(s).slice(0, 19).replace('T', ' ')
}
</script>

<template>
  <div class="capture">
    <PageHeader title="智能录入" subtitle="上传票据、账单、流水截图或合同，OCR 识别后经你审核才形成正式账目；识别失败可手工录入。">
      <AppButton variant="secondary" @click="router.push('/settings/ocr')">OCR 设置</AppButton>
    </PageHeader>

    <div class="seg">
      <button :class="{ on: tab === 'capture' }" type="button" @click="tab = 'capture'">上传识别</button>
      <button :class="{ on: tab === 'attachments' }" type="button" @click="tab = 'attachments'">附件（{{ pendingAttachments.length }}）</button>
      <button :class="{ on: tab === 'logs' }" type="button" @click="tab = 'logs'">调用记录（{{ logs.length }}）</button>
      <button :class="{ on: tab === 'import' }" type="button" @click="tab = 'import'; loadImports()">数据导入</button>
      <button :class="{ on: tab === 'text' }" type="button" @click="tab = 'text'">对话式录入</button>
    </div>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <!-- 上传识别 -->
    <template v-else-if="tab === 'capture'">
      <AppCard title="上传材料">
        <div class="upload-row">
          <AppSelect v-model="materialType" :options="materialOptions" class="mt" />
          <input ref="fileInput" type="file" accept="image/png,image/jpeg,application/pdf" class="hidden-input" @change="onFileChange" />
          <AppButton variant="secondary" @click="pickFile">
            <FileUp :size="15" /> 选择文件
          </AppButton>
          <span v-if="selectedFile" class="fname">{{ selectedFile.name }}（{{ fmtSize(selectedFile.size) }}）</span>
          <AppButton v-if="canCreate" variant="primary" :loading="uploading" @click="upload">上传</AppButton>
        </div>
        <p class="hint">仅支持 PNG / JPG / PDF，单文件不超过 20MB；上传后先做安全校验。</p>
      </AppCard>

      <AppCard title="房产证 → 资产（AI 协助录入）"
               subtitle="上传房产证照片，自动提取坐落地址、建筑面积、证号等字段，核对后生成资产。">
        <div class="upload-row">
          <input ref="assetCertInput" type="file" accept="image/*" class="hidden-input" @change="onAssetCert" />
          <AppButton v-if="canCreate" variant="secondary" :loading="assetCertBusy" @click="pickAssetCert">
            <ScanLine :size="15" /> 选择房产证照片
          </AppButton>
          <span class="fname">识别需要已配置百度 OCR；识别结果只做预填，不会自动入库。</span>
        </div>
      </AppCard>

      <AppCard title="选择附件并识别">
        <StateBlock v-if="!pendingAttachments.length" state="empty" title="没有待识别的附件"
                    :description="archivedCount ? `已有 ${archivedCount} 个附件入库，无需重复识别；如需重新识别，可在「附件」页签切到全部。` : '先在上方上传材料。'" />
        <div v-else class="pick-list">
          <button
            v-for="a in pendingAttachments" :key="a.id"
            class="pick-item" :class="{ on: ocrTargetId === a.id }"
            type="button" @click="ocrTargetId = a.id; recognize(a.id)"
          >
            <span class="pn">{{ a.file_name }}</span>
            <span class="pm">
              {{ a.material_type_label || '其他' }} · {{ fmtSize(a.size_bytes) }}
              <StatusBadge v-if="referenceBadge(a)" :label="referenceBadge(a)!.label" :tone="referenceBadge(a)!.tone" />
            </span>
          </button>
        </div>
        <p v-if="archivedCount" class="archived-note">
          已有 {{ archivedCount }} 个附件入库，不再显示在待识别列表
        </p>
        <AppButton v-if="pendingAttachments.length" variant="primary" :loading="recognizing" @click="recognize()">
          <Sparkles :size="15" /> 识别选中附件
        </AppButton>
      </AppCard>

      <AppCard v-if="recognizing" title="识别中">
        <StateBlock state="loading" title="正在识别" description="OCR 识别中，请稍候…" />
      </AppCard>

      <AppCard v-else-if="ocrError && ocrError.includes('尚未配置')" title="OCR 未配置">
        <StateBlock state="pending" title="百度 OCR 尚未配置" :description="ocrError">
          <template #actions>
            <AppButton variant="primary" @click="router.push('/settings/ocr')">前往配置百度 OCR</AppButton>
          </template>
        </StateBlock>
      </AppCard>

      <AppCard v-else-if="ocrResult" title="OCR 结果与审核">
        <div class="ocr-grid">
          <div class="raw">
            <h4>识别原文</h4>
            <pre>{{ ocrResult.raw_text || '（无文字）' }}</pre>
          </div>
          <div class="form">
            <div class="conf">
              置信度 {{ ocrResult.confidence ?? 0 }}
              <StatusBadge :label="ocrResult.structured?.low_confidence ? '低置信度，请核对' : '需审核'"
                           :tone="ocrResult.structured?.low_confidence ? 'warning' : 'neutral'" />
            </div>
            <AppSelect v-model="eventType" :options="[{ value: 'expense', label: '支出' }, { value: 'income', label: '收入' }]" />
            <AppField label="标题" required :error="fieldErrors.title">
              <AppInput v-model="eventForm.title" placeholder="例如：超市采购" />
            </AppField>
            <AppField label="金额" required :error="fieldErrors.amount">
              <AppInput v-model="eventForm.amount" type="number" placeholder="0.00" />
            </AppField>
            <AppField label="日期">
              <AppDatePicker v-model="eventForm.occurred_at" value-format="datetime" placeholder="选择日期" />
            </AppField>
            <AppField label="账户"><AppSelect v-model="eventForm.account_id" :options="accountOptions" /></AppField>
            <AppField label="分类"><AppSelect v-model="eventForm.category_id" :options="categoryOptions" /></AppField>
            <AppField label="备注"><AppInput v-model="eventForm.note" /></AppField>
            <div class="acts">
              <AppButton variant="secondary" @click="fillFromOcr">填入 OCR 建议值</AppButton>
              <AppButton v-if="canCreate" variant="secondary" :loading="saving" @click="saveDraft(false)">保存草稿</AppButton>
              <AppButton v-if="canConfirm" variant="primary" :loading="saving" @click="saveDraft(true)">确认入账</AppButton>
            </div>
            <p class="hint">OCR 结构化结果仅供审核参考，确认后才形成正式业务事实。</p>
          </div>
        </div>
      </AppCard>
    </template>

    <!-- 附件 -->
    <template v-else-if="tab === 'attachments'">
      <AppCard title="附件">
        <template #extra>
          <label class="archived-toggle" v-if="archivedCount">
            <input v-model="showArchived" type="checkbox" />
            显示已入库（{{ archivedCount }}）
          </label>
        </template>
        <AppDataTable v-if="visibleAttachments.length" :columns="attachmentColumns" :rows="visibleAttachments as never" mobile-title-key="file_name">
          <template #reference="{ row }">
            <template v-if="referenceBadge(row as unknown as Attachment)">
              <StatusBadge :label="referenceBadge(row as unknown as Attachment)!.label"
                           :tone="referenceBadge(row as unknown as Attachment)!.tone" />
              <small v-if="(row as unknown as Attachment).reference_label" class="ref-label">
                {{ (row as unknown as Attachment).reference_label }}
              </small>
            </template>
            <span v-else class="muted">待录入</span>
          </template>
          <template #size_bytes="{ row }">{{ fmtSize(Number((row as unknown as Attachment).size_bytes)) }}</template>
          <template #actions="{ row }">
            <div class="acts">
              <button class="act" type="button" @click="downloadAttachment(row as unknown as Attachment)">下载</button>
              <button v-if="auth.can('delete')" class="act danger" type="button" @click="removeAttachment(row as unknown as Attachment)">删除</button>
            </div>
          </template>
        </AppDataTable>
        <StateBlock v-else state="empty" title="暂无附件"
                    :description="archivedCount ? '待处理附件已全部入库；勾选「显示已入库」可查看历史附件。' : '在上传识别页签上传材料。'" />
      </AppCard>
    </template>

    <!-- 调用记录 -->
    <template v-else-if="tab === 'logs'">
      <AppCard title="OCR 调用记录">
        <AppDataTable v-if="logs.length" :columns="logColumns" :rows="logs as never" mobile-title-key="created_at">
          <template #created_at="{ row }">{{ fmtDate((row as unknown as OcrLog).created_at) }}</template>
          <template #status_label="{ row }">
            <StatusBadge :label="(row as unknown as OcrLog).status_label"
                         :tone="(row as unknown as OcrLog).status === 'success' ? 'success' : 'danger'" />
          </template>
          <template #confidence="{ row }">{{ (row as unknown as OcrLog).confidence ?? '—' }}</template>
          <template #actions="{ row }">
            <div class="acts">
              <button v-if="(row as unknown as OcrLog).status === 'failed'" class="act" type="button" @click="retryLog((row as unknown as OcrLog).id)">
                <RefreshCw :size="13" /> 重试
              </button>
            </div>
          </template>
        </AppDataTable>
        <StateBlock v-else state="empty" title="暂无调用记录" description="OCR 识别后会在这里留下原文与结构化结果。" />
      </AppCard>
    </template>

    <!-- 对话式录入 -->
    <template v-else-if="tab === 'text'">
      <AppCard
        title="对话式录入"
        subtitle="粘贴账单 / 聊天记录 / 随手记，由本地 AI 读成一条条待确认草稿；模型不可用时自动退回规则解析，不会卡住不能录。"
      >
        <textarea
          v-model="textInput" class="csv-area"
          placeholder="每行一条，例如：&#10;昨天超市买菜 128 元&#10;10月15日 工资到账 12000&#10;加油 300"
        ></textarea>
        <div class="imp-actions">
          <AppButton variant="secondary" :loading="textParsing" @click="parseText">解析</AppButton>
          <AppButton variant="primary" :disabled="!textItems.length" :loading="textCreating" @click="createDraftsFromText">
            生成草稿（{{ textItems.filter((i) => i.ok).length }}）
          </AppButton>
        </div>
        <p v-if="textParser" class="hint">
          <strong>{{ textParser === 'ai' ? '本地 AI 已解析' : '本次为规则解析' }}</strong>
          <template v-if="textParser === 'rule' && textAiError">（{{ textAiError }}）</template>
          · {{ textCaliber }}
          <button v-if="textParser === 'rule'" class="inline-link" type="button"
                  @click="$router.push('/settings/local-ai')">检查本地 AI 配置</button>
        </p>
        <p v-else class="hint">点「解析」后由本地 AI 理解文本；模型不在线时自动退回规则解析，结果都只是草稿建议，确认后才入账。</p>
      </AppCard>

      <AppCard v-if="textItems.length" title="解析结果（可直接修改后再生成草稿）">
        <div class="trows">
          <div v-for="(it, idx) in textItems" :key="idx" class="trow" :class="{ bad: !it.ok }">
            <AppSelect v-model="it.event_type" class="c-type"
              :options="[{ value: 'expense', label: '支出' }, { value: 'income', label: '收入' }]" />
            <AppInput v-model="it.title" class="c-title" placeholder="标题" />
            <AppInput v-model="it.amount" class="c-amt" placeholder="金额" />
            <AppDatePicker v-model="it.occurred_at" class="c-date" size="sm" placeholder="日期" />
            <span class="c-tag">{{ it.category_name || '未分类' }}</span>
            <StatusBadge
              :label="it.confidence === 'high' ? '高' : it.confidence === 'medium' ? '中' : '待补'"
              :tone="it.confidence === 'high' ? 'success' : it.confidence === 'medium' ? 'warning' : 'danger'"
            />
          </div>
        </div>
        <p class="hint">共 {{ textItems.length }} 条，有效 {{ textItems.filter((i) => i.ok).length }} 条（红框需补充金额）。</p>
      </AppCard>
    </template>

    <!-- 数据导入 -->
    <template v-else>
      <AppCard title="批量导入流水（CSV）" subtitle="人工审核式导入：成功行生成草稿，确认后才入账；错误行与重复行自动隔离。">
        <div class="import-grid">
          <div class="imp-left">
            <AppField label="文件名">
              <AppInput v-model="importFileName" placeholder="流水导入.csv" />
            </AppField>
            <AppField label="CSV 内容" hint="表头：类型,标题,金额,日期,账户,分类,备注（账户/分类用名称匹配）">
              <textarea v-model="importContent" class="csv-area" placeholder="类型,标题,金额,日期,账户,分类&#10;收入,工资,20000,2026-10-01,工资卡,工资收入&#10;支出,餐饮,500,2026-10-02,工资卡,餐饮"></textarea>
            </AppField>
            <div class="imp-actions">
              <AppButton variant="secondary" :loading="importPreviewing" @click="previewImport">预览</AppButton>
              <AppButton variant="primary" @click="createImport">生成导入批次</AppButton>
            </div>
          </div>
          <div v-if="importPreview" class="imp-right">
            <p class="imp-sum">
              共 {{ importPreview.total }} 行：成功 {{ importPreview.success }} · 错误 {{ importPreview.error }}
              · 批次内重复 {{ importPreview.duplicate }}
              <template v-if="importPreview.suspected_duplicate">
                · <strong class="warn">与已有流水疑似重复 {{ importPreview.suspected_duplicate }}</strong>
              </template>
            </p>
            <ul class="imp-rows">
              <li v-for="(r, i) in importPreview.rows" :key="i"
                  :class="{ err: r.errors.length, dup: r.duplicate, sus: r.suspected_duplicate }">
                <div class="r-head">
                  <span class="r-t">{{ r.title || '（空标题）' }}</span>
                  <span class="r-a">{{ r.amount }} · {{ r.account || '—' }}</span>
                </div>
                <small v-if="r.errors.length">{{ r.errors.join('；') }}</small>
                <small v-else-if="r.duplicate">批次内重复，已自动跳过</small>
                <!-- 与已有流水同日期 + 同金额：交给用户判断是不是同一笔 -->
                <div v-else-if="r.suspected_duplicate" class="dup-box">
                  <p class="dup-tip">
                    库里已有 {{ r.suspected_duplicates.length }} 笔「{{ r.amount }} 元 · {{ (r.occurred_at || '').slice(0, 10) }}」的记录：
                  </p>
                  <ul class="dup-list">
                    <li v-for="d in r.suspected_duplicates" :key="d.event_no">
                      <span class="d-t">{{ d.title }}</span>
                      <span class="d-m">{{ d.event_no }} · {{ d.status_label }} · {{ d.source_label }}</span>
                    </li>
                  </ul>
                  <p class="dup-choose">
                    是同一笔吗？
                    <label>
                      <input type="radio" :name="`dup-${i}`" :checked="importSkipRows.has(i + 1)"
                             @change="importSkipRows.add(i + 1)" />
                      是，跳过（不重复入账）
                    </label>
                    <label>
                      <input type="radio" :name="`dup-${i}`" :checked="!importSkipRows.has(i + 1)"
                             @change="importSkipRows.delete(i + 1)" />
                      不是，照常导入
                    </label>
                  </p>
                </div>
                <small v-else class="ok">可导入</small>
              </li>
            </ul>
            <p v-if="importPreview.suspected_duplicate" class="imp-tip">
              疑似重复只按「金额 + 日期」提示，同一天同金额也可能是两笔真实消费；请逐条确认后再生成批次。
            </p>
          </div>
        </div>
      </AppCard>

      <AppCard title="导入批次">
        <StateBlock v-if="importLoading" state="loading" />
        <StateBlock v-else-if="!importBatches.length" state="empty" title="暂无导入批次"
                    description="生成导入批次后，可在这里逐批确认草稿。" />
        <ul v-else class="imp-batches">
          <li v-for="b in importBatches" :key="b.id">
            <div class="b-main">
              <strong>{{ b.source_file_name }}</strong>
              <small>{{ b.status_label }} · 成功 {{ b.success_rows }} / 错误 {{ b.error_rows }} / 重复 {{ b.duplicate_count }} · {{ fmtDate(b.created_at) }}</small>
            </div>
            <div class="b-actions">
              <button v-if="b.status === 'draft'" class="act" type="button" @click="confirmImport(b.id)">批量确认</button>
              <StatusBadge v-else label="已确认" tone="success" />
            </div>
          </li>
        </ul>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.inline-link {
  margin-left: 4px; padding: 0; border: 0; background: none; cursor: pointer;
  color: var(--primary); font-size: inherit; text-decoration: underline;
}
/* 导入查重 */
.imp-rows li.sus { border-left: 3px solid var(--warning, #f4a62a); }
.r-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.dup-box { margin-top: 6px; padding: 8px 10px; border: 1px dashed var(--border); border-radius: 8px; background: var(--glass-1); }
.dup-tip { font-size: 12px; color: var(--ink-muted); }
.dup-list { margin: 4px 0 6px; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 2px; }
.dup-list li { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--ink-strong); }
.d-m { color: var(--ink-muted); }
.dup-choose { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 12.5px; color: var(--ink); }
.dup-choose label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.dup-choose input { accent-color: var(--primary); }
.imp-tip { margin-top: 6px; font-size: 11.5px; color: var(--ink-muted); line-height: 1.5; }
.warn { color: var(--warning, #b26a00); }
.ok { color: var(--ink-muted); }

.capture { display: flex; flex-direction: column; gap: var(--space-4); }
.seg { display: inline-flex; flex-wrap: wrap; max-width: 100%; gap: 2px; padding: 2px; align-self: flex-start; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.seg button { height: 30px; padding: 0 14px; border: none; border-radius: 9px; background: transparent; color: var(--ink-muted); font-size: 13px; cursor: pointer; }
.seg button.on { color: var(--ink-strong); background: var(--glass-3); font-weight: 600; }
.upload-row { display: flex; gap: var(--space-2); flex-wrap: wrap; align-items: center; }
.mt { width: 140px; }
.hidden-input { display: none; }
.fname { font-size: 12.5px; color: var(--ink-muted); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hint { font-size: 12px; color: var(--ink-muted); }
.pick-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: var(--space-2); margin-bottom: var(--space-3); }
.pick-item { display: flex; flex-direction: column; gap: 2px; padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); text-align: left; cursor: pointer; }
.pick-item.on { border-color: var(--primary); }
.pn { font-size: 13px; color: var(--ink-strong); }
.pm { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 12px; color: var(--ink-muted); }
/* 已入库提示与开关 */
.archived-note { margin-top: var(--space-2); font-size: 12px; color: var(--ink-muted); }
.archived-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-muted); cursor: pointer; }
.archived-toggle input { width: 14px; height: 14px; accent-color: var(--primary); cursor: pointer; }
.ref-label { display: block; margin-top: 3px; font-size: 11.5px; color: var(--ink-muted); }
.muted { font-size: 12.5px; color: var(--ink-muted); }
.ocr-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
@media (max-width: 860px) { .ocr-grid { grid-template-columns: 1fr; } }
.raw h4 { font-size: 13.5px; color: var(--ink-strong); margin-bottom: var(--space-2); }
.raw pre { white-space: pre-wrap; word-break: break-all; font-size: 12.5px; color: var(--ink); background: var(--glass-1); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--space-3); max-height: 360px; overflow: auto; }
.conf { display: flex; align-items: center; gap: var(--space-2); font-size: 12.5px; color: var(--ink-muted); }
.form { display: flex; flex-direction: column; gap: var(--space-3); }
.acts { display: flex; gap: var(--space-2); flex-wrap: wrap; }
.act { display: inline-flex; align-items: center; gap: 4px; height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1); color: var(--ink-strong); font-size: 12.5px; cursor: pointer; text-decoration: none; }
.act:hover { border-color: var(--primary); color: var(--primary); }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.import-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
@media (max-width: 860px) { .import-grid { grid-template-columns: 1fr; } }
.imp-left { display: flex; flex-direction: column; gap: var(--space-3); }
.csv-area { width: 100%; min-height: 180px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); color: var(--ink-strong); font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; resize: vertical; outline: none; }
.imp-actions { display: flex; gap: var(--space-2); }
.imp-sum { font-size: 13px; color: var(--ink-strong); margin-bottom: var(--space-2); }
.imp-rows { display: flex; flex-direction: column; gap: 4px; max-height: 300px; overflow: auto; }
.imp-rows li { display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1); font-size: 12.5px; color: var(--ink-strong); }
.imp-rows li.err { border-color: var(--danger); }
.imp-rows li.dup { border-color: var(--warning); }
.imp-rows small { color: var(--ink-muted); }
.imp-rows li.err small { color: var(--danger); }
.imp-rows li.dup small { color: var(--warning); }
.imp-batches { display: flex; flex-direction: column; gap: 6px; }
.imp-batches li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.b-main { display: flex; flex-direction: column; gap: 2px; }
.b-main strong { font-size: 13.5px; color: var(--ink-strong); }
.b-main small { font-size: 12px; color: var(--ink-muted); }
.b-actions { display: flex; align-items: center; gap: var(--space-2); }
.trows { display: flex; flex-direction: column; gap: 6px; }
.trow { display: grid; grid-template-columns: 96px 1fr 120px 130px 90px auto; gap: 6px; align-items: center; }
.trow.bad { border-left: 3px solid var(--danger); padding-left: 6px; }
.c-tag { font-size: 12px; color: var(--ink-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 860px) { .trow { grid-template-columns: 1fr 1fr; } }
</style>
