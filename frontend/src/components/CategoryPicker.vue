<script setup lang="ts">
/**
 * 分类选择器（08 §3）：按分类域加载「一级 + 子分类」层级选项，支持内联新增分类。
 * - 选项按「父分类 → 缩进子分类」排列，子分类直接可选（不是手动输入）；
 * - 不在选项里时点「＋」新增（可选上级分类），创建后自动选中；
 * - 传 filterKey（如账户类型）时只显示通用分类与该 key 适用的分类，实现联动筛选。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { Plus } from 'lucide-vue-next'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import AppButton from '@/components/ui/AppButton.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppModal from '@/components/ui/AppModal.vue'
import AppSelect from '@/components/ui/AppSelect.vue'

interface Category {
  id: string
  name: string
  domain: string
  parent_id: string | null
  level: number
  sort_order: number
  status: string
  applies_to: string | null
}

const props = withDefaults(defineProps<{
  modelValue: string
  domain: string
  label?: string
  hint?: string
  error?: string
  required?: boolean
  placeholder?: string
  /** 只显示二级分类（如电子钱包：二级分类本身就是开户机构）。 */
  leafOnly?: boolean
  /** 联动筛选键：仅保留「通用分类」或 applies_to 含该键的分类（如账户类型 code）。 */
  filterKey?: string
  /**
   * 一级分类白名单（按名称）：只显示这些一级分类及其子分类。
   * 用于「先选类型、分类只保留与该类型相关项」的联动（如负债的用途分类）。
   * 为空表示不过滤。名单之外的一级分类连同子分类一起隐藏。
   */
  includeRoots?: string[]
}>(), {
  label: '分类',
  placeholder: '请选择分类',
  leafOnly: false,
  filterKey: '',
  includeRoots: () => [],
})

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const auth = useAuthStore()
const toast = useToastStore()
const categories = ref<Category[]>([])
const loading = ref(false)
const modalOpen = ref(false)
const newName = ref('')
const newParent = ref('')
const creating = ref(false)

const canManage = computed(() => auth.can('configure'))

/** 分类是否在筛选下可见：通用（applies_to 为空）或适用于当前 filterKey。 */
function appliesVisible(c: Category) {
  if (!props.filterKey) return true
  const keys = (c.applies_to || '').split(',').map((s) => s.trim()).filter(Boolean)
  return keys.length === 0 || keys.includes(props.filterKey)
}

/** 一级分类是否在白名单内（空名单表示不限）。子分类跟随父分类一起显隐。 */
function rootAllowed(c: Category) {
  if (!props.includeRoots.length) return true
  return props.includeRoots.includes(c.name)
}

/** 按「是否套用一级白名单」构建选项；白名单命中为空时回退到不过滤，避免下拉变空。 */
function buildOptions(applyRootFilter: boolean) {
  const byParent: Record<string, Category[]> = {}
  const roots: Category[] = []
  for (const c of categories.value) {
    if (c.parent_id) (byParent[c.parent_id] ||= []).push(c)
    else roots.push(c)
  }
  const byOrder = (a: Category, b: Category) =>
    (a.sort_order - b.sort_order) || a.name.localeCompare(b.name, 'zh')
  roots.sort(byOrder)
  const out: { value: string; label: string }[] = []
  for (const r of roots) {
    if (!appliesVisible(r)) continue
    if (applyRootFilter && !rootAllowed(r)) continue
    const children = (byParent[r.id] || []).sort(byOrder)
    // leafOnly：只要二级分类（一级只是分组，不作为可选项）
    if (!props.leafOnly) out.push({ value: r.id, label: r.name })
    for (const ch of children) {
      out.push({ value: ch.id, label: props.leafOnly ? ch.name : `　└ ${ch.name}` })
    }
  }
  return out
}

const options = computed(() => {
  if (!props.includeRoots.length) return buildOptions(false)
  const filtered = buildOptions(true)
  // 家庭改过分类名 / 删光了相关分类时不要让下拉变空
  return filtered.length ? filtered : buildOptions(false)
})

const parentOptions = computed(() => [
  { value: '', label: '作为一级分类' },
  ...categories.value
    .filter((c) => !c.parent_id && appliesVisible(c) && rootAllowed(c))
    .sort((a, b) => (a.sort_order - b.sort_order) || a.name.localeCompare(b.name, 'zh'))
    .map((c) => ({ value: c.id, label: c.name })),
])

async function load() {
  if (!props.domain) return
  loading.value = true
  try {
    const res = await api.get<{ items: Category[] }>('/settings/categories', {
      domain: props.domain,
    })
    categories.value = (res.items || []).filter((c) => c.status === 'active')
  } catch {
    categories.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.domain, load)

function openModal() {
  newName.value = ''
  newParent.value = ''
  modalOpen.value = true
}

async function create() {
  const name = newName.value.trim()
  if (!name) {
    toast.error('请填写分类名称')
    return
  }
  creating.value = true
  try {
    const created = await api.post<Category>('/settings/categories', {
      domain: props.domain,
      name,
      parent_id: newParent.value || null,
    })
    toast.success('分类已新增')
    modalOpen.value = false
    await load()
    emit('update:modelValue', created.id)
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="cat-picker">
    <AppField :label="label" :required="required" :error="error" :hint="hint">
      <div class="row">
        <AppSelect
          :model-value="modelValue"
          :options="options"
          :placeholder="modelValue ? '' : placeholder"
          :disabled="loading"
          @update:model-value="emit('update:modelValue', $event)"
        />
        <button
          v-if="canManage"
          class="add"
          type="button"
          title="新增分类"
          aria-label="新增分类"
          @click="openModal"
        >
          <Plus :size="15" />
        </button>
      </div>
    </AppField>

    <AppModal :open="modalOpen" title="新增分类" size="sm" @close="modalOpen = false">
      <AppField label="分类名称" required>
        <AppInput v-model="newName" placeholder="例如：宠物 / 早教" />
      </AppField>
      <AppField label="上级分类" hint="选择后作为其子分类；不选则新增一级分类">
        <AppSelect v-model="newParent" :options="parentOptions" />
      </AppField>
      <template #footer>
        <AppButton variant="secondary" @click="modalOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="creating" @click="create">创建</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.cat-picker { display: block; }
.row { display: flex; gap: var(--space-2); align-items: center; }
.row :deep(.select) { flex: 1; }
.add {
  flex: none;
  width: 38px; height: 38px; display: grid; place-items: center;
  border: 1px dashed var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1); color: var(--ink-muted); cursor: pointer;
}
.add:hover { border-color: var(--primary); color: var(--primary); }
</style>
