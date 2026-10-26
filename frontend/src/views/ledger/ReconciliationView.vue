<script setup lang="ts">
/**
 * 账户对账 /ledger/reconciliation（06 §5、18 §4 顺序 7）。
 * 选账户与截止日 → 系统余额、未匹配流水；录入实际余额与原因 → 差额生成调整事件。
 * 对账不直接覆盖系统余额。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, errorFields, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Account { id: string; name: string; currency: string; status: string }
interface UnmatchedEvent { id: string; event_no: string; event_type_label: string; title: string; amount: string; currency: string; occurred_at: string | null; status: string }
interface Preview { account_id: string; account_name: string; currency: string; opening_balance: string; as_of: string | null; system_balance: string; last_reconciled_at: string | null; unmatched_events: UnmatchedEvent[]; caliber: string }
interface ReconcileResult { adjusted: boolean; diff: string; actual_balance: string; system_balance_before: string; adjustment_event: { event_type: string; event_no: string; source: string; source_label: string } | null; last_reconciled_at: string | null }

const auth = useAuthStore()
const toast = useToastStore()

const accounts = ref<Account[]>([])
const accountId = ref('')
const asOf = ref('')
const loading = ref(false)
const error = ref('')
const preview = ref<Preview | null>(null)
// 请求序号：快速切换账户/截止日时，慢响应不能覆盖新账户的预览（B-4）。
let loadSeq = 0

const actualBalance = ref('')
const reason = ref('')
const submitting = ref(false)
const fieldErrors = ref<Record<string, string>>({})
const result = ref<ReconcileResult | null>(null)

const canConfirm = computed(() => auth.can('confirm'))

const accountOptions = computed(() => [
  { value: '', label: '请选择账户' },
  ...accounts.value.filter((a) => a.status === 'active')
    .map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` })),
])

async function loadAccounts() {
  const res = await api.get<{ items: Account[] }>('/ledger/accounts')
  accounts.value = res.items
}

async function loadPreview() {
  if (!accountId.value) return
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  result.value = null
  try {
    const data = await api.get<Preview>('/ledger/reconciliation', {
      account_id: accountId.value, as_of: asOf.value || undefined,
    })
    // 期间账户/日期已切换：丢弃过期响应，避免用旧账户余额覆盖新账户（B-4）
    if (seq !== loadSeq) return
    preview.value = data
    actualBalance.value = data.system_balance
  } catch (err) {
    if (seq !== loadSeq) return
    error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function submit() {
  // 提交前校验：当前预览必须是所选账户的，避免「招行余额核对工行」（B-4）
  if (!preview.value || preview.value.account_id !== accountId.value) {
    toast.error('账户信息已变化，请重新加载后再对账')
    return
  }
  submitting.value = true
  fieldErrors.value = {}
  try {
    result.value = await api.post<ReconcileResult>('/ledger/reconciliation', {
      account_id: accountId.value,
      as_of: asOf.value || null,
      actual_balance: actualBalance.value,
      reason: reason.value || null,
    })
    toast.success(result.value.adjusted ? '对账完成，已生成调整事件' : '已标记为已核对')
    await loadPreview()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    submitting.value = false
  }
}

const route = useRoute()

onMounted(async () => {
  await loadAccounts()
  // 从账户页「去对账」进来时直接带上账户，省去二次选择
  const preset = route.query.account_id
  if (typeof preset === 'string' && preset && accounts.value.some((a) => a.id === preset)) {
    accountId.value = preset
    await loadPreview()
  }
})

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  return String(s).slice(0, 10)
}
</script>

<template>
  <div class="recon">
    <PageHeader title="对账" subtitle="系统余额 = 期初余额 + 截至截止日的已确认资金变动；对账不直接覆盖系统余额，差额通过调整事件对齐。" />

    <AppCard title="选择账户与截止日" level="1">
      <div class="pick">
        <AppSelect v-model="accountId" :options="accountOptions" class="acc" @update:model-value="loadPreview" />
        <AppDatePicker v-model="asOf" class="date" placeholder="截止日（留空为今天）" @change="loadPreview" />
        <AppButton variant="secondary" @click="loadPreview">加载</AppButton>
      </div>
    </AppCard>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="loadPreview" />

    <template v-else-if="preview">
      <AppCard title="余额与流水">
        <ul class="kv">
          <li><span class="k">账户</span><span class="v strong">{{ preview.account_name }}</span></li>
          <li><span class="k">期初余额</span><span class="v">{{ preview.opening_balance }} {{ preview.currency }}</span></li>
          <li><span class="k">系统余额（截止 {{ fmtDate(preview.as_of) }}）</span>
            <span class="v strong"><MoneyText :value="preview.system_balance" :currency="preview.currency" show-currency :animated="false" /></span></li>
          <li><span class="k">上次对账</span><span class="v">{{ fmtDate(preview.last_reconciled_at) }}</span></li>
        </ul>
        <p class="hint">{{ preview.caliber }}</p>

        <h4 class="sec">未匹配流水（{{ preview.unmatched_events.length }} 条）</h4>
        <StateBlock v-if="!preview.unmatched_events.length" state="empty" title="暂无流水" description="该账户截至截止日没有已确认流水。" />
        <ul v-else class="list">
          <li v-for="e in preview.unmatched_events" :key="e.id">
            <div class="l-main">
              <strong>{{ e.title }}</strong>
              <small>{{ e.event_no }} · {{ e.event_type_label }} · {{ fmtDate(e.occurred_at) }}</small>
            </div>
            <span class="amt">{{ e.amount }} {{ e.currency }}</span>
          </li>
        </ul>
      </AppCard>

      <AppCard title="录入实际余额并核对">
        <div class="form">
          <AppField label="人工实际余额" required :error="fieldErrors.actual_balance">
            <AppInput v-model="actualBalance" type="number" placeholder="银行/现金实际余额" />
          </AppField>
          <AppField label="差额说明" :error="fieldErrors.reason" hint="系统余额与人工实际余额不一致时必须填写">
            <AppInput v-model="reason" placeholder="例如：银行到账漏记 / 手续费补记" />
          </AppField>
          <AppButton v-if="canConfirm" variant="primary" :loading="submitting" @click="submit">提交对账</AppButton>
          <StateBlock v-else state="forbidden" description="当前角色没有「确认」权限，无法提交对账。" />
        </div>
      </AppCard>

      <AppCard v-if="result" title="对账结果" level="2" aurora>
        <ul class="kv">
          <li><span class="k">系统余额（调整前）</span><span class="v">{{ result.system_balance_before }} {{ preview.currency }}</span></li>
          <li><span class="k">人工实际余额</span><span class="v">{{ result.actual_balance }} {{ preview.currency }}</span></li>
          <li><span class="k">差额</span>
            <span class="v strong" :class="{ warn: result.diff !== '0.0000' }">{{ result.diff }}</span></li>
          <li><span class="k">是否生成调整事件</span>
            <span class="v"><StatusBadge :label="result.adjusted ? '已调整' : '无差额'" :tone="result.adjusted ? 'warning' : 'success'" /></span></li>
        </ul>
        <p v-if="result.adjusted && result.adjustment_event" class="hint">
          已生成{{ result.adjustment_event.source_label }}事件 {{ result.adjustment_event.event_no }}，确认后系统余额已对齐。
        </p>
      </AppCard>
    </template>
  </div>
</template>

<style scoped>
.recon { display: flex; flex-direction: column; gap: var(--space-4); }
.pick { display: flex; gap: var(--space-2); flex-wrap: wrap; }
.acc { width: 260px; }
.date { width: 240px; }
@media (max-width: 700px) {
  .acc, .date { width: 100%; }
}
.kv { display: flex; flex-direction: column; }
.kv li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 9px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.kv .k { color: var(--ink-muted); }
.kv .v { color: var(--ink-strong); }
.kv .v.warn { color: var(--warning); }
.hint { font-size: 12px; color: var(--ink-muted); margin-top: var(--space-2); }
.sec { margin: var(--space-4) 0 var(--space-2); font-size: 13.5px; color: var(--ink-strong); }
.list { display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.l-main { display: flex; flex-direction: column; gap: 2px; }
.l-main strong { font-size: 13.5px; color: var(--ink-strong); }
.l-main small { font-size: 12px; color: var(--ink-muted); }
.amt { font-size: 13px; color: var(--ink-strong); font-variant-numeric: tabular-nums; }
.form { display: flex; flex-direction: column; gap: var(--space-4); max-width: 480px; }
</style>
