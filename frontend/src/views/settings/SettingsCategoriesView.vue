<script setup lang="ts">
/** 分类管理 /settings/categories（08 §3）：六个分类域、启停、排序、父子层级、引用保护。 */
import { computed, onMounted, ref } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { CATEGORY_DOMAIN_ZH } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useUiStore } from '@/stores/ui'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface Category {
  id: string
  domain: string
  domain_label: string
  parent_id: string | null
  name: string
  level: number
  sort_order: number
  status: string
  status_label: string
  is_system: boolean
  ai_recommendable: boolean
  applies_to: string | null
  attribute_profile: string | null
}

/** 账户域字段档案：决定新建账户时显示哪些专属字段 */
const ATTRIBUTE_PROFILE_OPTIONS = [
  { value: '', label: '按账户类型自动' },
  { value: 'credit_card', label: '信用卡（额度 / 有效期 / 账单日 / 还款日 / 卡号）' },
  { value: 'credit_line', label: '消费信贷（额度 / 账单日 / 还款日）' },
  { value: 'loan_line', label: '借款额度（额度 / 年化利率 / 还款日）' },
  { value: 'debit_card', label: '借记卡（卡号）' },
  { value: 'savings_current', label: '活期储蓄（年利率）' },
  { value: 'savings_fixed', label: '定期存款（年利率 / 存期 / 到期日）' },
  { value: 'wallet', label: '电子钱包（开户人 / 余额）' },
  { value: 'cash', label: '现金（无额外字段）' },
]

/** 账户域适用范围选项（对应 account_type 枚举） */
const ACCOUNT_APPLY_OPTIONS = [
  { value: 'bank', label: '银行卡' },
  { value: 'e_wallet', label: '电子钱包 / 消费信贷' },
  { value: 'cash', label: '现金' },
  { value: 'other', label: '其他' },
]

const auth = useAuthStore()
const toast = useToastStore()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const items = ref<Category[]>([])
const domain = ref('expense')
const drawerOpen = ref(false)
const saving = ref(false)
const fieldErrors = ref<Record<string, string>>({})
const form = ref({ name: '', parent_id: '', sort_order: 0, ai_recommendable: true, applies: [] as string[], profile: '' })

const domainOptions = Object.entries(CATEGORY_DOMAIN_ZH).map(([value, label]) => ({ value, label }))
/** 父分类名：二级分类显示「父级 / 自己」，否则缩进之外看不出它属于谁 */
const nameById = computed(() => Object.fromEntries(items.value.map((c) => [c.id, c.name])))
function parentName(c: { parent_id: string | null }): string {
  return c.parent_id ? nameById.value[c.parent_id] || '' : ''
}
/** 每个一级分类下的子分类数量，标题上直接给出层级规模 */
const childCount = computed(() => {
  const map: Record<string, number> = {}
  for (const c of items.value) {
    if (c.parent_id) map[c.parent_id] = (map[c.parent_id] || 0) + 1
  }
  return map
})

