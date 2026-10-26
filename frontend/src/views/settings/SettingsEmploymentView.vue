<script setup lang="ts">
/**
 * 工作与收入 /settings/employment（工作维度：在职状态 + 社保公积金基数 + 固定收入）。
 * 只记录「与钱相关」的工作维度，不做职业/简历管理。
 */
import { computed, onMounted, ref } from 'vue'
import { Briefcase, CalendarClock, Plus, Trash2 } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import AppDataTable, { type Column } from '@/components/ui/AppDataTable.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Member { id: string; display_name: string; status: string }
interface EmploymentRow {
  id: string; member_id: string; member_name: string; employer: string | null
  employment_status: string; employment_status_label: string
  social_insurance_base: string | null; housing_fund_base: string | null
  start_date: string | null; end_date: string | null; note: string | null; version: number
}
interface IncomeRow {
  id: string; title: string; category_id: string | null; category_label: string
  account_id: string | null; member_id: string | null; member_name: string
  currency: string; amount: string; frequency: string; frequency_label: string
  day_of_month: number | null; next_due_date: string | null; status: string
  status_label: string; note: string | null; version: number
}

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const canConfigure = () => auth.can('edit_draft')

const loading = ref(true)
const error = ref('')

const profiles = ref<EmploymentRow[]>([])
const incomes = ref<IncomeRow[]>([])
const members = ref<Member[]>([])

const STATUS_OPTIONS = [
  { value: 'employed', label: '在职' },
  { value: 'probation', label: '试用期' },
  { value: 'unemployed', label: '失业' },
  { value: 'retired', label: '退休' },
  { value: 'freelance', label: '自由职业' },
]
const FREQ_OPTIONS = [
  { value: 'monthly', label: '每月' },
  { value: 'weekly', label: '每周' },
  { value: 'yearly', label: '每年' },
]

const memberOptions = computed(() => [
  { value: '', label: '请选择成员' },
  ...members.value.filter((m) => m.status === 'active')
    .map((m) => ({ value: m.id, label: m.display_name })),
])

// ---- 工作档案编辑 ----
const profileDrawer = ref(false)
const profileSaving = ref(false)
const profileErrors = ref<Record<string, string>>({})
const profileForm = ref({
  id: '', member_id: '', employer: '', employment_status: 'employed',
  social_insurance_base: '', housing_fund_base: '', start_date: '', end_date: '', note: '',
  version: 1,
})

// ---- 固定收入编辑 ----
const incomeDrawer = ref(false)
const incomeSaving = ref(false)
const incomeErrors = ref<Record<string, string>>({})
const incomeForm = ref({
  id: '', title: '', member_id: '', account_id: '', category_id: '',
  amount: '', frequency: 'monthly', day_of_month: 1, currency: 'CNY', note: '',
  version: 1,
})

const profileColumns: Column[] = [
  { key: 'member_name', label: '成员', primary: true },
  { key: 'employer', label: '雇主' },
  { key: 'employment_status_label', label: '在职状态' },
  { key: 'social_insurance_base', label: '社保基数' },
  { key: 'actions', label: '操作', align: 'right' },
]
const incomeColumns: Column[] = [
  { key: 'title', label: '收入', primary: true },
  { key: 'member_name', label: '成员' },
  { key: 'amount', label: '金额' },
  { key: 'frequency_label', label: '周期' },
  { key: 'next_due_date', label: '下次到账' },
  { key: 'status_label', label: '状态' },
  { key: 'actions', label: '操作', align: 'right' },
]

