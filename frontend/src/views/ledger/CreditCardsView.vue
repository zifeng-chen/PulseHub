<script setup lang="ts">
/**
 * 信用卡页面 /ledger/credit-cards（06 §7，负债内部页）。
 * 卡片显示额度、已用额度、可用额度、账单日、还款日、本期账单、最低还款、已还金额；
 * 账单详情区分消费明细、账单形成、还款和费用；AI 明确「还款不重复计消费」。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowLeft, CreditCard, Layers, Plus, RefreshCw, Sparkles, TrendingDown, Wallet,
} from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { zh } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppDatePicker from '@/components/ui/AppDatePicker.vue'
import AppDateRangePicker from '@/components/ui/AppDateRangePicker.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppModal from '@/components/ui/AppModal.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { fmtDate, type AccountLite } from './asset-shared'
import { accountLabel } from '@/utils/account'

interface CardBill {
  id: string
  period_start: string | null
  period_end: string | null
  bill_date: string | null
  due_date: string | null
  total_amount: string
  minimum_payment: string | null
  paid_amount: string
  remaining_amount: string
  currency: string
  status: string
  status_label: string
  note: string | null
}

interface CardRow {
  id: string
  name: string
  liability_type_label: string
  currency: string
  outstanding: string
  status_label: string
  settled: boolean
  card: {
    id: string
    issuer: string | null
    card_no: string | null
    credit_limit: string
    bill_day: number | null
    due_day: number | null
    linked_account_id: string | null
    used_amount: string
    available_amount: string
  }
  current_bill: CardBill | null
  bills: CardBill[]
  bill_count: number
  version: number
  note: string | null
}

interface CardDetail extends CardRow {
  activity: {
    id: string; event_no: string; event_type_label: string; title: string
    amount: string; currency: string; status_label: string
    occurred_at: string | null; direction: string | null
  }[]
  repayments: {
    id: string; amount: string; principal: string; interest: string; fee: string
    occurred_at: string | null; reason: string | null
  }[]
  ai: { allowed: string[]; forbidden: string[] }
}

const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const rows = ref<CardRow[]>([])
const summary = ref<Record<string, any> | null>(null)
const accounts = ref<AccountLite[]>([])

const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const detailOpen = ref(false)
const detail = ref<CardDetail | null>(null)
const detailLoading = ref(false)
const activeTab = ref('bills')

const expenseOpen = ref(false)
const expenseSaving = ref(false)
const expenseErrors = ref<Record<string, string>>({})
const expenseForm = reactive({ amount: '', title: '', occurred_at: '', category_id: '', note: '' })
const categories = ref<{ id: string; name: string }[]>([])

const billOpen = ref(false)
const billSaving = ref(false)
const billErrors = ref<Record<string, string>>({})
const billForm = reactive({
  total_amount: '', minimum_payment: '', bill_date: '', due_date: '',
  period_start: '', period_end: '', note: '',
})

const repayOpen = ref(false)
const repaySaving = ref(false)
const repayErrors = ref<Record<string, string>>({})
const repayForm = reactive({ amount: '', account_id: '', bill_id: '', occurred_at: '', note: '' })

const form = reactive({
  id: '', version: 1, name: '', creditor: '', currency: 'CNY',
  credit_limit: '', bill_day: '', due_day: '', card_no: '', linked_account_id: '', note: '',
})

const canCreate = computed(() => auth.can('create_draft'))
const canConfirm = computed(() => auth.can('confirm'))
const currencyOptions = [
  { value: 'CNY', label: '人民币 CNY' },
  { value: 'USD', label: '美元 USD' },
  { value: 'HKD', label: '港币 HKD' },
]

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const [list, acc] = await Promise.all([
      api.get<{ items: CardRow[]; summary: Record<string, any> }>('/ledger/credit-cards'),
      api.get<{ items: AccountLite[] }>('/ledger/accounts'),
    ])
    if (seq !== loadSeq) return
    rows.value = list.items
    summary.value = list.summary
    accounts.value = (acc.items || []).filter((a) => a.status === 'active')
  } catch (err) {
    if (seq === loadSeq) error.value = errorMessage(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function openCreate() {
  fieldErrors.value = {}
  Object.assign(form, {
    id: '', version: 1, name: '', creditor: '', currency: 'CNY',
    credit_limit: '', bill_day: '', due_day: '', card_no: '', linked_account_id: '', note: '',
  })
  editorMode.value = 'create'
  editorOpen.value = true
}

function openEdit(row: CardRow) {
  fieldErrors.value = {}
  Object.assign(form, {
    id: row.id, version: row.version,
    name: row.name, creditor: row.card.issuer || '', currency: row.currency,
    credit_limit: row.card.credit_limit, bill_day: row.card.bill_day ?? '',
    due_day: row.card.due_day ?? '', card_no: row.card.card_no || '',
    linked_account_id: '', note: '',
  })
  editorMode.value = 'edit'
  editorOpen.value = true
}

async function save() {
  saving.value = true
  fieldErrors.value = {}
  try {
    if (editorMode.value === 'create') {
      await api.post('/ledger/liabilities', {
        name: form.name, liability_type: 'credit_card', creditor: form.creditor || null,
        currency: form.currency, initial_amount: '0',
        extension: {
          credit_limit: form.credit_limit || '0',
          bill_day: form.bill_day === '' ? null : form.bill_day,
          due_day: form.due_day === '' ? null : form.due_day,
          card_no: form.card_no || null,
          issuer: form.creditor || null,
          linked_account_id: form.linked_account_id || null,
        },
      })
      toast.success('信用卡已创建')
    } else {
      await api.put(`/ledger/liabilities/${form.id}`, {
        version: form.version, name: form.name, creditor: form.creditor || null,
        extension: {
          credit_limit: form.credit_limit || '0',
          bill_day: form.bill_day === '' ? null : form.bill_day,
          due_day: form.due_day === '' ? null : form.due_day,
          card_no: form.card_no || null,
          issuer: form.creditor || null,
          linked_account_id: form.linked_account_id || null,
        },
      })
      toast.success('信用卡已更新')
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

async function openDetail(row: CardRow) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  activeTab.value = 'bills'
  try {
    detail.value = await api.get<CardDetail>(`/ledger/credit-cards/${row.card.id}`)
    const cats = await api.get<{ items: { id: string; name: string }[] }>('/ledger/categories', { domain: 'expense' })
    categories.value = cats.items || []
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    detailLoading.value = false
  }
}

async function reloadDetail() {
  if (!detail.value) return
  detail.value = await api.get<CardDetail>(`/ledger/credit-cards/${detail.value.card.id}`)
  await load()
}

function openExpense() {
  expenseErrors.value = {}
  Object.assign(expenseForm, { amount: '', title: '', occurred_at: '', category_id: '', note: '' })
  expenseOpen.value = true
}

async function submitExpense() {
  if (!detail.value) return
  expenseSaving.value = true
  expenseErrors.value = {}
  try {
    await api.post(`/ledger/credit-cards/${detail.value.card.id}/expenses`, {
      amount: expenseForm.amount, title: expenseForm.title || '信用卡消费',
      occurred_at: expenseForm.occurred_at || null,
      category_id: expenseForm.category_id || null, note: expenseForm.note || null,
    })
    toast.success('消费已记录：计入支出，同时增加已用额度')
    expenseOpen.value = false
    await reloadDetail()
  } catch (err) {
    expenseErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    expenseSaving.value = false
  }
}

function openBill() {
  billErrors.value = {}
  Object.assign(billForm, {
    total_amount: detail.value?.card.used_amount || '', minimum_payment: '',
    bill_date: '', due_date: '', period_start: '', period_end: '', note: '',
  })
  billOpen.value = true
}

async function submitBill() {
  if (!detail.value) return
  billSaving.value = true
  billErrors.value = {}
  try {
    await api.post(`/ledger/credit-cards/${detail.value.card.id}/bills`, {
      total_amount: billForm.total_amount, minimum_payment: billForm.minimum_payment || null,
      bill_date: billForm.bill_date || null, due_date: billForm.due_date || null,
      period_start: billForm.period_start || null, period_end: billForm.period_end || null,
      note: billForm.note || null,
    })
    toast.success('账单已生成')
    billOpen.value = false
    await reloadDetail()
  } catch (err) {
    billErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    billSaving.value = false
  }
}

function openRepay(bill?: CardBill) {
  repayErrors.value = {}
  Object.assign(repayForm, {
    amount: bill ? bill.remaining_amount : (detail.value?.card.used_amount || ''),
    account_id: detail.value?.card.linked_account_id || '',
    bill_id: bill ? bill.id : '', occurred_at: '', note: '',
  })
  repayOpen.value = true
}

async function submitRepay() {
  if (!detail.value) return
  repaySaving.value = true
  repayErrors.value = {}
  try {
    await api.post(`/ledger/liabilities/${detail.value.id}/repayments`, {
      amount: repayForm.amount, account_id: repayForm.account_id,
      bill_id: repayForm.bill_id || null,
      occurred_at: repayForm.occurred_at || null, note: repayForm.note || null,
    })
    toast.success('还款已登记：不重复计入消费')
    repayOpen.value = false
    await reloadDetail()
  } catch (err) {
    repayErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    repaySaving.value = false
  }
}

function billTone(status: string) {
  if (status === 'paid') return 'success' as const
  if (status === 'overdue') return 'danger' as const
  if (status === 'partial') return 'warning' as const
  return 'neutral' as const
}

function usagePercent(row: CardRow) {
  const limit = Number(row.card.credit_limit || 0)
  const used = Number(row.card.used_amount || 0)
  if (limit <= 0) return 0
  return Math.max(0, Math.min(100, (used / limit) * 100))
}

onMounted(load)
</script>

<template>
  <div class="cards">
    <PageHeader
      title="信用卡"
      subtitle="已用额度 = 未还本金；可用额度 = 额度 − 已用额度；信用卡还款不重复计消费。"
      :breadcrumb="['家庭台账', '负债', '信用卡']"
    >
      <AppButton variant="secondary" @click="router.push('/ledger/liabilities')">
        <ArrowLeft :size="15" /> 返回负债
      </AppButton>
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiAnalysis('credit_cards', 'AI 信用卡消费与还款压力')">
        <Sparkles :size="15" /> AI 消费与还款压力
      </AppButton>
      <AppButton v-if="canCreate" variant="primary" @click="openCreate">
        <Plus :size="15" /> 新增信用卡
      </AppButton>
    </PageHeader>

    <section v-if="summary" class="stats">
      <StatCard :icon="CreditCard" tone="primary" label="总额度"
                :value="summary.total_limit" hint="所有信用卡额度之和" />
      <StatCard :icon="TrendingDown" tone="danger" label="已用额度"
                :value="summary.total_used" hint="当前待还的透支金额" />
      <StatCard :icon="Wallet" tone="success" label="可用额度"
                :value="summary.total_available" hint="还能刷多少" />
      <StatCard :icon="Layers" tone="neutral" label="卡数量"
                :value="summary.count" plain unit="张" hint="在用信用卡" />
    </section>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
    <StateBlock
      v-else-if="!rows.length" state="empty" title="还没有信用卡"
      description="新增信用卡后可记录额度、账单与消费，账单到期与还款压力一目了然。"
    >
      <template #actions>
        <AppButton v-if="canCreate" variant="primary" @click="openCreate">
          <Plus :size="15" /> 新增信用卡
        </AppButton>
      </template>
    </StateBlock>

    <section v-else class="grid">
      <AppCard v-for="row in rows" :key="row.id" class="card-item" level="3">
        <div class="ci-head">
          <div>
            <strong>{{ row.name }}</strong>
            <small>{{ row.card.issuer || '未填写发卡机构' }} · {{ row.card.card_no || '卡号未填写' }}</small>
          </div>
          <StatusBadge :label="row.status_label" :tone="row.settled ? 'success' : 'warning'" />
        </div>
        <div class="progress"><span :style="{ width: `${usagePercent(row)}%` }" /></div>
        <ul class="ci-kv">
          <li><span class="k">额度</span><span class="v">{{ row.card.credit_limit }}</span></li>
          <li><span class="k">已用</span><span class="v">{{ row.card.used_amount }}</span></li>
          <li><span class="k">可用</span><span class="v strong">{{ row.card.available_amount }}</span></li>
          <li><span class="k">账单日 / 还款日</span><span class="v">{{ row.card.bill_day || '—' }} / {{ row.card.due_day || '—' }}</span></li>
        </ul>
        <div v-if="row.current_bill" class="ci-bill">
          <div class="cb-main">
            <small>本期账单（{{ row.current_bill.status_label }}）</small>
            <strong>{{ row.current_bill.total_amount }} {{ row.current_bill.currency }}</strong>
            <small>最低还款 {{ row.current_bill.minimum_payment || '—' }} · 已还 {{ row.current_bill.paid_amount }} · 到期 {{ fmtDate(row.current_bill.due_date) }}</small>
          </div>
          <StatusBadge :label="row.current_bill.status_label" :tone="billTone(row.current_bill.status)" />
        </div>
        <p v-else class="ci-empty">暂无账单记录</p>
        <div class="ci-actions">
          <AppButton variant="secondary" @click="openDetail(row)">详情</AppButton>
          <AppButton v-if="auth.can('edit_draft')" variant="secondary" @click="openEdit(row)">编辑</AppButton>
        </div>
      </AppCard>
    </section>

    <!-- 新增 / 编辑 -->
    <AppDrawer
      :open="editorOpen" :title="editorMode === 'create' ? '新增信用卡' : '编辑信用卡'" :width="520"
      @close="editorOpen = false"
    >
      <div class="form">
        <AppField label="卡名称" required :error="fieldErrors.name">
          <AppInput v-model="form.name" placeholder="例如：某银行信用卡" />
        </AppField>
        <AppField label="额度" required :error="fieldErrors['extension.credit_limit']"
                  hint="卡片总额度，决定可用额度">
          <AppInput v-model="form.credit_limit" type="number" placeholder="例如：50000" />
        </AppField>
        <AppField label="发卡机构" :error="fieldErrors.creditor">
          <AppInput v-model="form.creditor" />
        </AppField>
        <AppField label="币种">
          <AppSelect v-model="form.currency" :options="currencyOptions" />
        </AppField>
        <AppField label="账单日 / 还款日">
          <div class="grid-2">
            <AppInput v-model="form.bill_day" type="number" placeholder="账单日 1-31" />
            <AppInput v-model="form.due_day" type="number" placeholder="还款日 1-31" />
          </div>
        </AppField>
        <AppField label="卡号" hint="敏感字段，默认掩码展示">
          <AppInput v-model="form.card_no" placeholder="卡号仅用于识别" />
        </AppField>
        <AppField label="默认还款账户">
          <AppSelect
            v-model="form.linked_account_id"
            :options="[{ value: '', label: '未指定' }, ...accounts.map((a) => ({ value: a.id, label: accountLabel(a, ui.amountPrecision) }))]"
          />
        </AppField>
        <AppField label="备注"><AppInput v-model="form.note" /></AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">
          {{ editorMode === 'create' ? '创建信用卡' : '保存修改' }}
        </AppButton>
      </template>
    </AppDrawer>

    <!-- 详情 -->
    <AppDrawer :open="detailOpen" :title="detail?.name || '信用卡详情'" :width="700" @close="detailOpen = false">
      <StateBlock v-if="detailLoading" state="loading" />
      <template v-else-if="detail">
        <div class="head">
          <ul class="ci-kv">
            <li><span class="k">额度</span><span class="v">{{ detail.card.credit_limit }}</span></li>
            <li><span class="k">已用额度</span><span class="v strong">{{ detail.card.used_amount }}</span></li>
            <li><span class="k">可用额度</span><span class="v strong">{{ detail.card.available_amount }}</span></li>
            <li><span class="k">账单日 / 还款日</span><span class="v">{{ detail.card.bill_day || '—' }} / {{ detail.card.due_day || '—' }}</span></li>
          </ul>
        </div>

        <div class="tabs">
          <button
            v-for="t in [
              { key: 'bills', label: `账单（${detail.bills.length}）` },
              { key: 'activity', label: `消费明细（${detail.activity.length}）` },
              { key: 'repay', label: `还款（${detail.repayments.length}）` },
              { key: 'ai', label: 'AI 分析' },
            ]"
            :key="t.key" class="tab" :class="{ 'is-active': activeTab === t.key }"
            type="button" @click="activeTab = t.key"
          >
            {{ t.label }}
          </button>
        </div>

        <div v-if="activeTab === 'bills'" class="pane">
          <div class="pane-actions">
            <AppButton v-if="canCreate" variant="secondary" @click="openBill">
              <Plus :size="15" /> 生成账单
            </AppButton>
            <AppButton v-if="canConfirm && !detail.settled" variant="secondary" @click="openRepay()">
              <TrendingDown :size="15" /> 还款
            </AppButton>
          </div>
          <StateBlock v-if="!detail.bills.length" state="empty" title="暂无账单" description="到账单日后可在此生成账单，跟踪最低还款与已还金额。" />
          <ul v-else class="list">
            <li v-for="b in detail.bills" :key="b.id">
              <div class="l-main">
                <strong>账单 {{ b.total_amount }} {{ b.currency }}</strong>
                <small>账期 {{ fmtDate(b.period_start) }} ~ {{ fmtDate(b.period_end) }} · 账单日 {{ fmtDate(b.bill_date) }} · 到期 {{ fmtDate(b.due_date) }}</small>
                <small>最低还款 {{ b.minimum_payment || '—' }} · 已还 {{ b.paid_amount }} · 剩余 {{ b.remaining_amount }}</small>
              </div>
              <div class="l-actions">
                <StatusBadge :label="b.status_label" :tone="billTone(b.status)" />
                <button v-if="canConfirm && b.status !== 'paid'" class="act" type="button" @click="openRepay(b)">还款</button>
              </div>
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'activity'" class="pane">
          <div class="pane-actions">
            <AppButton v-if="canCreate" variant="secondary" @click="openExpense">
              <Plus :size="15" /> 记一笔消费
            </AppButton>
          </div>
          <StateBlock v-if="!detail.activity.length" state="empty" title="暂无消费明细" description="记录消费后会生成支出事件，并增加已用额度。" />
          <ul v-else class="list">
            <li v-for="a in detail.activity" :key="a.id">
              <div class="l-main">
                <strong>{{ a.title }}</strong>
                <small>{{ a.event_type_label }} · {{ a.event_no }} · {{ fmtDate(a.occurred_at) }}</small>
              </div>
              <span class="l-amount">{{ a.direction === 'in' ? '+' : '−' }}{{ a.amount }} {{ a.currency }}</span>
            </li>
          </ul>
        </div>

        <div v-else-if="activeTab === 'repay'" class="pane">
          <p class="tip">信用卡还款不重复计入消费；如产生利息或分期手续费，会计入支出。</p>
          <StateBlock v-if="!detail.repayments.length" state="empty" title="暂无还款记录" />
          <ul v-else class="list">
            <li v-for="r in detail.repayments" :key="r.id">
              <div class="l-main">
                <strong>还款 {{ r.amount }} {{ detail.currency }}</strong>
                <small>{{ fmtDate(r.occurred_at) }} · 本金 {{ r.principal }} · 利息 {{ r.interest }} · 费用 {{ r.fee }}</small>
              </div>
            </li>
          </ul>
        </div>

        <div v-else class="pane">
          <div class="ai">
            <h4><Sparkles :size="14" /> AI 可以做什么</h4>
            <ul><li v-for="c in detail.ai.allowed" :key="c">{{ c }}</li></ul>
            <h4>AI 不能做什么</h4>
            <ul class="forbidden"><li v-for="c in detail.ai.forbidden" :key="c">{{ c }}</li></ul>
            <AppButton variant="secondary" @click="ui.openAiPanel(`信用卡：${detail.name}`)">
              <Sparkles :size="15" /> 就这笔向 AI 追问
            </AppButton>
          </div>
        </div>
      </template>
      <template #footer>
        <AppButton variant="primary" @click="detailOpen = false">关闭</AppButton>
      </template>
    </AppDrawer>

    <!-- 消费 -->
    <AppModal
      :open="expenseOpen" title="记一笔消费" confirm-text="保存消费"
      :confirm-loading="expenseSaving" @close="expenseOpen = false" @confirm="submitExpense"
    >
      <div class="form">
        <AppField label="消费金额" required :error="expenseErrors.amount">
          <AppInput v-model="expenseForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="标题" :error="expenseErrors.title">
          <AppInput v-model="expenseForm.title" placeholder="例如：超市购物" />
        </AppField>
        <AppField label="消费分类" :error="expenseErrors.category_id">
          <AppSelect
            v-model="expenseForm.category_id"
            :options="[{ value: '', label: '不指定分类' }, ...categories.map((c) => ({ value: c.id, label: c.name }))]"
          />
        </AppField>
        <AppField label="消费日期">
          <AppDatePicker v-model="expenseForm.occurred_at" value-format="datetime" placeholder="留空为今天" />
        </AppField>
        <AppField label="备注"><AppInput v-model="expenseForm.note" /></AppField>
      </div>
    </AppModal>

    <!-- 生成账单 -->
    <AppModal
      :open="billOpen" title="生成账单" confirm-text="生成"
      :confirm-loading="billSaving" @close="billOpen = false" @confirm="submitBill"
    >
      <div class="form">
        <AppField label="本期账单金额" required :error="billErrors.total_amount">
          <AppInput v-model="billForm.total_amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="最低还款" :error="billErrors.minimum_payment">
          <AppInput v-model="billForm.minimum_payment" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="账期">
          <AppDateRangePicker
            v-model:start="billForm.period_start" v-model:end="billForm.period_end"
            placeholder="选择本期账期"
          />
        </AppField>
        <AppField label="账单日 / 到期日">
          <div class="grid-2">
            <AppDatePicker v-model="billForm.bill_date" placeholder="账单日" />
            <AppDatePicker v-model="billForm.due_date" placeholder="到期日" />
          </div>
        </AppField>
        <AppField label="备注"><AppInput v-model="billForm.note" /></AppField>
      </div>
    </AppModal>

    <!-- 还款 -->
    <AppModal
      :open="repayOpen" title="信用卡还款" confirm-text="确认还款"
      :confirm-loading="repaySaving" @close="repayOpen = false" @confirm="submitRepay"
    >
      <div class="form">
        <p class="tip">还款不会重复计入消费；本金减少已用额度，利息与费用计入支出。</p>
        <AppField label="还款金额" required :error="repayErrors.amount">
          <AppInput v-model="repayForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="还款账户" required :error="repayErrors.account_id">
          <AppSelect
            v-model="repayForm.account_id"
            :options="[{ value: '', label: '请选择账户' }, ...accounts.map((a) => ({ value: a.id, label: accountLabel(a, ui.amountPrecision) }))]"
          />
        </AppField>
        <AppField v-if="detail?.bills?.length" label="关联账单（可选）" hint="关联后账单已还金额自动累计">
          <AppSelect
            v-model="repayForm.bill_id"
            :options="[{ value: '', label: '不关联账单' }, ...detail.bills.map((b) => ({ value: b.id, label: `账单 ${b.total_amount}（${fmtDate(b.bill_date)}）` }))]"
          />
        </AppField>
        <AppField label="还款日期">
          <AppDatePicker v-model="repayForm.occurred_at" value-format="datetime" placeholder="留空为今天" />
        </AppField>
        <AppField label="备注"><AppInput v-model="repayForm.note" /></AppField>
      </div>
    </AppModal>
  </div>
</template>

<style scoped>
.cards { display: flex; flex-direction: column; gap: var(--space-4); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(196px, 1fr)); gap: var(--space-3); }
@media (max-width: 640px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .stat :deep(.card-body) { padding: 11px 12px 12px; }
  .stat .k { font-size: 11.5px; }
  .stat .n { font-size: 19px; }
  .stat :deep(.money) { font-size: 17px; }
}
.stat .k { font-size: 12.5px; color: var(--ink-muted); }
.stat .n { font-size: 22px; font-weight: 600; color: var(--ink-strong); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: var(--space-4); }
.card-item { display: flex; flex-direction: column; gap: 10px; }
.ci-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.ci-head strong { display: block; font-size: 15px; color: var(--ink-strong); }
.ci-head small { font-size: 11.5px; color: var(--ink-muted); }
.progress { height: 6px; border-radius: 999px; background: var(--glass-2); overflow: hidden; }
.progress span { display: block; height: 100%; background: var(--primary); }
.ci-kv { display: flex; flex-direction: column; }
.ci-kv li { display: flex; justify-content: space-between; gap: 8px; padding: 5px 0; font-size: 12.5px; border-bottom: 1px dashed var(--border); }
.ci-kv .k { color: var(--ink-muted); }
.ci-kv .v { color: var(--ink-strong); }
.ci-kv .v.strong { font-weight: 600; }
.ci-bill { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.cb-main { display: flex; flex-direction: column; }
.cb-main small { font-size: 11.5px; color: var(--ink-muted); }
.cb-main strong { font-size: 14px; color: var(--ink-strong); }
.ci-empty { font-size: 12px; color: var(--ink-muted); }
.ci-actions { display: flex; gap: var(--space-2); justify-content: flex-end; }
.tabs { display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid var(--border); margin-bottom: var(--space-3); }
.tab { flex: none; padding: 8px 12px; border: none; background: transparent; cursor: pointer; font-size: 13px; color: var(--ink-muted); border-bottom: 2px solid transparent; }
.tab.is-active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 500; }
.pane { display: flex; flex-direction: column; gap: var(--space-3); }
.pane-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.list { display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--glass-radius-control); background: var(--glass-1); }
.l-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.l-main strong { font-size: 13.5px; color: var(--ink-strong); }
.l-main small { font-size: 11.5px; color: var(--ink-muted); }
.l-actions { display: flex; align-items: center; gap: 6px; }
.l-amount { font-variant-numeric: tabular-nums; font-size: 13.5px; white-space: nowrap; }
.act { height: 26px; padding: 0 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1); color: var(--ink-strong); font-size: 12px; cursor: pointer; }
.act:hover { border-color: var(--primary); color: var(--primary); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tip { font-size: 12.5px; color: var(--ink-muted); line-height: 1.6; }
.ai { display: flex; flex-direction: column; gap: 10px; }
.ai h4 { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--ink-strong); }
.ai ul.forbidden li { color: var(--ink-muted); }
@media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }
</style>
