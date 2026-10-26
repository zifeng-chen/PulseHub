<script setup lang="ts">
/** 财务口径 /settings/finance（08 §4）：币种、统计周期、资产购置与投资估值口径、金额精度。 */
import { onMounted, ref } from 'vue'
import { Save } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Finance {
  base_currency: string
  timezone: string
  default_period: string
  asset_purchase_separate: boolean
  investment_valuation_in_net_worth: boolean
  bill_display_mode: string
  default_date_range: string
  amount_precision: number
  options: { periods: { value: string; label: string }[]; bill_display_modes: { value: string; label: string }[] }
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const saving = ref(false)
const data = ref<Finance | null>(null)
const form = ref({
  base_currency: 'CNY',
  default_period: 'current_month',
  asset_purchase_separate: true,
  investment_valuation_in_net_worth: true,
  bill_display_mode: 'day_of_month',
  default_date_range: 'last_6_months',
  amount_precision: 2,
})

const canConfigure = () => auth.can('configure')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Finance>('/settings/finance')
    form.value = {
      base_currency: data.value.base_currency,
      default_period: data.value.default_period,
      asset_purchase_separate: data.value.asset_purchase_separate,
      investment_valuation_in_net_worth: data.value.investment_valuation_in_net_worth,
      bill_display_mode: data.value.bill_display_mode,
      default_date_range: data.value.default_date_range,
      amount_precision: data.value.amount_precision,
    }
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await api.put('/settings/finance', {
      default_period: form.value.default_period,
      asset_purchase_separate: form.value.asset_purchase_separate,
      investment_valuation_in_net_worth: form.value.investment_valuation_in_net_worth,
      bill_display_mode: form.value.bill_display_mode,
      default_date_range: form.value.default_date_range,
      amount_precision: Number(form.value.amount_precision),
    })
    ui.setAmountPrecision(form.value.amount_precision)
    toast.success('财务口径已更新，变更已写入审计')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function changeBaseCurrency(next: string) {
  if (next === form.value.base_currency) return
  const res = await ui.askConfirm({
    title: '确认修改基础币种',
    message: `基础币种将从 ${form.value.base_currency} 变更为 ${next}。该操作会记录对历史统计的影响并写入审计。`,
    detail: ['多币种数据保留原始金额、币种、汇率、日期与来源。', '缺少有效汇率时不会默认为 1，而是显示未换算。'],
    confirmText: '确认修改',
    danger: true,
    requireReason: true,
    reasonLabel: '修改原因（必填）',
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'finance.base_currency', object_type: 'household', object_id: null,
    })
    await api.put('/settings/finance', {
      base_currency: next,
      confirm_token: tokenRes.token,
      reason: res.reason,
    })
    toast.success('基础币种已修改并写入审计')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

onMounted(load)
</script>

<template>
  <div class="finance">
    <PageHeader title="财务口径" subtitle="所有配置变更都会显示影响说明并写入审计。" />

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else-if="data">
      <AppCard title="币种与时区">
        <div class="grid">
          <AppField label="基础币种" hint="修改基础币种属于高影响操作，需要二次确认与原因">
            <AppSelect
              :model-value="form.base_currency"
              :disabled="!canConfigure()"
              :options="[
                { value: 'CNY', label: '人民币 CNY' },
                { value: 'USD', label: '美元 USD' },
                { value: 'HKD', label: '港币 HKD' },
              ]"
              @update:model-value="changeBaseCurrency"
            />
          </AppField>
          <AppField label="时区">
            <AppInput :model-value="data.timezone" disabled />
          </AppField>
        </div>
        <p class="note">
          多币种底层预留：业务金额至少保留原始金额、原始币种、换算金额、基础币种、使用汇率、汇率日期、
          汇率来源与换算状态。第一版可以只启用人民币，但数据库与接口不假定永远只有一种币种。
        </p>
      </AppCard>

      <AppCard title="统计与展示口径">
        <div class="stack">
          <div class="opt-row">
            <div>
              <strong>资产购置独立于消费</strong>
              <p>开启后，资产购置形成的资金减少不会重复计入消费统计。</p>
            </div>
            <AppSwitch v-model="form.asset_purchase_separate" :disabled="!canConfigure()" />
          </div>
          <div class="opt-row">
            <div>
              <strong>投资估值纳入净资产</strong>
              <p>关闭后净资产不包含投资当前市值。这是全局开关；单个投资账户也可在编辑里排除。</p>
            </div>
            <AppSwitch v-model="form.investment_valuation_in_net_worth" :disabled="!canConfigure()" />
          </div>
          <div class="opt-row">
            <div>
              <strong>单项「纳入净资产」开关</strong>
              <p>账户、资产、负债、投资都能在各自表单里单独设置是否计入家庭净值（例如公司贷款、代持账户可排除）。</p>
            </div>
          </div>
          <div class="opt-row">
            <div><strong>默认统计周期</strong><p>脉枢台与脉诊打开时的默认期间。</p></div>
            <AppSelect v-model="form.default_period" class="w220" :disabled="!canConfigure()" :options="data.options.periods" />
          </div>
          <div class="opt-row">
            <div><strong>默认日期范围</strong><p>列表与图表的默认查询范围。</p></div>
            <AppSelect v-model="form.default_date_range" class="w220" :disabled="!canConfigure()" :options="data.options.periods" />
          </div>
          <div class="opt-row">
            <div><strong>账单日 / 还款日显示方式</strong><p>按每月固定日期或按到期前天数显示。</p></div>
            <AppSelect v-model="form.bill_display_mode" class="w220" :disabled="!canConfigure()" :options="data.options.bill_display_modes" />
          </div>
          <div class="opt-row">
            <div><strong>金额精度展示</strong><p>底层金额固定 DECIMAL(20,4)，此处控制全平台金额显示的小数位（默认 2 位，0–4）。</p></div>
            <AppInput v-model="form.amount_precision" type="number" min="0" max="4" class="w220" :disabled="!canConfigure()" />
          </div>
        </div>
      </AppCard>

      <AppCard title="统计口径说明" level="1">
        <ul class="caliber">
          <li>净资产 = 可计入资产当前价值 + 投资总额（持仓市值 + 账户现金） − 负债未偿余额。</li>
          <li>收入只包含已确认收入、投资收入、租金实收；借款、押金、内部转入不算收入。</li>
          <li>支出只包含已确认消费、利息、手续费等；信用卡还款和贷款本金不重复计入支出。</li>
          <li>内部转账只改变账户余额，不改变收入和支出。</li>
          <li>应收、计划、账单和草稿不等于实际发生。</li>
        </ul>
      </AppCard>

      <div v-if="canConfigure()" class="foot">
        <AppButton variant="primary" :loading="saving" @click="save"><Save :size="15" /> 保存财务口径</AppButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.finance { display: flex; flex-direction: column; gap: var(--space-4); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: var(--space-4); }
.stack { display: flex; flex-direction: column; gap: var(--space-2); }
.opt-row {
  display: flex; align-items: center; justify-content: space-between; gap: var(--space-4);
  padding: var(--space-3) var(--space-4); border: 1px solid var(--border);
  border-radius: var(--glass-radius-card); background: var(--glass-1);
}
.opt-row strong { font-size: 13.5px; color: var(--ink-strong); }
.opt-row p { font-size: 12.5px; color: var(--ink-muted); }
.w220 { width: 220px; }
.note, .caliber { font-size: 12.5px; color: var(--ink-muted); line-height: 1.9; }
.note { margin-top: var(--space-3); }
.caliber { display: flex; flex-direction: column; gap: 4px; }
.foot { display: flex; justify-content: flex-end; }
@media (max-width: 700px) { .opt-row { flex-direction: column; align-items: flex-start; } .w220 { width: 100%; } }
</style>
