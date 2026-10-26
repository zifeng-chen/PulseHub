<script setup lang="ts">
/**
 * 不动产页面 /ledger/assets/real-estates（06 §2、15 §3）。
 * 详情页签：概览、产权与基础资料、房贷、估值历史、租赁、水电煤户号、费用流水、文件、AI 分析。
 */
import { computed, watch } from 'vue'
import {
  Building2, HandCoins, Layers, Plus, RefreshCw, Search, Sparkles,
} from 'lucide-vue-next'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppToolbar from '@/components/ui/AppToolbar.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StatCard from '@/components/ui/StatCard.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import MoneyText from '@/components/ui/MoneyText.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import AssetAttachments from '@/components/asset/AssetAttachments.vue'
import AssetBaseFields from '@/components/asset/AssetBaseFields.vue'
import AssetCard from '@/components/asset/AssetCard.vue'
import AssetDetailDrawer from '@/components/asset/AssetDetailDrawer.vue'
import ResidenceFields from '@/components/asset/ResidenceFields.vue'
import { ASSET_STATUS_VALUES, ASSET_TABS } from './asset-shared'
import { useAssetPage } from './use-asset-page'
import { zh } from '@/i18n'

const {
  loading, error, rows, summary, members, accounts,
  keyword, memberFilter, statusFilter,
  editorOpen, editorMode, saving, fieldErrors, form, extForm,
  detailOpen, detailId, canCreate, canEdit, utilities, pendingFiles,
  load, openCreate, openEdit, save, openDetail,
} = useAssetPage({
  assetType: 'real_estate',
  endpoint: '/ledger/assets/real-estates',
  extDefaults: () => ({
    property_type: 'residence', unit_price: '', management_fee_price: '',
  }),
})

const ui = useUiStore()

// 房产类型不再手选：分类选「车位」时记为车库，其余按住宅
watch(
  () => (form as unknown as Record<string, unknown>).category_root_name,
  () => {
    const name = String((form as unknown as Record<string, unknown>).category_root_name || '')
    if (name) extForm.property_type = name.includes('车位') ? 'garage' : 'residence'
  },
)

// 住宅表单只需要「购入价格」：新建时以它作为资产初始价值
watch(() => form.acquire_cost, (value) => {
  if (editorMode.value === 'create') form.current_value = value
})

/** 填了参考单价就按 建筑面积 × 单价 估算当前价值，否则沿用购入价格 */
watch([() => extForm.unit_price, () => extForm.building_area], ([unitPrice, area]) => {
  if (editorMode.value !== 'create') return
  const price = Number(unitPrice || 0)
  const size = Number(area || 0)
  if (price > 0 && size > 0) {
    form.current_value = (price * size).toFixed(2)
  } else {
    form.current_value = form.acquire_cost
  }
})


const statusOptions = computed(() => [
  { value: '', label: '全部状态' },
  ...ASSET_STATUS_VALUES.map((v) => ({ value: v, label: zh('asset_status', v) })),
])
const memberOptions = computed(() => [
  { value: '', label: '全部成员' },
  ...members.value.map((m) => ({ value: m.id, label: m.display_name })),
])
</script>

