<script setup lang="ts">
/**
 * 账单抵扣（抵扣金 / 积分抵现）。
 *
 * 抵扣是银行对账单的减免，没有现金流入：账单未还与信用卡欠款同步减少，
 * 但不计入收支。这里只负责录入与撤销，账务落库由后端一趟做完
 * （账单 offset_amount + card_offset 事件）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import { formatAmount } from '@/utils/format'
import AppModal from '@/components/ui/AppModal.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'

interface BillInfo {
  id: string
  total_amount: string
  paid_amount: string
  offset_amount: string
  unpaid_amount: string
  due_date: string | null
  currency: string
}

interface OffsetRow {
  id: string
  offset_type_label: string
  amount: string
  points: number | null
  note: string | null
  status: string
  created_at: string | null
}

const props = defineProps<{
  open: boolean
  accountId: string
  accountName: string
  bill: BillInfo | null
  /** 信用卡当前欠款（正数），用于提示抵扣后的欠款 */
  creditUsed?: string
}>()

const emit = defineEmits<{ close: []; done: [] }>()

const toast = useToastStore()
const ui = useUiStore()
const saving = ref(false)
const loading = ref(false)
const offsets = ref<OffsetRow[]>([])
const points = ref<number | null>(null)
const pointsDate = ref('')
const revokeId = ref('')

const form = reactive({
  offset_type: 'credit' as 'credit' | 'points',
  amount: '',
  points: '',
  note: '',
})

const unpaid = computed(() => Number(props.bill?.unpaid_amount || 0))
const amountNum = computed(() => Number(form.amount || 0))
const afterUnpaid = computed(() => Math.max(0, unpaid.value - amountNum.value))
const debtNow = computed(() => Number(props.creditUsed || 0))
const afterDebt = computed(() => Math.max(0, debtNow.value - amountNum.value))
const overLimit = computed(() => amountNum.value > unpaid.value + 0.001)
/** 已结清时只能看记录 / 撤销，不能再抵（抵了会变成溢缴） */
const canOffset = computed(() => unpaid.value > 0)
const confirmText = computed(() => (canOffset.value ? '确认抵扣' : '关闭'))

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    form.offset_type = 'credit'
    form.amount = props.bill ? props.bill.unpaid_amount : ''
    form.points = ''
    form.note = ''
    revokeId.value = ''
    await Promise.all([loadOffsets(), loadPoints()])
  },
)

async function loadOffsets() {
  if (!props.bill) return
  loading.value = true
  try {
    const data = await api.get<{ items: OffsetRow[] }>(
      `/ledger/accounts/${props.accountId}/card-bills/${props.bill.id}/offsets`,
    )
    offsets.value = data.items || []
  } catch {
    offsets.value = []
  } finally {
    loading.value = false
  }
}

async function loadPoints() {
  try {
    const data = await api.get<{ latest: { points: number; snapshot_date: string } | null }>(
      '/ledger/card-rewards',
    )
    points.value = data.latest?.points ?? null
    pointsDate.value = data.latest?.snapshot_date?.slice(0, 10) || ''
  } catch {
    points.value = null
  }
}

