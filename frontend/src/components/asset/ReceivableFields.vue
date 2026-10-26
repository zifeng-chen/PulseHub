<script setup lang="ts">
/**
 * 借出款字段（应收资产）。
 * 借出不是支出：钱从账户出去、形成一笔应收，净资产不变；
 * 因此这里只收集「借给谁、借多少、怎么还、手续费谁承担」，不再问当前价值/折旧等字段。
 */
import { computed } from 'vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'

interface AccountLite { id: string; name: string; currency: string }

const props = defineProps<{
  receivable: Record<string, any>
  accounts: AccountLite[]
  errors: Record<string, string>
}>()

const lendTypeOptions = [
  { value: 'cash', label: '现金出借' },
  { value: 'bank_card', label: '银行卡出借' },
  { value: 'credit_card', label: '信用卡出借' },
  { value: 'e_wallet', label: '电子钱包出借' },
  { value: 'other', label: '其他出借' },
]
const repaymentOptions = [
  { value: 'lump_sum', label: '到期一次还本' },
  { value: 'installments', label: '分期还款' },
  { value: 'monthly_interest', label: '按月付息、到期还本' },
  { value: 'other', label: '其他方式' },
]
const lentModeOptions = [
  { value: 'account', label: '从账户出借', hint: '钱从下面选定的账户出，账户余额会减少' },
  { value: 'existing', label: '补录既有借出', hint: '之前就借出去了，只登记应收，不动现在的账户' },
]
const feeBearerOptions = [
  { value: 'borrower', label: '借款人承担' },
  { value: 'self', label: '自己承担（可另记手续费支出）' },
]