<template>
  <div class="assets">
    <PageHeader
      title="不动产"
      subtitle="产权资料、面积与购置成本记录在资产侧；房贷余额与还款由负债模块统一管理，不重复录入。"
      :breadcrumb="['家庭台账', '资产', '不动产']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiPanel('不动产')">
        <Sparkles :size="15" /> AI 房产分析
      </AppButton>
      <AppButton v-if="canCreate" variant="primary" @click="openCreate">
        <Plus :size="15" /> 新增不动产
      </AppButton>
    </PageHeader>

    <section v-if="summary" class="stats">
      <StatCard
        :icon="Building2" tone="primary" :label="`不动产总值（${summary.base_currency}）`"
        :value="summary.total_value" :currency="summary.base_currency"
        :hint="`共 ${summary.count} 套 · 仅统计计入净资产的`"
      />
      <StatCard
        :icon="Layers" tone="neutral" label="数量"
        :value="summary.count" plain unit="套"
        hint="当前登记在册"
      />
      <StatCard
        :icon="HandCoins" tone="warning" label="待估值"
        :value="summary.pending_valuation_count" plain unit="项"
        hint="当前价值为空或从未登记估值"
      />
    </section>

    <AppCard>
      <template #header>
        <AppToolbar>
          <div class="search">
            <Search :size="15" />
            <input v-model="keyword" placeholder="搜索房产名称" @keyup.enter="load" />
          </div>
          <AppSelect v-model="memberFilter" :options="memberOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="statusFilter" :options="statusOptions" class="sel" @update:model-value="load" />
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
      <StateBlock
        v-else-if="!rows.length" state="empty" title="还没有不动产"
        description="登记房产后可以维护产权资料、面积、水电煤户号并跟踪估值。"
      >
        <template #actions>
          <AppButton v-if="canCreate" variant="primary" @click="openCreate">
            <Plus :size="15" /> 新增不动产
          </AppButton>
        </template>
      </StateBlock>

      <div v-else class="card-grid">
        <AssetCard
          v-for="row in rows"
          :key="row.id"
          :row="row"
          :can-edit="canEdit"
          @open="openDetail(row)"
          @edit="openEdit(row)"
        />
      </div>
    </AppCard>

    <AppDrawer
      :open="editorOpen" :title="editorMode === 'create' ? '新增不动产' : '编辑不动产'" :width="560"
      @close="editorOpen = false"
    >
      <div class="form">
        <AssetBaseFields :form="form" :errors="fieldErrors" :members="members" :accounts="accounts"
                         :show-type="false" compact>
          <ResidenceFields
            :form="form" :ext-form="extForm" :errors="fieldErrors" :accounts="accounts"
            :show-utilities="editorMode === 'create'" :utilities="utilities"
            v-model:pending="pendingFiles"
          />
        </AssetBaseFields>

        <!-- 附件：新增时暂存，创建成功后统一上传；编辑时直接上传 -->
        <AssetAttachments
          v-model:pending="pendingFiles"
          :asset-id="editorMode === 'edit' ? form.id : ''"
          :disabled="editorMode === 'edit' && !canEdit"
          hint="可上传房产证、购房合同、发票、房屋照片等"
        />

        <AppField
          v-if="editorMode === 'edit'" label="调整原因"
          hint="修改当前价值时必须填写，会写入估值时间线" :error="fieldErrors.reason"
        >
          <AppInput v-model="form.reason" placeholder="仅在调整当前价值时必填" />
        </AppField>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">
          {{ editorMode === 'create' ? '创建不动产' : '保存修改' }}
        </AppButton>
      </template>
    </AppDrawer>

    <AssetDetailDrawer
      :open="detailOpen" :asset-id="detailId" asset-type="real_estate"
      :tabs="ASSET_TABS.real_estate" :accounts="accounts"
      @close="detailOpen = false" @changed="load"
    />
  </div>
</template>

<style scoped>
.assets { display: flex; flex-direction: column; gap: var(--space-4); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(196px, 1fr)); gap: var(--space-3); }
.stat .k { font-size: 12.5px; color: var(--ink-muted); }
.stat .n { font-size: 22px; font-weight: 600; color: var(--ink-strong); }
.toolbar { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; width: 100%; }
.search {
  display: flex; align-items: center; gap: 6px; flex: 1; min-width: 180px; height: 34px;
  padding: 0 10px; border: 1px solid var(--border); border-radius: var(--glass-radius-control);
  background: var(--glass-1); color: var(--ink-muted);
}
.search input { flex: 1; border: none; background: transparent; color: var(--ink-strong); font-size: 13.5px; outline: none; }
.sel { width: 150px; }
.row-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.act {
  display: inline-flex; align-items: center; gap: 4px; height: 28px; padding: 0 10px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--glass-1);
  color: var(--ink-strong); font-size: 12.5px; cursor: pointer;
}
.act:hover { border-color: var(--primary); color: var(--primary); }
.form { display: flex; flex-direction: column; gap: var(--space-4); }
.sec { font-size: 13.5px; color: var(--ink-strong); border-top: 1px solid var(--border); padding-top: var(--space-3); }
@media (max-width: 700px) { .sel { width: 100%; } }

/* 移动端：统计卡两列排布，别再一人一行把首屏占满 */
@media (max-width: 640px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .stat :deep(.card-body) { padding: 11px 12px 12px; }
  .stat .k { font-size: 11.5px; }
  .stat .n { font-size: 19px; }
  .stat :deep(.money) { font-size: 17px; }
}

</style>