async function submit() {
  if (!props.bill) return
  if (!canOffset.value) {
    emit('close')
    return
  }
  if (!canOffset.value) {
    emit('close')
    return
  }
  if (amountNum.value <= 0) {
    toast.error('请输入抵扣金额')
    return
  }
  if (overLimit.value) {
    toast.error(`最多可抵 ${formatAmount(props.bill.unpaid_amount, ui.amountPrecision)}`)
    return
  }
  saving.value = true
  try {
    await api.post(`/ledger/accounts/${props.accountId}/card-bills/${props.bill.id}/offsets`, {
      amount: form.amount,
      offset_type: form.offset_type,
      points: form.offset_type === 'points' && form.points ? Number(form.points) : null,
      note: form.note || null,
    })
    toast.success('抵扣已入账：账单未还与欠款同步减少')
    emit('done')
    emit('close')
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function revoke(row: OffsetRow) {
  try {
    await api.delete(`/ledger/card-bill-offsets/${row.id}`)
    toast.success('已撤销抵扣')
    revokeId.value = ''
    await loadOffsets()
    emit('done')
  } catch (err) {
    toast.error(errorMessage(err))
  }
}
</script>

<template>
  <AppModal
    :open="open" title="账单抵扣" :mask-closable="false"
    :confirm-text="confirmText" :confirm-loading="saving"
    @close="emit('close')" @confirm="submit"
  >
    <div v-if="bill" class="form">
      <div class="bill-box">
        <div class="row">
          <span class="k">本期账单</span>
          <strong>{{ formatAmount(bill.total_amount, ui.amountPrecision) }}</strong>
        </div>
        <div class="row muted">
          <span>已还 {{ formatAmount(bill.paid_amount, ui.amountPrecision) }}</span>
          <span v-if="Number(bill.offset_amount) > 0">已抵 {{ formatAmount(bill.offset_amount, ui.amountPrecision) }}</span>
          <span>未还 {{ formatAmount(bill.unpaid_amount, ui.amountPrecision) }}</span>
        </div>
        <div v-if="bill.due_date" class="row muted">
          <span>到期日</span><span>{{ bill.due_date.slice(0, 10) }}</span>
        </div>
      </div>

      <p v-if="!canOffset" class="settled-tip">
        本账单已结清，无需抵扣。<template v-if="offsets.length">下面保留已记录的抵扣，可撤销。</template>
      </p>

      <template v-if="canOffset">
      <AppField label="抵扣方式">
        <div class="seg">
          <button
            type="button" :class="{ on: form.offset_type === 'credit' }"
            @click="form.offset_type = 'credit'"
          >
            抵扣金
          </button>
          <button
            type="button" :class="{ on: form.offset_type === 'points' }"
            @click="form.offset_type = 'points'"
          >
            积分抵现
          </button>
        </div>
      </AppField>

      <AppField
        label="抵扣金额" required
        :error="overLimit ? `最多可抵 ${formatAmount(bill.unpaid_amount, ui.amountPrecision)}` : ''"
        hint="抵扣不产生现金流水，只减少欠款，不计入收支"
      >
        <AppInput v-model="form.amount" type="number" placeholder="0.00" />
      </AppField>

      <AppField
        v-if="form.offset_type === 'points'"
        label="使用积分" hint="选填，仅用于记录；不自动换算，金额以银行实际抵扣为准"
      >
        <AppInput v-model="form.points" type="number" placeholder="例如：599" />
      </AppField>

      <p v-if="points !== null" class="points-tip">
        邮件同步的积分余额 <strong>{{ points }}</strong>
        <span v-if="pointsDate">（{{ pointsDate }}）</span>
      </p>

      <AppField label="备注">
        <AppInput v-model="form.note" placeholder="例如：积分兑换刷卡金 / 活动抵扣" />
      </AppField>

      <div class="result" :class="{ ok: amountNum > 0 && afterUnpaid <= 0 && !overLimit }">
        <template v-if="amountNum > 0 && !overLimit">
          <span v-if="afterUnpaid <= 0">抵扣后账单结清</span>
          <span v-else>抵扣后账单未还 {{ formatAmount(String(afterUnpaid), ui.amountPrecision) }}</span>
          <span v-if="debtNow > 0">，信用卡欠款变为 {{ formatAmount(String(afterDebt), ui.amountPrecision) }}</span>
        </template>
        <template v-else>填写金额后显示抵扣结果</template>
      </div>
      </template>

      <div v-if="offsets.length" class="history">
        <p class="h-title">本账单抵扣记录</p>
        <div v-for="row in offsets" :key="row.id" class="h-row">
          <div class="h-main">
            <strong>{{ row.offset_type_label }} {{ formatAmount(row.amount, ui.amountPrecision) }}</strong>
            <small>
              {{ row.created_at?.slice(0, 16).replace('T', ' ') || '—' }}
              <template v-if="row.points"> · {{ row.points }} 积分</template>
              <template v-if="row.note"> · {{ row.note }}</template>
            </small>
          </div>
          <span v-if="row.status !== 'confirmed'" class="h-done">已撤销</span>
          <template v-else>
            <button v-if="revokeId !== row.id" class="h-act" type="button" @click="revokeId = row.id">
              撤销
            </button>
            <template v-else>
              <button class="h-act danger" type="button" @click="revoke(row)">确认撤销</button>
              <button class="h-act" type="button" @click="revokeId = ''">取消</button>
            </template>
          </template>
        </div>
      </div>
    </div>
  </AppModal>
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: var(--space-3); }
.bill-box {
  padding: 10px 12px;
  border-radius: var(--glass-radius-control);
  background: var(--glass-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bill-box .row { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; font-size: 12.5px; }
.bill-box .row.muted { color: var(--ink-muted); font-size: 11.5px; justify-content: flex-start; gap: 12px; }
.bill-box strong { font-size: 16px; color: var(--ink-strong); }

.seg { display: flex; gap: 6px; }
.seg button {
  flex: 1;
  padding: 8px 0;
  border: 1px solid var(--border);
  border-radius: var(--glass-radius-control);
  background: transparent;
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
}
.seg button.on {
  border-color: transparent;
  background: linear-gradient(135deg, rgba(53, 109, 255, 0.16), rgba(124, 92, 252, 0.14));
  color: var(--ink-strong);
  font-weight: 600;
}

.settled-tip {
  margin: 0;
  padding: 9px 12px;
  border-radius: var(--glass-radius-control);
  background: rgba(25, 183, 122, 0.1);
  color: var(--ink);
  font-size: 12.5px;
  line-height: 1.6;
}
.settled-tip {
  margin: 0;
  padding: 9px 12px;
  border-radius: var(--glass-radius-control);
  background: rgba(25, 183, 122, 0.1);
  color: var(--ink);
  font-size: 12.5px;
  line-height: 1.6;
}
.points-tip { margin: 0; font-size: 12px; color: var(--ink-muted); }
.points-tip strong { color: var(--ink-strong); }

.result {
  padding: 9px 12px;
  border-radius: var(--glass-radius-control);
  background: var(--glass-1);
  font-size: 12.5px;
  color: var(--ink-muted);
}
.result.ok { background: rgba(25, 183, 122, 0.12); color: var(--success); font-weight: 600; }

.history { border-top: 1px dashed var(--border); padding-top: var(--space-3); }
.h-title { margin: 0 0 6px; font-size: 12px; color: var(--ink-muted); }
.h-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 6px 0;
}
.h-main { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.h-main strong { font-size: 13px; color: var(--ink-strong); }
.h-main small { font-size: 11px; color: var(--ink-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.h-act {
  flex: none; padding: 3px 8px; border: 1px solid var(--border);
  border-radius: 6px; background: transparent; color: var(--ink-muted);
  font-size: 11.5px; cursor: pointer;
}
.h-act:hover { border-color: var(--primary); color: var(--primary); }
.h-act.danger { border-color: var(--danger); color: var(--danger); }
.h-done { font-size: 11.5px; color: var(--ink-muted); }
</style>