function fmtDate(s: string | null): string {
  return s ? s.slice(0, 10) : '—'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [p, i, hh] = await Promise.all([
      api.get<{ items: EmploymentRow[] }>('/ledger/employment'),
      api.get<{ items: IncomeRow[] }>('/ledger/recurring-incomes'),
      api.get<{ members: Member[] }>('/settings/household'),
    ])
    members.value = hh.members || []
    const nameMap = new Map(members.value.map((m) => [m.id, m.display_name]))
    profiles.value = (p.items || []).map((r) => ({ ...r, member_name: r.member_name || nameMap.get(r.member_id) || '—' }))
    incomes.value = (i.items || []).map((r) => ({ ...r, member_name: r.member_name || nameMap.get(r.member_id || '') || '—' }))
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

// ---- 工作档案 ----
function openProfileCreate() {
  profileErrors.value = {}
  profileForm.value = { id: '', member_id: '', employer: '', employment_status: 'employed',
    social_insurance_base: '', housing_fund_base: '', start_date: '', end_date: '', note: '', version: 1 }
  profileDrawer.value = true
}
function openProfileEdit(row: EmploymentRow) {
  profileErrors.value = {}
  profileForm.value = { id: row.id, member_id: row.member_id, employer: row.employer || '',
    employment_status: row.employment_status, social_insurance_base: row.social_insurance_base || '',
    housing_fund_base: row.housing_fund_base || '', start_date: row.start_date ? row.start_date.slice(0, 10) : '',
    end_date: row.end_date ? row.end_date.slice(0, 10) : '', note: row.note || '', version: row.version }
  profileDrawer.value = true
}
async function saveProfile() {
  profileSaving.value = true
  profileErrors.value = {}
  try {
    const body: Record<string, unknown> = {
      member_id: profileForm.value.member_id || null,
      employer: profileForm.value.employer || null,
      employment_status: profileForm.value.employment_status,
      social_insurance_base: profileForm.value.social_insurance_base || null,
      housing_fund_base: profileForm.value.housing_fund_base || null,
      start_date: profileForm.value.start_date || null,
      end_date: profileForm.value.end_date || null,
      note: profileForm.value.note || null,
    }
    if (profileForm.value.id) {
      body.version = profileForm.value.version
      await api.put(`/ledger/employment/${profileForm.value.id}`, body)
      toast.success('工作档案已更新')
    } else {
      await api.post('/ledger/employment', body)
      toast.success('工作档案已创建')
    }
    profileDrawer.value = false
    await load()
  } catch (err) {
    profileErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    profileSaving.value = false
  }
}
async function removeProfile(row: EmploymentRow) {
  const res = await ui.askConfirm({
    title: '删除工作档案', message: `确认删除「${row.member_name}」的工作档案？`,
    confirmText: '删除', danger: true,
  })
  if (!res.ok) return
  await api.delete(`/ledger/employment/${row.id}`)
  toast.success('已删除')
  await load()
}

// ---- 固定收入 ----
function openIncomeCreate() {
  incomeErrors.value = {}
  incomeForm.value = { id: '', title: '', member_id: '', account_id: '', category_id: '',
    amount: '', frequency: 'monthly', day_of_month: 1, currency: 'CNY', note: '', version: 1 }
  incomeDrawer.value = true
}
function openIncomeEdit(row: IncomeRow) {
  incomeErrors.value = {}
  incomeForm.value = { id: row.id, title: row.title, member_id: row.member_id || '',
    account_id: row.account_id || '', category_id: row.category_id || '',
    amount: row.amount, frequency: row.frequency, day_of_month: row.day_of_month || 1,
    currency: row.currency, note: row.note || '', version: row.version }
  incomeDrawer.value = true
}
async function saveIncome() {
  incomeSaving.value = true
  incomeErrors.value = {}
  try {
    const body: Record<string, unknown> = {
      title: incomeForm.value.title,
      member_id: incomeForm.value.member_id || null,
      account_id: incomeForm.value.account_id || null,
      category_id: incomeForm.value.category_id || null,
      amount: incomeForm.value.amount || '0',
      frequency: incomeForm.value.frequency,
      day_of_month: incomeForm.value.day_of_month,
      currency: incomeForm.value.currency,
      note: incomeForm.value.note || null,
    }
    if (incomeForm.value.id) {
      body.version = incomeForm.value.version
      await api.put(`/ledger/recurring-incomes/${incomeForm.value.id}`, body)
      toast.success('固定收入已更新')
    } else {
      await api.post('/ledger/recurring-incomes', body)
      toast.success('固定收入已登记，到期自动记账')
    }
    incomeDrawer.value = false
    await load()
  } catch (err) {
    incomeErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    incomeSaving.value = false
  }
}
async function toggleIncome(row: IncomeRow) {
  const target = row.status === 'paused' ? 'resume' : 'pause'
  await api.post(`/ledger/recurring-incomes/${row.id}/${target}`)
  toast.success(target === 'pause' ? '已暂停' : '已恢复')
  await load()
}
async function removeIncome(row: IncomeRow) {
  const res = await ui.askConfirm({
    title: '删除固定收入', message: `确认删除「${row.title}」？`,
    confirmText: '删除', danger: true,
  })
  if (!res.ok) return
  await api.delete(`/ledger/recurring-incomes/${row.id}`)
  toast.success('已删除')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="employment">
    <PageHeader
      title="工作与收入"
      subtitle="记录家庭成员的在职状态、社保公积金基数与固定收入（工资/养老金），把「钱的来源」纳入家庭资产全景。"
    />

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else>
      <!-- 工作档案 -->
      <AppCard title="工作档案" level="1">
        <template #extra>
          <AppButton variant="secondary" :disabled="!canConfigure()" @click="openProfileCreate">
            <Plus :size="15" /> 新增档案
          </AppButton>
        </template>
        <p class="muted">
          只记录「与钱相关」的工作维度：在职状态用于现金流风险预警，社保公积金基数是工资之外的隐性福利，不参与净资产计算。
        </p>
        <AppDataTable
          v-if="profiles.length" :columns="profileColumns" :rows="profiles"
        >
          <template #employment_status_label="{ row }">
            <StatusBadge
              :label="row.employment_status_label"
              :tone="row.employment_status === 'unemployed' ? 'danger'
                : row.employment_status === 'probation' ? 'warning' : 'success'"
            />
          </template>
          <template #actions="{ row }">
            <div class="row-actions">
              <AppButton variant="ghost" size="sm" :disabled="!canConfigure()" @click="openProfileEdit(row)">编辑</AppButton>
              <AppButton variant="ghost" size="sm" :disabled="!canConfigure()" @click="removeProfile(row)">
                <Trash2 :size="14" />
              </AppButton>
            </div>
          </template>
        </AppDataTable>
        <StateBlock v-else state="empty" description="还没有工作档案，点「新增档案」为家庭成员登记在职状态。" />
      </AppCard>

      <!-- 固定收入 -->
      <AppCard title="固定收入（工资 / 养老金等）" level="1">
        <template #extra>
          <AppButton variant="secondary" :disabled="!canConfigure()" @click="openIncomeCreate">
            <Plus :size="15" /> 新增收入
          </AppButton>
        </template>
        <p class="muted">
          金额固定、按周期重复的收入，到期自动生成一笔收入事件（与手工记账同口径）。也可在「流水」页记收入时直接勾选「设为每月固定收入」。
        </p>
        <AppDataTable v-if="incomes.length" :columns="incomeColumns" :rows="incomes">
          <template #next_due_date="{ row }">{{ fmtDate(row.next_due_date) }}</template>
          <template #actions="{ row }">
            <div class="row-actions">
              <AppButton variant="ghost" size="sm" :disabled="!canConfigure()" @click="toggleIncome(row)">
                {{ row.status === 'paused' ? '恢复' : '暂停' }}
              </AppButton>
              <AppButton variant="ghost" size="sm" :disabled="!canConfigure()" @click="openIncomeEdit(row)">编辑</AppButton>
              <AppButton variant="ghost" size="sm" :disabled="!canConfigure()" @click="removeIncome(row)">
                <Trash2 :size="14" />
              </AppButton>
            </div>
          </template>
        </AppDataTable>
        <StateBlock v-else state="empty" description="还没有固定收入。登记工资后，每月发薪日会自动记账。" />
      </AppCard>
    </template>

    <!-- 工作档案抽屉 -->
    <AppDrawer :open="profileDrawer" :title="profileForm.id ? '编辑工作档案' : '新增工作档案'" @close="profileDrawer = false">
      <div class="drawer-body">
        <AppField label="家庭成员" :error="profileErrors.member_id" required>
          <AppSelect v-model="profileForm.member_id" :options="memberOptions" :disabled="Boolean(profileForm.id)" />
        </AppField>
        <AppField label="在职状态" :error="profileErrors.employment_status">
          <AppSelect v-model="profileForm.employment_status" :options="STATUS_OPTIONS" />
        </AppField>
        <AppField label="雇主" :error="profileErrors.employer" hint="仅记录雇主名称，脱敏展示，不做简历管理">
          <AppInput v-model="profileForm.employer" />
        </AppField>
        <AppField label="社保基数（元/月）" :error="profileErrors.social_insurance_base">
          <AppInput v-model="profileForm.social_insurance_base" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="公积金基数（元/月）" :error="profileErrors.housing_fund_base">
          <AppInput v-model="profileForm.housing_fund_base" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="备注" :error="profileErrors.note">
          <AppInput v-model="profileForm.note" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="profileDrawer = false">取消</AppButton>
        <AppButton variant="primary" :loading="profileSaving" @click="saveProfile">保存</AppButton>
      </template>
    </AppDrawer>

    <!-- 固定收入抽屉 -->
    <AppDrawer :open="incomeDrawer" :title="incomeForm.id ? '编辑固定收入' : '新增固定收入'" @close="incomeDrawer = false">
      <div class="drawer-body">
        <AppField label="收入名称" :error="incomeErrors.title" required>
          <AppInput v-model="incomeForm.title" placeholder="如：工资、养老金" />
        </AppField>
        <AppField label="金额" :error="incomeErrors.amount" required>
          <AppInput v-model="incomeForm.amount" type="number" placeholder="0.00" />
        </AppField>
        <AppField label="归属成员（可选）" :error="incomeErrors.member_id">
          <AppSelect v-model="incomeForm.member_id" :options="[{ value: '', label: '不指定成员' }, ...memberOptions.filter((o) => o.value)]" />
        </AppField>
        <div class="grid-2">
          <AppField label="发薪日" :error="incomeErrors.day_of_month">
            <AppInput v-model="incomeForm.day_of_month" type="number" min="1" max="28" />
          </AppField>
          <AppField label="周期" :error="incomeErrors.frequency">
            <AppSelect v-model="incomeForm.frequency" :options="FREQ_OPTIONS" />
          </AppField>
        </div>
        <AppField label="备注" :error="incomeErrors.note">
          <AppInput v-model="incomeForm.note" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="incomeDrawer = false">取消</AppButton>
        <AppButton variant="primary" :loading="incomeSaving" @click="saveIncome">保存</AppButton>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.muted { color: var(--text-muted); font-size: 13px; margin: 0 0 12px; }
.drawer-body { display: flex; flex-direction: column; gap: 4px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); align-items: center; }
</style>