const filtered = computed(() => items.value.filter((c) => c.domain === domain.value))
const parentOptions = computed(() => [
  { value: '', label: '作为一级分类' },
  ...filtered.value.map((c) => ({ value: c.id, label: `${'　'.repeat(c.level - 1)}${c.name}` })),
])
const canConfigure = computed(() => auth.can('configure'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get<{ items: Category[] }>('/settings/categories', { domain: domain.value })
    items.value = res.items
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function createCategory() {
  saving.value = true
  fieldErrors.value = {}
  try {
    await api.post('/settings/categories', {
      domain: domain.value,
      name: form.value.name,
      parent_id: form.value.parent_id || null,
      sort_order: Number(form.value.sort_order),
      ai_recommendable: form.value.ai_recommendable,
      applies_to: form.value.applies.length ? form.value.applies.join(',') : null,
      attribute_profile: form.value.profile || null,
    })
    toast.success('分类已创建')
    drawerOpen.value = false
    form.value = { name: '', parent_id: '', sort_order: 0, ai_recommendable: true, applies: [], profile: '' }
    await load()
  } catch (err) {
    fieldErrors.value = errorFields(err)
    toast.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function toggleStatus(cat: Category) {
  try {
    await api.patch(`/settings/categories/${cat.id}`, {
      status: cat.status === 'active' ? 'disabled' : 'active',
    })
    toast.success(cat.status === 'active' ? '分类已停用' : '分类已启用')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function toggleAi(cat: Category) {
  try {
    await api.patch(`/settings/categories/${cat.id}`, { ai_recommendable: !cat.ai_recommendable })
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

async function removeCategory(cat: Category) {
  const res = await ui.askConfirm({
    title: '确认删除分类',
    message: `将删除分类「${cat.name}」。已被业务事件使用的分类不能物理删除，只能停用。`,
    detail: ['删除为软删除，会保留审计记录。', '已确认业务事实不会被改动。'],
    confirmText: '确认删除',
    danger: true,
    irreversible: false,
  })
  if (!res.ok) return
  try {
    const tokenRes = await api.post<{ token: string }>('/settings/confirm-tokens', {
      action: 'category.delete', object_type: 'category', object_id: cat.id,
    })
    await api.delete(`/settings/categories/${cat.id}`, { confirm_token: tokenRes.token })
    toast.success('分类已删除（软删除并保留审计）')
    await load()
  } catch (err) {
    toast.error(errorMessage(err))
  }
}

onMounted(load)

function toggleApply(value: string) {
  const arr = form.value.applies
  const index = arr.indexOf(value)
  if (index >= 0) arr.splice(index, 1)
  else arr.push(value)
}

/** 适用范围的可读标签（仅账户域有意义） */
function applyLabel(c: Category) {
  if (c.domain !== 'account' || !c.applies_to) return ''
  return c.applies_to
    .split(',')
    .map((v) => ACCOUNT_APPLY_OPTIONS.find((o) => o.value === v)?.label || v)
    .join(' / ')
}
</script>

<template>
  <div class="cats">
    <PageHeader title="分类管理" subtitle="分类域：收入、支出、资产、负债、投资、租赁、账户。支持父子层级；已被业务数据使用的分类不能物理删除。">
      <AppSelect v-model="domain" :options="domainOptions" class="domain" @update:model-value="load" />
      <AppButton v-if="canConfigure" variant="primary" @click="drawerOpen = true">
        <Plus :size="15" /> 新增分类
      </AppButton>
    </PageHeader>

    <StateBlock v-if="loading" state="loading" />
    <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />

    <template v-else>
      <AppCard :title="`${CATEGORY_DOMAIN_ZH[domain]}分类（${filtered.length}）`">
        <StateBlock v-if="!filtered.length" state="empty" title="该分类域下暂无分类" description="可以新增分类，或切换其他分类域。" />
        <ul v-else class="list">
          <li v-for="c in filtered" :key="c.id" :style="{ paddingLeft: `${12 + (c.level - 1) * 22}px` }">
            <div class="c-main">
              <strong>{{ c.name }}</strong>
              <small v-if="parentName(c)" class="parent">{{ parentName(c) }} 下</small>
              <small v-else-if="childCount[c.id]" class="parent">{{ childCount[c.id] }} 个子分类</small>
              <StatusBadge v-if="c.is_system" label="系统内置" tone="info" />
              <StatusBadge :label="c.status_label" :tone="c.status === 'active' ? 'success' : 'neutral'" />
              <StatusBadge :label="c.ai_recommendable ? 'AI 可推荐' : 'AI 不推荐'" :tone="c.ai_recommendable ? 'ai' : 'neutral'" />
              <StatusBadge v-if="applyLabel(c)" :label="`仅 ${applyLabel(c)}`" tone="neutral" />
            </div>
            <div class="c-actions">
              <button v-if="canConfigure" class="act" type="button" @click="toggleAi(c)">
                {{ c.ai_recommendable ? '关闭 AI 推荐' : '开启 AI 推荐' }}
              </button>
              <button v-if="canConfigure" class="act" type="button" @click="toggleStatus(c)">
                {{ c.status === 'active' ? '停用' : '启用' }}
              </button>
              <button v-if="canConfigure && !c.is_system" class="act danger" type="button" @click="removeCategory(c)">
                <Trash2 :size="13" /> 删除
              </button>
            </div>
          </li>
        </ul>
      </AppCard>

      <AppCard title="引用保护与排序" level="1">
        <p class="note">
          支持新建、编辑、启用、停用、排序与父子层级；已被业务事件使用的分类不能物理删除。
          每个分类都可以设置是否允许 AI 推荐；账户域还可声明「适用的账户类型」，新建账户时按类型自动筛选。
        </p>
      </AppCard>
    </template>

    <AppDrawer :open="drawerOpen" title="新增分类" @close="drawerOpen = false">
      <div class="form">
        <AppField label="分类域">
          <AppInput :model-value="CATEGORY_DOMAIN_ZH[domain]" disabled />
        </AppField>
        <AppField label="分类名称" required :error="fieldErrors.name">
          <AppInput v-model="form.name" />
        </AppField>
        <AppField label="上级分类" :error="fieldErrors.parent_id">
          <AppSelect v-model="form.parent_id" :options="parentOptions" />
        </AppField>
        <AppField label="排序" :error="fieldErrors.sort_order">
          <AppInput v-model="form.sort_order" type="number" />
        </AppField>
        <AppField v-if="domain === 'account'" label="适用账户类型"
                  hint="不选 = 通用（所有账户类型下都可见）；选中后仅在对应类型的账户表单中出现">
          <div class="chips">
            <button
              v-for="t in ACCOUNT_APPLY_OPTIONS"
              :key="t.value"
              class="chip"
              :class="{ on: form.applies.includes(t.value) }"
              type="button"
              @click="toggleApply(t.value)"
            >
              {{ t.label }}
            </button>
          </div>
        </AppField>
        <AppField v-if="domain === 'account'" label="字段档案"
                  hint="决定新建账户时显示哪些专属字段；不选则按账户类型自动判断">
          <AppSelect v-model="form.profile" :options="ATTRIBUTE_PROFILE_OPTIONS" />
        </AppField>
        <AppField label="允许 AI 推荐">
          <AppSelect :model-value="String(form.ai_recommendable)" :options="[
            { value: 'true', label: '允许' }, { value: 'false', label: '不允许' },
          ]" @update:model-value="(v) => (form.ai_recommendable = v === 'true')" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="drawerOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="createCategory">创建</AppButton>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.c-main .parent { font-size: 11.5px; color: var(--ink-muted); }
.cats { display: flex; flex-direction: column; gap: var(--space-4); }
.domain { width: 160px; }
.list { display: flex; flex-direction: column; gap: 6px; }
.list li {
  display: flex; align-items: center; justify-content: space-between; gap: var(--space-3);
  padding: 10px 12px; border: 1px solid var(--border);
  border-radius: var(--glass-radius-control); background: var(--glass-1);
}
.c-main { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.c-main strong { font-size: 13.5px; color: var(--ink-strong); }
.c-actions { display: flex; gap: var(--space-2); flex-wrap: wrap; }
.act {
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--glass-2); color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 4px;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.act.danger:hover { border-color: var(--danger); color: var(--danger); }
.note { font-size: 12.5px; color: var(--ink-muted); line-height: 1.8; }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.chips { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.chip {
  height: 32px; padding: 0 12px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--glass-1); color: var(--ink-muted); font-size: 12.5px; cursor: pointer;
}
.chip:hover { border-color: var(--primary); color: var(--primary); }
.chip.on {
  border-color: var(--primary); color: var(--primary);
  background: color-mix(in srgb, var(--primary) 12%, transparent);
}
@media (max-width: 700px) {
  .list li { flex-direction: column; align-items: flex-start; padding: 9px 11px; gap: 7px; }
  .c-main { gap: 6px; }
  .c-main strong { font-size: 13px; }
  .c-actions { gap: 6px; }
  .act { height: 26px; padding: 0 9px; font-size: 12px; }
}
</style>
