<script setup lang="ts">
/**
 * 家庭台账未交付模块的统一占位页。
 * M1 已交付的账户与流水各有独立页面（AccountsView / TransactionsView）；
 * 其余模块在此显示「模块未启用 / 数据不可用」，不创建假数据（18 §7）。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Sparkles } from 'lucide-vue-next'
import { useUiStore } from '@/stores/ui'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import StateBlock from '@/components/ui/StateBlock.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const pendingModule = computed(() => (route.meta.pendingModule as string) || '后续模块')
</script>

<template>
  <div class="module">
    <PageHeader
      :title="(route.meta.title as string)"
      :breadcrumb="(route.meta.breadcrumb as string[]) || []"
    />
    <AppCard>
      <StateBlock
        state="pending"
        :title="`${pendingModule}尚未启用`"
        description="该页面的数据表、接口与关联关系将在对应模块交付时一并提供，当前不创建任何假数据。"
        hint="基座与 M1 已预留关联字段：household_id、account_id、asset_id、liability_id、investment_account_id、lease_id、category_id、event_id 等。"
      >
        <template #actions>
          <AppButton variant="secondary" @click="router.push('/ledger/transactions')">
            查看已启用的流水
          </AppButton>
          <AppButton variant="secondary" @click="router.push('/ledger/accounts')">
            查看账户
          </AppButton>
          <AppButton variant="ghost" @click="ui.openAiPanel((route.meta.title as string))">
            <Sparkles :size="15" /> 询问 AI
          </AppButton>
        </template>
      </StateBlock>
    </AppCard>
  </div>
</template>

<style scoped>
.module { display: flex; flex-direction: column; gap: var(--space-4); }
</style>
