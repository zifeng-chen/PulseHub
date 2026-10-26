/**
 * 资产创建后的收尾动作（总览页与各专项页共用）：
 * 1. 上传暂存附件并绑定到新资产；
 * 2. 住宅登记水电燃气户号；
 * 3. 勾选了房贷 / 车贷时，跳转到负债页并自动打开新增表单（带上关联资产）。
 *
 * 这些动作都发生在资产创建成功之后，失败不回滚资产本身，只提示哪些没成功。
 */
import type { Router } from 'vue-router'
import { api } from '@/api/client'

export interface UtilityDraft {
  water: string
  electricity: string
  gas: string
  heating: string
}

export function emptyUtilities(): UtilityDraft {
  return { water: '', electricity: '', gas: '', heating: '' }
}

const UTILITY_TYPES: { key: keyof UtilityDraft; type: string }[] = [
  { key: 'water', type: 'water' },
  { key: 'electricity', type: 'electricity' },
  { key: 'gas', type: 'gas' },
  { key: 'heating', type: 'heating' },
]

/** 把暂存文件上传并绑定到资产；返回失败数量（失败不回滚已上传的部分）。 */
export async function uploadAssetFiles(assetId: string, files: File[]): Promise<number> {
  let failed = 0
  for (const file of files) {
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('object_type', 'asset')
      fd.append('object_id', assetId)
      await api.post('/ledger/capture/attachments', fd)
    } catch {
      failed += 1
    }
  }
  return failed
}

export interface FinalizeResult {
  failedFiles: number
  failedUtilities: number
  loanType: '' | 'mortgage' | 'vehicle_loan'
}

export async function finalizeAssetCreate(opts: {
  assetId: string
  assetType: string
  assetName: string
  pendingFiles: File[]
  utilities: UtilityDraft
  hasMortgage: boolean
  hasLoan: boolean
  router: Router
}): Promise<FinalizeResult> {
  const failedFiles = await uploadAssetFiles(opts.assetId, opts.pendingFiles)

  let failedUtilities = 0
  if (opts.assetType === 'real_estate') {
    for (const item of UTILITY_TYPES) {
      const accountNo = (opts.utilities[item.key] || '').trim()
      if (!accountNo) continue
      try {
        await api.post(`/ledger/assets/real-estates/${opts.assetId}/utility-accounts`, {
          utility_type: item.type,
          account_no: accountNo,
        })
      } catch {
        failedUtilities += 1
      }
    }
  }

  let loanType: FinalizeResult['loanType'] = ''
  if (opts.assetType === 'real_estate' && opts.hasMortgage) loanType = 'mortgage'
  else if (opts.assetType === 'vehicle' && opts.hasLoan) loanType = 'vehicle_loan'

  if (loanType) {
    await opts.router.push({
      path: '/ledger/liabilities',
      query: {
        new: '1',
        type: loanType,
        asset_id: opts.assetId,
        name: `${loanType === 'mortgage' ? '房贷' : '车贷'} · ${opts.assetName}`,
      },
    })
  }

  return { failedFiles, failedUtilities, loanType }
}
