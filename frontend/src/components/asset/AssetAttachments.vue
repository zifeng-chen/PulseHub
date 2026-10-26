<script setup lang="ts">
/**
 * 资产附件容器（证件、发票、合同、照片）。
 *
 * - 新增资产时先暂存本地文件，资产创建成功后再统一上传并绑定 asset；
 * - 编辑 / 详情时直接上传到当前资产，并可下载、删除；
 * - 图片走带鉴权的 blob 预览，PDF 显示文件卡，不暴露真实存储路径。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Download, FileText, Paperclip, Trash2, Upload } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'

interface AttachmentRow {
  id: string
  file_name: string
  mime_type: string
  size_bytes: number
  status?: string
}

const props = withDefaults(
  defineProps<{
    assetId?: string
    pending?: File[]
    disabled?: boolean
    hint?: string
  }>(),
  { assetId: '', pending: () => [], disabled: false, hint: '' },
)

const emit = defineEmits<{ 'update:pending': [File[]] }>()
const toast = useToastStore()
const ui = useUiStore()

const MAX_FILES = 5
const MAX_MB = 20
const rows = ref<AttachmentRow[]>([])
const loading = ref(false)
const uploading = ref(false)
const input = ref<HTMLInputElement | null>(null)
const thumbs = ref<Record<string, string>>({})
const previews = new Map<File, string>()

const count = computed(() => rows.value.length + props.pending.length)
const canAdd = computed(() => !props.disabled && count.value < MAX_FILES)

function sizeLabel(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function previewOf(file: File) {
  let url = previews.get(file)
  if (!url) {
    url = URL.createObjectURL(file)
    previews.set(file, url)
  }
  return url
}

function isImage(mime: string, name = '') {
  return mime?.startsWith('image/') || /\.(png|jpe?g|webp)$/i.test(name)
}

function revokeThumbs() {
  Object.values(thumbs.value).forEach((url) => URL.revokeObjectURL(url))
  thumbs.value = {}
}

let loadSeq = 0
async function load() {
  if (!props.assetId) {
    rows.value = []
    revokeThumbs()
    return
  }
  const seq = ++loadSeq
  const assetId = props.assetId
  loading.value = true
  try {
    const res = await api.get<{ items: AttachmentRow[] }>('/ledger/capture/attachments', {
      object_type: 'asset',
      object_id: assetId,
    })
    if (seq !== loadSeq) return
    rows.value = res.items || []
    revokeThumbs()
    const token = localStorage.getItem('fam.access_token') || ''
    // 并发拉取缩略图；切换资产后旧请求的结果直接丢弃，避免写进新资产的列表
    const images = rows.value.filter((row) => isImage(row.mime_type, row.file_name))
    await Promise.all(images.map(async (row) => {
      try {
        const resp = await fetch(`/api/v1/ledger/capture/attachments/${row.id}/download`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!resp.ok || seq !== loadSeq) return
        const url = URL.createObjectURL(await resp.blob())
        if (seq !== loadSeq) {
          URL.revokeObjectURL(url)
          return
        }
        thumbs.value[row.id] = url
      } catch {
        /* 预览失败保留文件卡 */
      }
    }))
  } catch (err) {
    if (seq === loadSeq) toast.error(errorMessage(err))
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

watch(() => props.assetId, load, { immediate: true })
onBeforeUnmount(() => {
  revokeThumbs()
  previews.forEach((url) => URL.revokeObjectURL(url))
  previews.clear()
})

function pick() {
  input.value?.click()
}

function validate(file: File): string {
  const okType = /\.(png|jpe?g|pdf)$/i.test(file.name)
  if (!okType) return `${file.name}：只支持 JPG / PNG / PDF`
  if (file.size > MAX_MB * 1024 * 1024) return `${file.name}：单文件不能超过 ${MAX_MB}MB`
  return ''
}

async function onPick(e: Event) {
  const el = e.target as HTMLInputElement
  const files = Array.from(el.files || [])
  el.value = ''
  if (!files.length) return

  const accepted: File[] = []
  for (const file of files) {
    const problem = validate(file)
    if (problem) {
      toast.error(problem)
      continue
    }
    accepted.push(file)
  }
  if (!accepted.length) return

  const room = MAX_FILES - count.value
  if (accepted.length > room) {
    toast.error(`最多保留 ${MAX_FILES} 个附件，已忽略多余文件`)
  }
  const chosen = accepted.slice(0, Math.max(0, room))

  if (!props.assetId) {
    emit('update:pending', [...props.pending, ...chosen])
    return
  }

  uploading.value = true
  try {
    for (const file of chosen) {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('object_type', 'asset')
      fd.append('object_id', props.assetId)
      await api.post('/ledger/capture/attachments', fd)
    }
    toast.success('附件已上传')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    uploading.value = false
  }
}

function removePending(index: number) {
  const file = props.pending[index]
  const url = previews.get(file)
  if (url) {
    URL.revokeObjectURL(url)
    previews.delete(file)
  }
  emit('update:pending', props.pending.filter((_, i) => i !== index))
}

/** 供父级在资产创建成功后调用：把暂存文件真正上传并绑定新资产 */
async function flush(assetId: string): Promise<File[]> {
  const failed: File[] = []
  for (const file of props.pending) {
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('object_type', 'asset')
      fd.append('object_id', assetId)
      await api.post('/ledger/capture/attachments', fd)
    } catch {
      failed.push(file)
    }
  }
  emit('update:pending', failed)
  return failed
}

