<script setup lang="ts">
/**
 * 车位页面 /ledger/assets/parking-spaces（06 §2、15 §4）。
 * 一个车位最多关联一套不动产；一套不动产可以关联多个车位。
 */
import { computed } from 'vue'
import {
  HandCoins, Layers, Plus, RefreshCw, Search, Sparkles, SquareParking,
} from 'lucide-vue-next'
import { zh } from '@/i18n'
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
import ParkingFields from '@/components/asset/ParkingFields.vue'
import AssetDetailDrawer from '@/components/asset/AssetDetailDrawer.vue'
import { ASSET_STATUS_VALUES, ASSET_TABS } from './asset-shared'
import { useAssetPage } from './use-asset-page'

const {
  loading, error, rows, summary, members, accounts, linkTargets,
  keyword, memberFilter, statusFilter,
  editorOpen, editorMode, saving, fieldErrors, form, extForm,
  detailOpen, detailId, canCreate, canEdit, pendingFiles,
  load, openCreate, openEdit, save, openDetail,
} = useAssetPage({
  assetType: 'parking',
  endpoint: '/ledger/assets/parking-spaces',
  extDefaults: () => ({ parking_type: 'ownership' }),
})

const ui = useUiStore()


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
      title="车位"
      subtitle="车位净资产独立计入；车位贷款需关联独立负债，不会并入房贷本金。"
      :breadcrumb="['家庭台账', '资产', '车位']"
    >
      <AppButton variant="secondary" :loading="loading" @click="load">
        <RefreshCw :size="15" /> 刷新
      </AppButton>
      <AppButton variant="secondary" @click="ui.openAiPanel('车位')">
        <Sparkles :size="15" /> AI 分析
      </AppButton>
      <AppButton v-if="canCreate" variant="primary" @click="openCreate">
        <Plus :size="15" /> 新增车位
      </AppButton>
    </PageHeader>

    <section v-if="summary" class="stats">
      <StatCard
        :icon="SquareParking" tone="ai" :label="`车位总值（${summary.base_currency}）`"
        :value="summary.total_value" :currency="summary.base_currency"
        :hint="`共 ${summary.count} 个 · 仅统计计入净资产的`"
      />
      <StatCard
        :icon="Layers" tone="neutral" label="数量"
        :value="summary.count" plain unit="个"
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
            <input v-model="keyword" placeholder="搜索车位名称 / 编号" @keyup.enter="load" />
          </div>
          <AppSelect v-model="memberFilter" :options="memberOptions" class="sel" @update:model-value="load" />
          <AppSelect v-model="statusFilter" :options="statusOptions" class="sel" @update:model-value="load" />
        </AppToolbar>
      </template>

      <StateBlock v-if="loading" state="loading" />
      <StateBlock v-else-if="error" state="error" :description="error" retryable @retry="load" />
      <StateBlock
        v-else-if="!rows.length" state="empty" title="还没有车位"
        description="登记车位后可以关联到具体不动产，管理费与估值独立跟踪。"
      >
        <template #actions>
          <AppButton v-if="canCreate" variant="primary" @click="openCreate">
            <Plus :size="15" /> 新增车位
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
      :open="editorOpen" :title="editorMode === 'create' ? '新增车位' : '编辑车位'" :width="560"
      @close="editorOpen = false"
    >
      <div class="form">
        <AssetBaseFields :form="form" :errors="fieldErrors" :members="members" :accounts="accounts" :show-type="false" />
        <ParkingFields :form="extForm" :errors="fieldErrors" :real-estates="linkTargets" />

        <AssetAttachments
          v-model:pending="pendingFiles"
          :asset-id="editorMode === 'edit' ? form.id : ''"
          :disabled="editorMode === 'edit' && !canEdit"
          hint="可上传车位产权证、合同、发票等"
        />
        <AppField
          v-if="editorMode === 'edit'" label="调整原因"
          hint="修改当前价值时必须填写，会写入估值时间线" :error="fieldErrors.reason"
        >
          <AppInput v-model="form.reason" placeholder="仅在调整当前价值时必填" />
        </AppField>
        <template v-if="editorMode === 'create'">
          <h4 class="sec">购置事件（可选）</h4>
          <AppField label="登记方式" hint="草稿不计入账户余额，确认后才生成资金变动">
            <AppSelect
              v-model="form.acquisition_mode"
              :options="[
                { value: 'none', label: '仅登记资产（补录，不生成资金事件）' },
                { value: 'draft', label: '保存为购置草稿' },
                { value: 'confirm', label: '直接确认购置' },
              ]"
            />
          </AppField>
          <template v-if="form.acquisition_mode !== 'none'">
            <AppField label="资金来源账户" required :error="fieldErrors['acquisition.account_id']">
              <AppSelect
                v-model="form.acquisition_account"
                :options="[{ value: '', label: '请选择账户' }, ...accounts.map((a) => ({ value: a.id, label: `${a.name}（${a.currency}）` }))]"
              />
            </AppField>
            <AppField label="购置金额" :error="fieldErrors['acquisition.amount']" hint="留空则使用购置成本">
              <AppInput v-model="form.acquisition_amount" type="number" placeholder="0.00" />
            </AppField>
          </template>
        </template>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="editorOpen = false">取消</AppButton>
        <AppButton variant="primary" :loading="saving" @click="save">
          {{ editorMode === 'create' ? '创建车位' : '保存修改' }}
        </AppButton>
      </template>
    </AppDrawer>

    <AssetDetailDrawer
      :open="detailOpen" :asset-id="detailId" asset-type="parking"
      :tabs="ASSET_TABS.parking" :accounts="accounts"
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
