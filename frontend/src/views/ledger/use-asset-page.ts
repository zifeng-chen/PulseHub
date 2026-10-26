/**
 * 资产类型列表页共享逻辑（06 §2、15）。
 * 不动产 / 车位 / 车辆 / 贵金属 四个页面共用：筛选、列表、统计、新增编辑、详情。
 * 金额与统计口径全部来自后端，前端不自行计算。
 */

import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorFields, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import {
  loadAssetMeta, type AccountLite, type AssetListResponse, type AssetRow,
  type AssetSummary, type Member,
} from './asset-shared'
import {
  emptyUtilities, finalizeAssetCreate, uploadAssetFiles, type UtilityDraft,
} from './asset-create-flow'

export interface UseAssetPageOptions {
  assetType: string
  endpoint: string
  extDefaults?: () => Record<string, unknown>
}

export function useAssetPage(opts: UseAssetPageOptions) {
  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()
  const toast = useToastStore()

  const loading = ref(false)
  const error = ref('')
  const rows = ref<AssetRow[]>([])
  const summary = ref<AssetSummary | null>(null)
  const members = ref<Member[]>([])
  const accounts = ref<AccountLite[]>([])
  const linkTargets = ref<{ id: string; name: string }[]>([])

  const keyword = ref('')
  const memberFilter = ref('')
  const statusFilter = ref('')

  const editorOpen = ref(false)
  const editorMode = ref<'create' | 'edit'>('create')
  const saving = ref(false)
  const fieldErrors = ref<Record<string, string>>({})

  const detailOpen = ref(false)
  const detailId = ref('')

  const canCreate = computed(() => auth.can('create_draft'))
  const canEdit = computed(() => auth.can('edit_draft'))

  const form = reactive({
    id: '', version: 1, name: '', sub_type: '', currency: 'CNY',
    owner_member_id: '', acquire_date: '', acquire_cost: '', current_value: '',
    value_source: '', depreciation_method: '', maintenance_date: '', linked_account_id: '',
    include_in_net_worth: true, note: '', reason: '',
    acquisition_mode: 'none', acquisition_account: '', acquisition_amount: '',
  })
  const extForm = reactive<Record<string, any>>({})
  const utilities = reactive<UtilityDraft>(emptyUtilities())
  const pendingFiles = ref<File[]>([])

  let loadSeq = 0
  async function load() {
    // 快速切换筛选时旧响应可能后到：只让最后一次请求写结果
    const seq = ++loadSeq
    loading.value = true
    error.value = ''
    try {
      const [list, meta] = await Promise.all([
        api.get<AssetListResponse>(opts.endpoint, {
          keyword: keyword.value || undefined,
          member_id: memberFilter.value || undefined,
          status: statusFilter.value || undefined,
        }),
        loadAssetMeta(),
      ])
      if (seq !== loadSeq) return
      rows.value = list.items
      summary.value = list.summary || null
      members.value = meta.members
      accounts.value = meta.accounts

      // 车位/车辆需要可选关联对象
      if (opts.assetType === 'parking') {
        const res = await api.get<AssetListResponse>('/ledger/assets/real-estates')
        if (seq !== loadSeq) return
        linkTargets.value = res.items.map((a) => ({ id: a.id, name: a.name }))
      } else if (opts.assetType === 'vehicle') {
        const res = await api.get<AssetListResponse>('/ledger/assets/parking-spaces')
        if (seq !== loadSeq) return
        linkTargets.value = res.items.map((a) => ({ id: a.id, name: a.name }))
      } else {
        linkTargets.value = []
      }
    } catch (err) {
      if (seq === loadSeq) error.value = errorMessage(err)
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  function _reset() {
    Object.keys(extForm).forEach((k) => delete extForm[k])
    Object.assign(form, {
      id: '', version: 1, name: '', sub_type: '', currency: 'CNY',
      owner_member_id: '', acquire_date: '', acquire_cost: '', current_value: '',
      value_source: '', depreciation_method: '', maintenance_date: '', linked_account_id: '',
      include_in_net_worth: true, note: '', reason: '',
      acquisition_mode: 'none', acquisition_account: '', acquisition_amount: '',
    })
    Object.assign(extForm, opts.extDefaults ? opts.extDefaults() : {})
    Object.assign(utilities, emptyUtilities())
    pendingFiles.value = []
  }

  function openCreate() {
    fieldErrors.value = {}
    _reset()
    editorMode.value = 'create'
    editorOpen.value = true
  }

  function openEdit(row: AssetRow) {
    fieldErrors.value = {}
    _reset()
    Object.assign(form, {
      id: row.id, version: row.version, name: row.name, sub_type: row.sub_type || '',
      currency: row.currency, owner_member_id: row.owner_member_id || '',
      acquire_date: row.acquire_date ? row.acquire_date.slice(0, 10) : '',
      acquire_cost: row.acquire_cost, current_value: row.current_value,
      value_source: row.value_source || '', depreciation_method: row.depreciation_method || '',
      maintenance_date: row.maintenance_date ? row.maintenance_date.slice(0, 10) : '',
      linked_account_id: row.linked_account_id || '',
      include_in_net_worth: row.include_in_net_worth, note: row.note || '',
      reason: '', acquisition_mode: 'none', acquisition_account: '', acquisition_amount: '',
    })
    Object.assign(extForm, row.extension || {})
    editorMode.value = 'edit'
    editorOpen.value = true
  }

  async function save() {
    saving.value = true
    fieldErrors.value = {}
    const body: Record<string, unknown> = {
      asset_type: opts.assetType,
      name: form.name,
      sub_type: form.sub_type || null,
      currency: form.currency,
      owner_member_id: form.owner_member_id || null,
      acquire_date: form.acquire_date || null,
      acquire_cost: form.acquire_cost || '0',
      current_value: form.current_value || null,
      value_source: form.value_source || null,
      depreciation_method: form.depreciation_method || null,
      maintenance_date: form.maintenance_date || null,
      linked_account_id: form.linked_account_id || null,
      include_in_net_worth: form.include_in_net_worth,
      note: form.note || null,
      extension: { ...extForm },
    }
    try {
      if (editorMode.value === 'create') {
        if (form.acquisition_mode !== 'none') {
          body.acquisition = {
            mode: form.acquisition_mode,
            account_id: form.acquisition_account || null,
            amount: form.acquisition_amount || form.acquire_cost || '0',
          }
        }
        const created = await api.post<AssetRow>('/ledger/assets', body)
        toast.success('资产已创建')
        editorOpen.value = false
        const extra = await finalizeAssetCreate({
          assetId: created.id,
          assetType: created.asset_type,
          assetName: created.name,
          pendingFiles: pendingFiles.value,
          utilities,
          hasMortgage: Boolean(extForm.has_mortgage),
          hasLoan: Boolean(extForm.has_loan),
          router,
        })
        pendingFiles.value = []
        if (extra.failedFiles) toast.error(`${extra.failedFiles} 个附件上传失败，可在资产详情里重新上传`)
        if (extra.failedUtilities) toast.error(`${extra.failedUtilities} 个户号未保存，可在资产详情里补充`)
        if (extra.loanType) {
          toast.success('已跳转到贷款信息填写，请补充金额与还款计划')
          return
        }
        await load()
        return
      } else {
        body.version = form.version
        if (form.reason) body.reason = form.reason
        await api.put(`/ledger/assets/${form.id}`, body)
        toast.success('资产已更新')
        // 编辑态识别房产证时照片先暂存在本地，保存成功后补传
        if (pendingFiles.value.length) {
          const failed = await uploadAssetFiles(form.id, pendingFiles.value)
          pendingFiles.value = []
          if (failed) toast.error(`${failed} 个附件上传失败，可在详情里重新上传`)
        }
      }
      editorOpen.value = false
      await load()
    } catch (err) {
      fieldErrors.value = errorFields(err)
      toast.error(errorMessage(err))
    } finally {
      saving.value = false
    }
  }

  function openDetail(row: AssetRow) {
    detailId.value = row.id
    detailOpen.value = true
  }

  onMounted(async () => {
    await load()
    const openId = route.query.open
    if (typeof openId === 'string' && openId) {
      detailId.value = openId
      detailOpen.value = true
    }
  })

  return {
    loading, error, rows, summary, members, accounts, linkTargets,
    keyword, memberFilter, statusFilter,
    editorOpen, editorMode, saving, fieldErrors, form, extForm,
    detailOpen, detailId, canCreate, canEdit,
    utilities, pendingFiles,
    load, openCreate, openEdit, save, openDetail,
  }
}
