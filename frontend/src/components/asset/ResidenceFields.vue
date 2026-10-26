<script setup lang="ts">
/**
 * 住宅专项字段（不动产 / 住宅）。
 * 只保留真正需要录入的信息：证件、地址、面积、物业费单价、购入价格、登记方式。
 * AI 识别房产证就在本区块内（不再放独立入口），识别结果由用户核对后保存；
 * 识别用的照片会同时进入附件容器（新增时先暂存，资产创建后一起上传）。
 */
import { ref } from 'vue'
import { ScanLine, Sparkles } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useToastStore } from '@/stores/toast'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'

interface AccountLite { id: string; name: string; currency: string }

const props = withDefaults(
  defineProps<{
    form: Record<string, any>
    extForm: Record<string, any>
    errors: Record<string, string>
    accounts: AccountLite[]
    /** 新增时展示水电燃气户号；编辑不展示（详情页已可维护） */
    showUtilities?: boolean
    utilities?: Record<string, string>
    /** 待上传附件（与 AssetAttachments 共用同一份暂存列表） */
    pending?: File[]
  }>(),
  { showUtilities: false, utilities: () => ({}), pending: () => [] },
)

const emit = defineEmits<{ 'update:pending': [File[]] }>()

const utils = props.utilities
const toast = useToastStore()

/** 附件容器支持的类型（JPG / PNG）；webp、bmp 只用于识别，不进附件 */
const ATTACHABLE = /\.(png|jpe?g)$/i
const MAX_ATTACH = 5

function attachPhoto(file: File) {
  if (!ATTACHABLE.test(file.name)) return
  const list = props.pending
  if (list.some((f) => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified)) return
  if (list.length >= MAX_ATTACH) {
    toast.error(`附件最多 ${MAX_ATTACH} 个，这张照片没有加入附件，可在下方附件区删掉一个后重传`)
    return
  }
  emit('update:pending', [...list, file])
}

const certInput = ref<HTMLInputElement | null>(null)
const ocrBusy = ref(false)
const lastRaw = ref('')

/** 关键字段：识别不到的要在提示里点名，避免用户以为已经填全 */
const KEY_FIELDS: [string, string][] = [
  ['address', '坐落地址'],
  ['ownership_no', '不动产权证号'],
  ['building_area', '建筑面积'],
  ['built_year', '建成年份'],
]

function applyFields(fields: Record<string, string>) {
  const form = props.form
  const ext = props.extForm
  if (fields.address) {
    ext.address = fields.address
    if (!form.name || form.name === '住宅' || form.name === '房产') form.name = fields.address
  }
  if (fields.ownership_no) ext.ownership_no = fields.ownership_no
  if (fields.title_no) ext.title_no = fields.title_no
  if (fields.building_area) ext.building_area = fields.building_area
  if (fields.inner_area) ext.inner_area = fields.inner_area
  if (fields.built_year) ext.built_year = fields.built_year
}

/**
 * 手机原图经常 5~8MB，OCR 接口对 base64 后的大小有限制（约 4MB）。
 * 超过 3MB 就先等比压到长边 2200px 再上传，附件里保留原图。
 */
const OCR_MAX_BYTES = 3 * 1024 * 1024

async function shrinkForOcr(file: File): Promise<File> {
  if (file.size <= OCR_MAX_BYTES) return file
  try {
    const bitmap = await createImageBitmap(file)
    const maxSide = 2200
    const ratio = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height))
    const width = Math.max(1, Math.round(bitmap.width * ratio))
    const height = Math.max(1, Math.round(bitmap.height * ratio))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) return file
    ctx.drawImage(bitmap, 0, 0, width, height)
    bitmap.close?.()
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.86))
    if (!blob || blob.size >= file.size) return file
    return new File([blob], file.name.replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' })
  } catch {
    return file
  }
}

async function onCertFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  // 先入附件：即使识别失败，这张房本照片也留在附件里
  attachPhoto(file)
  ocrBusy.value = true
  lastRaw.value = ''
  try {
    const fd = new FormData()
    fd.append('file', await shrinkForOcr(file))
    const res = await api.post<{ fields: Record<string, string>; filled: number; raw_text: string }>(
      '/ledger/assets/ocr-extract', fd,
    )
    const fields = res.fields || {}
    applyFields(fields)
    lastRaw.value = res.raw_text || ''
    const missing = KEY_FIELDS.filter(([key]) => !fields[key]).map(([, label]) => label)
    if (res.filled && !missing.length) {
      toast.success('识别完成，证号、坐落、面积已带入，请核对后保存；照片已加入附件')
    } else if (res.filled) {
      toast.success(`识别完成，${missing.join('、')}没识别到，请手工补充；照片已加入附件`)
    } else {
      toast.error('没有识别到可用字段，请检查照片是否清晰或手工填写；照片已加入附件')
    }
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    ocrBusy.value = false
  }
}
</script>