const accountOptions = computed(() => [
  { value: '', label: '请选择出借资金来源账户' },
  ...props.accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` })),
])

/** 信用卡 / 电子钱包出借才涉及手续费 */
const showFee = computed(() => ['credit_card', 'e_wallet'].includes(props.receivable.lend_type))
/** 补录既有借出：不产生资金流出，来源账户非必填且默认隐藏 */
const isExisting = computed(() => props.receivable.lent_mode === 'existing')
/** 是否约定利息：决定是否显示利率与回款时的利息拆分 */
const interestOn = computed(() => Boolean(props.receivable.interest_enabled))

/**
 * 切换登记方式时联动「计入净资产」——两种情况的算法不一样：
 * - 从账户出借：账户少了钱、应收多了钱，净资产必须不变 → 必须计入，否则净资产凭空少一笔；
 * - 补录既有借出：这笔钱早就出去了，账上本来就没有它 → 不能再计入，否则净资产虚增。
 * 之后用户仍可手动改这个开关。
 */
function onModeChange(mode: string) {
  props.receivable.include_in_net_worth = mode === 'account'
}
</script>

<template>
  <AppField label="借款人" required :error="errors.borrower_name">
    <AppInput v-model="receivable.borrower_name" placeholder="例如：张三" />
  </AppField>
  <AppField label="借款人身份证号" required :error="errors.borrower_id_no"
            hint="必填。列表与详情按「前 6 后 6」显示（如 310227******071419）；编辑时保持掩码即表示不修改">
    <AppInput v-model="receivable.borrower_id_no" placeholder="18 位身份证号（末位可为 X）" />
  </AppField>
  <!-- 之前借出去的钱不该再从现在的账户扣一次 -->
  <AppField label="登记方式" :error="errors.lent_mode"
            hint="之前就借出去的，选「补录既有借出」：只登记应收，不动当前账户余额">
    <div class="mode-row">
      <label v-for="m in lentModeOptions" :key="m.value" class="mode-item"
             :class="{ on: (receivable.lent_mode || 'account') === m.value }">
        <input v-model="receivable.lent_mode" type="radio" :value="m.value"
               @change="onModeChange(m.value)" />
        <span>
          <strong>{{ m.label }}</strong>
          <small>{{ m.hint }}</small>
        </span>
      </label>
    </div>
  </AppField>
  <AppField label="出借方式" :error="errors.lend_type" hint="决定资金来源与是否产生手续费">
    <AppSelect v-model="receivable.lend_type" :options="lendTypeOptions" />
  </AppField>
  <AppField label="借款金额" required :error="errors.principal" hint="未收回的本金；后续回款会逐笔扣减">
    <AppInput v-model="receivable.principal" type="number" placeholder="例如：50000" />
  </AppField>
  <AppField label="借款日期" :error="errors.lent_at">
    <AppDatePicker v-model="receivable.lent_at" placeholder="选择借款日期" />
  </AppField>
  <AppField label="还款方式" :error="errors.repayment_method">
    <AppSelect v-model="receivable.repayment_method" :options="repaymentOptions" />
  </AppField>
  <AppField label="约定还款日" :error="errors.due_date" hint="用于还款提醒与逾期判断">
    <AppDatePicker v-model="receivable.due_date" placeholder="选择约定还款日" />
  </AppField>

  <template v-if="showFee">
    <AppField label="手续费由谁承担" :error="errors.fee_bearer"
              hint="信用卡取现 / 平台提现会产生手续费，先约定清楚">
      <AppSelect v-model="receivable.fee_bearer" :options="feeBearerOptions" />
    </AppField>
    <AppField label="手续费金额" :error="errors.fee_amount"
              hint="选填；借款人承担时不影响你的收支，自己承担可再记一笔手续费支出">
      <AppInput v-model="receivable.fee_amount" type="number" placeholder="例如：150" />
    </AppField>
  </template>

  <AppField v-if="!isExisting" label="出借资金来源账户"
            :required="!isExisting" :error="errors.source_account_id"
            hint="从哪个账户出钱（信用卡出借会记为刷卡消费，增加待还）">
    <AppSelect v-model="receivable.source_account_id" :options="accountOptions" />
  </AppField>
  <p v-else class="mode-note">
    补录既有借出不会生成资金流水，也不会改变账户余额；这笔钱按「应收资产」计入净资产。
  </p>

  <!-- 利息约定 -->
  <AppField label="是否约定利息" hint="约定计息的，回款时可把利息单独算出来，计入「投资收益 · 利息收入」">
    <AppSwitch v-model="receivable.interest_enabled" label="约定利息" />
  </AppField>
  <AppField v-if="interestOn" label="年化利率（%）" :required="interestOn"
            :error="errors.interest_rate" hint="只记录约定；实际收息以回款时填写的利息金额为准">
    <AppInput v-model="receivable.interest_rate" type="number" placeholder="例如：3.5" />
  </AppField>

  <AppField label="计入净资产" :hint="isExisting
    ? '补录的借出：钱早已离开账户，账上本来就没有它，默认不计入；等对方回款、钱真正回到账户时才体现为净资产增加'
    : '从账户出借：账户少了钱、应收多了钱，净资产必须不变，所以默认计入；取消会导致净资产凭空减少'">
    <AppSwitch v-model="receivable.include_in_net_worth" label="计入净资产" />
  </AppField>
  <AppField label="备注">
    <AppInput v-model="receivable.note" placeholder="例如：借款用途、约定利息、电子签合同编号" />
  </AppField>
</template>

<style scoped>
.mode-row { display: flex; flex-direction: column; gap: 8px; }
.mode-item {
  display: flex; gap: 8px; align-items: flex-start; cursor: pointer;
  padding: 9px 11px; border: 1px solid var(--border); border-radius: 10px;
  transition: border-color 0.16s ease, background 0.16s ease;
}
.mode-item.on { border-color: var(--primary); background: var(--glass-1); }
.mode-item input { margin-top: 2px; accent-color: var(--primary); }
.mode-item strong { display: block; font-size: 13px; color: var(--ink-strong); }
.mode-item small { display: block; margin-top: 2px; font-size: 11.5px; color: var(--ink-muted); line-height: 1.45; }
.mode-note {
  margin: -4px 0 4px; padding: 8px 10px; border-radius: 8px;
  background: var(--glass-1); border: 1px dashed var(--border);
  font-size: 12px; color: var(--ink-muted); line-height: 1.5;
}
</style>