async function download(row: AttachmentRow) {
  try {
    const token = localStorage.getItem('fam.access_token') || ''
    const resp = await fetch(`/api/v1/ledger/capture/attachments/${row.id}/download`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!resp.ok) {
      toast.error('下载失败，请检查权限')
      return
    }
    const url = URL.createObjectURL(await resp.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = row.file_name
    a.click()
    // 延迟 revoke：Safari/Firefox 在 click 后立即释放会偶发取消下载（L-7）
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    toast.error('下载失败')
  }
}

async function remove(row: AttachmentRow) {
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
    const token = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'attachment.delete',
      object_type: 'attachment',
      object_id: row.id,
    })
    await api.delete(`/ledger/capture/attachments/${row.id}`, undefined, {
      reason,
      confirm_token: token.token,
    })
    toast.success('附件已删除（历史保留）')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

defineExpose({ flush, reload: load })
</script>

<template>
  <section class="atts">
    <header class="att-head">
      <span class="ttl"><Paperclip :size="14" /> 附件 <em v-if="count">（{{ count }}）</em></span>
      <span v-if="hint" class="tip">{{ hint }}</span>
    </header>

    <div v-if="loading" class="loading">正在加载附件…</div>

    <div class="att-grid">
      <article v-for="row in rows" :key="row.id" class="att-card">
        <div class="thumb">
          <img v-if="thumbs[row.id]" :src="thumbs[row.id]" :alt="row.file_name" />
          <FileText v-else :size="22" />
        </div>
        <div class="meta">
          <strong :title="row.file_name">{{ row.file_name }}</strong>
          <small>{{ sizeLabel(row.size_bytes) }}</small>
        </div>
        <div v-if="!disabled" class="ops">
          <button type="button" title="下载" @click="download(row)"><Download :size="13" /></button>
          <button type="button" class="danger" title="删除" @click="remove(row)"><Trash2 :size="13" /></button>
        </div>
      </article>

      <article v-for="(file, idx) in pending" :key="`p-${file.name}-${idx}`" class="att-card pending">
        <div class="thumb">
          <img v-if="isImage(file.type, file.name)" :src="previewOf(file)" :alt="file.name" />
          <FileText v-else :size="22" />
        </div>
        <div class="meta">
          <strong :title="file.name">{{ file.name }}</strong>
          <small>{{ sizeLabel(file.size) }} · 保存后上传</small>
        </div>
        <div v-if="!disabled" class="ops">
          <button type="button" class="danger" title="移除" @click="removePending(idx)"><Trash2 :size="13" /></button>
        </div>
      </article>

      <button v-if="canAdd" type="button" class="add" :disabled="uploading" @click="pick">
        <Upload :size="16" />
        <span>{{ uploading ? '上传中…' : '上传附件' }}</span>
      </button>
    </div>

    <input
      ref="input" type="file" class="hidden" multiple
      accept="image/png,image/jpeg,application/pdf"
      @change="onPick"
    />
    <p class="hint">支持 JPG / PNG / PDF，单文件不超过 {{ MAX_MB }}MB，最多 {{ MAX_FILES }} 个。</p>
  </section>
</template>

<style scoped>
.atts { display: flex; flex-direction: column; gap: 8px; }
.att-head { display: flex; align-items: baseline; gap: 8px; }
.ttl { display: inline-flex; align-items: center; gap: 5px; font-size: 13px; font-weight: 600; color: var(--ink-strong); }
.ttl em { font-style: normal; color: var(--ink-muted); font-weight: 500; }
.tip { font-size: 11.5px; color: var(--ink-muted); }
.loading { font-size: 12px; color: var(--ink-muted); }
.att-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
.att-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--glass-1);
  min-width: 0;
}
.att-card.pending { border-style: dashed; }
.thumb {
  flex: none;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  overflow: hidden;
  background: var(--glass-2);
  color: var(--ink-muted);
}
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.meta { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 1px; }
.meta strong { font-size: 12.5px; font-weight: 500; color: var(--ink-strong); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta small { font-size: 11px; color: var(--ink-muted); }
.ops { position: absolute; top: 4px; right: 4px; display: flex; gap: 2px; opacity: 0; transition: opacity var(--motion-fast) var(--ease-out); }
.att-card:hover .ops, .att-card:focus-within .ops { opacity: 1; }
.ops button {
  width: 22px; height: 22px; display: grid; place-items: center;
  border: none; border-radius: 6px; background: var(--surface); color: var(--ink);
  cursor: pointer; box-shadow: var(--shadow-card);
}
.ops button:hover { color: var(--primary); }
.ops button.danger:hover { color: var(--danger); }
.add {
  min-height: 58px;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px;
  border: 1px dashed var(--border); border-radius: var(--radius-md);
  background: transparent; color: var(--ink-muted); font-size: 12px; font-family: inherit; cursor: pointer;
  transition: border-color var(--motion-fast) var(--ease-out), color var(--motion-fast) var(--ease-out);
}
.add:hover { border-color: var(--primary); color: var(--primary); }
.add:disabled { opacity: 0.6; cursor: wait; }
.hidden { display: none; }
.hint { font-size: 11.5px; color: var(--ink-muted); }
@media (max-width: 640px) {
  .att-grid { grid-template-columns: 1fr; }
  .ops { opacity: 1; }
}
</style>