<template>
  <div class="ocr-bar">
    <div class="ocr-copy">
      <strong><Sparkles :size="14" /> AI 识别房产证</strong>
      <span>上传房本照片，自动填写证号（含不动产单元号）、坐落地址、面积与建成年份；照片会同时存入下方附件。</span>
    </div>
    <AppButton variant="secondary" size="sm" :loading="ocrBusy" @click="certInput?.click()">
      <ScanLine :size="14" /> {{ ocrBusy ? '识别中…' : '上传照片识别' }}
    </AppButton>
    <input ref="certInput" type="file" accept="image/*" class="hidden-file" @change="onCertFile" />
  </div>
  <details v-if="lastRaw" class="raw">
    <summary>查看识别原文</summary>
    <pre>{{ lastRaw }}</pre>
  </details>

  <AppField label="房产证编号" :error="errors['extension.title_no']" hint="敏感字段，默认掩码展示">
    <AppInput v-model="extForm.title_no" placeholder="例：京(2020)东不动产权第 0001 号" />
  </AppField>
  <AppField label="不动产权证号" :error="errors['extension.ownership_no']" hint="敏感字段，默认掩码展示">
    <AppInput v-model="extForm.ownership_no" placeholder="与房产证号相同时可留空" />
  </AppField>
  <AppField label="坐落地址" :error="errors['extension.address']">
    <AppInput v-model="extForm.address" placeholder="例：北京市朝阳区望京街道××路 8 号院 3 号楼 2 单元 1201" />
  </AppField>
  <AppField label="房屋建筑面积（㎡）" :error="errors['extension.building_area']">
    <AppInput v-model="extForm.building_area" type="number" placeholder="例如：89.5" />
  </AppField>
  <AppField label="套内建筑面积（㎡）" :error="errors['extension.inner_area']">
    <AppInput v-model="extForm.inner_area" type="number" placeholder="例如：72.3" />
  </AppField>
  <AppField label="建成年份">
    <AppInput v-model="extForm.built_year" type="number" placeholder="例如：2010" min="1900" max="2100" />
  </AppField>

  <!-- 水电燃气户号：新增时一起登记，省得创建后再进详情补 -->
  <AppField
    v-if="showUtilities"
    label="水电燃气户号"
    hint="选填；只登记户号，缴费人、缴费周期可在资产详情继续补充"
  >
    <div class="grid-2">
      <AppInput v-model="utils.water" placeholder="水费户号" />
      <AppInput v-model="utils.electricity" placeholder="电费户号" />
      <AppInput v-model="utils.gas" placeholder="燃气户号" />
      <AppInput v-model="utils.heating" placeholder="供暖户号" />
    </div>
  </AppField>

  <AppField label="物业费单价（元/㎡·月）" :error="errors['extension.management_fee_price']"
            hint="用于估算每月物业费，不是房屋单价">
    <AppInput v-model="extForm.management_fee_price" type="number" placeholder="例如：3.5" />
  </AppField>
  <AppField label="购入价格" required :error="errors.acquire_cost"
            hint="以此作为资产初始价值；后续估值可在详情里更新">
    <AppInput v-model="form.acquire_cost" type="number" placeholder="例如：1800000" />
  </AppField>
  <AppField label="参考单价（元/㎡）" :error="errors['extension.unit_price']"
            hint="可选：填写后按「建筑面积 × 单价」估算当前价值，不填则用购入价格">
    <AppInput v-model="extForm.unit_price" type="number" placeholder="例如：65000" />
  </AppField>

  <AppField label="登记方式"
            hint="已购资产只登记价值，不动任何账户；待扣资金才是还没付款、需要从账户扣">
    <AppSelect v-model="form.acquisition_mode" :options="[
      { value: 'none', label: '已购资产（纯资产，不扣资金）' },
      { value: 'confirm', label: '待扣资金资产（从资金账户付款）' },
    ]" />
  </AppField>
  <template v-if="form.acquisition_mode === 'confirm'">
    <AppField label="资金来源账户" required :error="errors['acquisition.account_id']">
      <AppSelect
        v-model="form.acquisition_account"
        :options="[{ value: '', label: '请选择账户' }, ...accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
      />
    </AppField>
    <AppField label="扣款金额" :error="errors['acquisition.amount']" hint="留空则按购入价格扣款">
      <AppInput v-model="form.acquisition_amount" type="number" placeholder="默认等于购入价格" />
    </AppField>
  </template>

  <AppField label="房贷">
    <AppSwitch v-model="extForm.has_mortgage" label="这套房产有房贷" />
    <p class="hint">勾选后，保存资产会自动跳到「负债 → 新增房贷」，并带上关联资产。</p>
  </AppField>
</template>

<style scoped>
.ocr-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-md);
  background: var(--glass-1);
}
.ocr-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ocr-copy strong { display: inline-flex; align-items: center; gap: 5px; font-size: 13px; color: var(--ink-strong); }
.ocr-copy span { font-size: 11.5px; color: var(--ink-muted); line-height: 1.5; }
.raw { font-size: 12px; color: var(--ink-muted); }
.raw summary { cursor: pointer; }
.raw pre {
  margin: 6px 0 0;
  padding: 8px;
  max-height: 180px;
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--surface-subtle);
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 11.5px;
  line-height: 1.6;
}
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.hint { margin-top: 6px; font-size: 12px; color: var(--ink-muted); line-height: 1.5; }
.hidden-file { display: none; }
@media (max-width: 700px) {
  .grid-2 { grid-template-columns: 1fr; }
  .ocr-bar { align-items: flex-start; flex-direction: column; }
}
</style>
