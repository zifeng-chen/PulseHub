<script setup lang="ts">
/**
 * 首次启动引导 /setup（04 §3）。
 * 六步：数据库连接 → 管理员 → 家庭基础信息 → 财务口径 → 接入配置 → 完成页。
 * 步骤可回退，不可跳过数据库与管理员步骤；每步独立保存，某项失败不阻塞其他项。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Check, ArrowRight, ArrowLeft, Plug, RefreshCw } from 'lucide-vue-next'
import { api, errorFields, errorMessage } from '@/api/client'
import { useSystemStore } from '@/stores/system'
import { useAuthStore } from '@/stores/auth'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppField from '@/components/ui/AppField.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

const router = useRouter()
const system = useSystemStore()
const auth = useAuthStore()

const steps = [
  { key: 'database', title: '数据库连接', desc: '配置 MySQL 连接，系统会创建或校验迁移表，不导入任何旧项目表结构。' },
  { key: 'admin', title: '创建管理员', desc: '创建首个系统管理员账号。密码使用服务端强哈希保存，不可逆。' },
  { key: 'household', title: '家庭基础信息', desc: '创建家庭并生成默认分类。' },
  { key: 'finance', title: '财务口径', desc: '确定统计口径。每个选项都有解释与恢复默认。' },
  { key: 'providers', title: '接入配置', desc: '本地 AI、百度 OCR、第三方 AI、通知渠道。可跳过，之后可在设置中配置。' },
  { key: 'finish', title: '完成', desc: '确认摘要并进入登录。' },
]

const current = ref(0)
const submitting = ref(false)
const message = ref('')
const errorMsg = ref('')
const fieldErrors = reactive<Record<string, string>>({})
const testing = reactive<Record<string, boolean>>({})
const testResults = reactive<Record<string, { ok: boolean; text: string }>>({})

const database = reactive({
  host: '127.0.0.1',
  port: 3306,
  database_name: 'fam',
  username: 'root',
  password: '',
  ssl_enabled: false,
  connect_timeout: 10,
  use_embedded_dev_database: false,
})

const admin = reactive({
  display_name: '',
  username: '',
  password: '',
  confirm_password: '',
  recovery_email: '',
  recovery_hint: '',
})

const household = reactive({
  name: '',
  base_currency: 'CNY',
  timezone: 'Asia/Shanghai',
  description: '',
  is_multi_member: true,
})

const finance = reactive({
  asset_purchase_separate: true,
  investment_valuation_in_net_worth: true,
  default_period: 'current_month',
  bill_display_mode: 'day_of_month',
  default_date_range: 'last_6_months',
  amount_precision: 2,
})

const localAi = reactive({ protocol: 'openai_compatible', base_url: '', model: '', api_key: '', enabled: false })
const ocr = reactive({ api_key: '', secret_key: '', region: 'general', enabled: false })
const notification = reactive({ token: '', enabled: false })
const thirdParty = reactive({ name: '', interface_type: 'openai_compatible', base_url: '', api_key: '', default_model: '' })

const strength = ref<{ score: number; label: string; warnings: string[] } | null>(null)
const summary = ref<Record<string, unknown> | null>(null)

const progress = computed(() => ((current.value + 1) / steps.length) * 100)
const canGoBack = computed(() => current.value > 0)
const providerStates = computed(() => (summary.value?.providers as Record<string, string>) || {})

function clearFeedback() {
  message.value = ''
  errorMsg.value = ''
  Object.keys(fieldErrors).forEach((k) => delete fieldErrors[k])
}

async function call<T>(label: string, fn: () => Promise<T>, nextStep = true) {
  clearFeedback()
  submitting.value = true
  try {
    const res = await fn()
    message.value = label
    if (nextStep) current.value = Math.min(current.value + 1, steps.length - 1)
    return res
  } catch (err) {
    errorMsg.value = errorMessage(err)
    Object.assign(fieldErrors, errorFields(err))
    return null
  } finally {
    submitting.value = false
  }
}

function saveDatabase() {
  return call('数据库配置已保存', () =>
    api.post('/setup/database', {
      host: database.host,
      port: Number(database.port),
      database_name: database.database_name,
      username: database.username,
      password: database.password,
      ssl_enabled: database.ssl_enabled,
      connect_timeout: Number(database.connect_timeout),
      use_embedded_dev_database: database.use_embedded_dev_database,
    }),
  )
}

async function testDatabase() {
  clearFeedback()
  testing.database = true
  testResults.database = undefined as never
  try {
    const res = await api.post<{
      ok: boolean
      latency_ms: number
      mysql_version?: string
      can_create_table?: boolean
      message: string
    }>('/setup/database/test', {
      host: database.host,
      port: Number(database.port),
      database_name: database.database_name,
      username: database.username,
      password: database.password,
      connect_timeout: Number(database.connect_timeout),
    })
    testResults.database = {
      ok: res.ok,
      text: res.ok
        ? `延迟 ${res.latency_ms} ms · MySQL ${res.mysql_version} · ${res.can_create_table ? '具备建表权限' : '缺少建表权限'}`
        : res.message,
    }
  } catch (err) {
    testResults.database = { ok: false, text: errorMessage(err) }
  } finally {
    testing.database = false
  }
}

async function checkStrength() {
  if (!admin.password) {
    strength.value = null
    return
  }
  try {
    strength.value = await api.get('/setup/password-strength', { password: admin.password })
  } catch {
    strength.value = null
  }
}

function saveAdmin() {
  return call('管理员创建成功', () =>
    api.post('/setup/admin', {
      display_name: admin.display_name,
      username: admin.username,
      password: admin.password,
      confirm_password: admin.confirm_password,
      recovery_email: admin.recovery_email,
      recovery_hint: admin.recovery_hint,
    }),
  )
}

function saveHousehold() {
  return call('家庭创建成功，已生成默认分类', () =>
    api.post('/setup/household', {
      name: household.name,
      base_currency: household.base_currency,
      timezone: household.timezone,
      description: household.description,
      is_multi_member: household.is_multi_member,
    }),
  )
}

function saveFinance() {
  return call('财务口径已保存', () =>
    api.post('/setup/finance', {
      asset_purchase_separate: finance.asset_purchase_separate,
      investment_valuation_in_net_worth: finance.investment_valuation_in_net_worth,
      default_period: finance.default_period,
      bill_display_mode: finance.bill_display_mode,
      default_date_range: finance.default_date_range,
      amount_precision: Number(finance.amount_precision),
    }),
  )
}

async function saveProvider(key: string, url: string, body: Record<string, unknown>) {
  clearFeedback()
  testing[key] = true
  try {
    await api.post(url, body)
    testResults[key] = { ok: true, text: '已保存（密钥已加密存储）' }
  } catch (err) {
    testResults[key] = { ok: false, text: errorMessage(err) }
  } finally {
    testing[key] = false
  }
}

async function testProvider(key: string, adapterKey: string) {
  clearFeedback()
  testing[key] = true
  try {
    const res = await api.post<{ status_label: string; detail: string }>(`/ai/providers/${adapterKey}/test`)
    testResults[key] = { ok: true, text: `${res.status_label}：${res.detail}` }
  } catch (err) {
    testResults[key] = { ok: false, text: errorMessage(err) }
  } finally {
    testing[key] = false
  }
}

async function loadSummary() {
  try {
    summary.value = await api.get('/setup/summary')
  } catch {
    summary.value = null
  }
}

async function goProvidersNext() {
  await loadSummary()
  current.value = 5
}

function resetFinance() {
  finance.asset_purchase_separate = true
  finance.investment_valuation_in_net_worth = true
  finance.default_period = 'current_month'
  finance.bill_display_mode = 'day_of_month'
  finance.default_date_range = 'last_6_months'
  finance.amount_precision = 2
  message.value = '已恢复默认财务口径'
}

async function finish() {
  const res = await call('系统初始化完成，公开初始化接口已关闭', () => api.post('/setup/complete'))
  if (!res) return
  await loadSummary()
  system.reset()
  await system.loadStatus()
  auth.clear()
  router.push('/login')
}

function goSetup() {
  router.push('/setup/check')
}

onMounted(async () => {
  await system.loadStatus()
  if (system.initialized) {
    router.push('/login')
    return
  }
  // 本地开发环境默认使用内嵌开发数据库；生产必须配置 MySQL
  database.use_embedded_dev_database = false
})

const periodOptions = [
  { value: 'current_month', label: '本月' },
  { value: 'last_month', label: '上月' },
  { value: 'last_3_months', label: '近三个月' },
  { value: 'last_6_months', label: '近六个月' },
  { value: 'current_year', label: '本年' },
]
</script>

<template>
  <div class="wizard">
    <header class="wz-head">
      <div class="brand" @click="goSetup">
        <img src="/logo.png" alt="财资脉枢" class="logo" />
        <div>
          <h1>首次启动引导</h1>
          <p>财资脉枢 · PulseHub · 本地优先部署</p>
        </div>
      </div>
      <StatusBadge label="系统尚未初始化" tone="warning" />
    </header>

    <div class="wz-progress" role="progressbar" :aria-valuenow="Math.round(progress)" aria-valuemin="0" aria-valuemax="100">
      <span :style="{ width: `${progress}%` }" />
    </div>

    <div class="wz-body">
      <nav class="steps-nav" aria-label="引导步骤">
        <button
          v-for="(s, i) in steps"
          :key="s.key"
          class="step-item"
          :class="{ active: i === current, done: i < current }"
          type="button"
          :disabled="i > current"
          @click="i <= current && (current = i)"
        >
          <span class="idx"><Check v-if="i < current" :size="13" /><template v-else>{{ i + 1 }}</template></span>
          <span class="txt">{{ s.title }}</span>
        </button>
      </nav>

      <AppCard class="wz-card" :title="steps[current].title">
        <p class="step-desc">{{ steps[current].desc }}</p>

        <!-- 步骤一：数据库连接 -->
        <div v-if="steps[current].key === 'database'" class="grid">
          <AppField label="数据库类型" for-id="db-type">
            <AppInput id="db-type" model-value="MySQL 8.x（固定）" disabled />
          </AppField>
          <div />
          <AppField label="主机" required :error="fieldErrors.host" for-id="db-host">
            <AppInput id="db-host" v-model="database.host" placeholder="127.0.0.1" />
          </AppField>
          <AppField label="端口" required :error="fieldErrors.port" for-id="db-port">
            <AppInput id="db-port" v-model="database.port" type="number" />
          </AppField>
          <AppField label="数据库名" required :error="fieldErrors.database_name" for-id="db-name">
            <AppInput id="db-name" v-model="database.database_name" placeholder="fam" />
          </AppField>
          <AppField label="用户名" required :error="fieldErrors.username" for-id="db-user">
            <AppInput id="db-user" v-model="database.username" />
          </AppField>
          <AppField label="密码" :error="fieldErrors.password" for-id="db-pass">
            <AppInput id="db-pass" v-model="database.password" sensitive placeholder="仅加密保存，不写入日志" />
          </AppField>
          <AppField label="连接超时（秒）" for-id="db-timeout">
            <AppInput id="db-timeout" v-model="database.connect_timeout" type="number" />
          </AppField>
          <AppField label="SSL">
            <AppSwitch v-model="database.ssl_enabled" label="启用 SSL 连接" />
          </AppField>
          <div class="full dev-note">
            <p class="note-title">本地开发环境可选：使用内嵌开发数据库</p>
            <p class="note-body">
              生产环境固定使用 MySQL。若当前仅用于本地联调且本机没有 MySQL，
              可启用内嵌开发数据库作为运行底座（该选项在生产环境不可用）。
            </p>
            <AppSwitch v-model="database.use_embedded_dev_database" label="使用内嵌开发数据库（仅开发环境）" />
          </div>
        </div>

        <!-- 步骤二：创建管理员 -->
        <div v-else-if="steps[current].key === 'admin'" class="grid">
          <AppField label="管理员名称" required :error="fieldErrors.display_name" for-id="ad-name">
            <AppInput id="ad-name" v-model="admin.display_name" placeholder="例如：张三" />
          </AppField>
          <AppField label="登录账号" required :error="fieldErrors.username" for-id="ad-user">
            <AppInput id="ad-user" v-model="admin.username" placeholder="用于登录的账号" />
          </AppField>
          <AppField label="密码" required :error="fieldErrors.password" for-id="ad-pass">
            <AppInput id="ad-pass" v-model="admin.password" sensitive placeholder="至少 8 位" @enter="checkStrength" />
          </AppField>
          <AppField label="确认密码" required :error="fieldErrors.confirm_password" for-id="ad-pass2">
            <AppInput id="ad-pass2" v-model="admin.confirm_password" sensitive />
          </AppField>
          <div v-if="strength" class="full strength">
            <div class="bars">
              <span v-for="i in 4" :key="i" :class="{ on: strength.score >= i, weak: strength.score < 2 }" />
            </div>
            <p class="label">密码强度：{{ strength.label }}</p>
            <ul v-if="strength.warnings.length" class="warns">
              <li v-for="w in strength.warnings" :key="w">{{ w }}</li>
            </ul>
          </div>
          <AppField label="恢复邮箱（可选）" :error="fieldErrors.recovery_email" for-id="ad-mail">
            <AppInput id="ad-mail" v-model="admin.recovery_email" type="email" />
          </AppField>
          <AppField label="恢复提示（可选）" for-id="ad-hint">
            <AppInput id="ad-hint" v-model="admin.recovery_hint" placeholder="例如：母亲生日组成的提示" />
          </AppField>
          <p class="full tip">
            创建后系统管理员为不可逆角色；忘记密码时可在本机执行管理员恢复流程，不会伪造外部找回能力。
          </p>
        </div>

        <!-- 步骤三：家庭基础信息 -->
        <div v-else-if="steps[current].key === 'household'" class="grid">
          <AppField label="家庭名称" required :error="fieldErrors.name" for-id="hh-name">
            <AppInput id="hh-name" v-model="household.name" placeholder="例如：我们的家" />
          </AppField>
          <AppField label="默认时区" for-id="hh-tz">
            <AppSelect id="hh-tz" v-model="household.timezone" :options="[
              { value: 'Asia/Shanghai', label: 'Asia/Shanghai（中国标准时间）' },
              { value: 'Asia/Hong_Kong', label: 'Asia/Hong_Kong' },
              { value: 'UTC', label: 'UTC' },
            ]" />
          </AppField>
          <AppField label="基础币种" for-id="hh-ccy">
            <AppSelect id="hh-ccy" v-model="household.base_currency" :options="[
              { value: 'CNY', label: '人民币 CNY' },
              { value: 'USD', label: '美元 USD' },
              { value: 'HKD', label: '港币 HKD' },
            ]" />
          </AppField>
          <AppField label="启用多成员">
            <AppSwitch v-model="household.is_multi_member" label="允许添加其他家庭成员" />
          </AppField>
          <AppField label="家庭描述（可选）" for-id="hh-desc">
            <AppInput id="hh-desc" v-model="household.description" />
          </AppField>
          <p class="full tip">创建后将自动生成默认家庭、系统分类与成员角色（所有者 / 管理员 / 成员 / 只读）。</p>
        </div>

        <!-- 步骤四：财务口径 -->
        <div v-else-if="steps[current].key === 'finance'" class="stack">
          <div class="opt-row">
            <div>
              <strong>资产购置独立于消费展示</strong>
              <p>开启后，购置资产形成的支出不会重复计入消费统计。</p>
            </div>
            <AppSwitch v-model="finance.asset_purchase_separate" />
          </div>
          <div class="opt-row">
            <div>
              <strong>投资估值纳入净资产</strong>
              <p>关闭后，净资产仅统计可计入资产的当前价值与负债。</p>
            </div>
            <AppSwitch v-model="finance.investment_valuation_in_net_worth" />
          </div>
          <div class="opt-row">
            <div><strong>默认统计周期</strong><p>脉枢台与脉诊打开时使用的默认期间。</p></div>
            <AppSelect v-model="finance.default_period" :options="periodOptions" class="w220" />
          </div>
          <div class="opt-row">
            <div><strong>账单日 / 还款日显示方式</strong><p>按每月固定日期或按到期前天数显示。</p></div>
            <AppSelect v-model="finance.bill_display_mode" :options="[
              { value: 'day_of_month', label: '每月固定日期' },
              { value: 'days_before_due', label: '到期前天数' },
            ]" class="w220" />
          </div>
          <div class="opt-row">
            <div><strong>默认日期范围</strong><p>列表与图表的默认查询范围。</p></div>
            <AppSelect v-model="finance.default_date_range" :options="periodOptions" class="w220" />
          </div>
          <div class="opt-row">
            <div><strong>金额精度展示</strong><p>底层金额固定 DECIMAL(20,4)，此处控制全平台金额显示的小数位（默认 2 位，0–4）。</p></div>
            <AppInput v-model="finance.amount_precision" type="number" min="0" max="4" class="w220" />
          </div>
          <div class="reset-row"><AppButton variant="ghost" size="sm" @click="resetFinance">恢复默认</AppButton></div>
        </div>

        <!-- 步骤五：接入配置 -->
        <div v-else-if="steps[current].key === 'providers'" class="providers">
          <div class="provider-card">
            <header>
              <h4>本地 AI</h4>
              <StatusBadge
                :label="testResults.local_ai?.ok ? '已保存' : '未配置'"
                :tone="testResults.local_ai?.ok ? 'success' : 'neutral'"
              />
            </header>
            <p class="pdesc">默认不连接云端。可跳过，之后在设置 / 本地 AI 中配置。</p>
            <div class="grid">
              <AppField label="协议">
                <AppSelect v-model="localAi.protocol" :options="[
                  { value: 'openai_compatible', label: 'OpenAI 兼容接口' },
                  { value: 'ollama', label: 'Ollama' },
                ]" />
              </AppField>
              <AppField label="服务地址">
                <AppInput v-model="localAi.base_url" placeholder="http://127.0.0.1:11434/v1" />
              </AppField>
              <AppField label="模型名">
                <AppInput v-model="localAi.model" placeholder="例如：qwen2.5:7b" />
              </AppField>
              <AppField label="API Key（如需要）">
                <AppInput v-model="localAi.api_key" sensitive />
              </AppField>
            </div>
            <div class="pfoot">
              <AppButton size="sm" variant="secondary" :loading="testing.local_ai" @click="testProvider('local_ai', 'local_ai')">
                <Plug :size="14" /> 连接测试
              </AppButton>
              <AppButton size="sm" variant="primary" @click="saveProvider('local_ai', '/setup/local-ai', { ...localAi, enabled: Boolean(localAi.base_url) })">
                保存
              </AppButton>
            </div>
            <p v-if="testResults.local_ai" class="presult" :class="{ bad: !testResults.local_ai.ok }">
              {{ testResults.local_ai.text }}
            </p>
          </div>

          <div class="provider-card">
            <header>
              <h4>百度 OCR</h4>
              <StatusBadge :label="testResults.ocr?.ok ? '已保存' : '未配置'" :tone="testResults.ocr?.ok ? 'success' : 'neutral'" />
            </header>
            <p class="pdesc">凭证掩码保存；OCR 失败时可手工录入，不影响手工记账。</p>
            <div class="grid">
              <AppField label="API Key"><AppInput v-model="ocr.api_key" sensitive /></AppField>
              <AppField label="Secret Key"><AppInput v-model="ocr.secret_key" sensitive /></AppField>
              <AppField label="区域 / 接口"><AppInput v-model="ocr.region" placeholder="general" /></AppField>
            </div>
            <div class="pfoot">
              <AppButton size="sm" variant="secondary" :loading="testing.ocr" @click="testProvider('ocr', 'ocr')">
                <Plug :size="14" /> 测试识别
              </AppButton>
              <AppButton size="sm" variant="primary" @click="saveProvider('ocr', '/setup/ocr', { ...ocr, enabled: Boolean(ocr.api_key) })">
                保存
              </AppButton>
            </div>
            <p v-if="testResults.ocr" class="presult" :class="{ bad: !testResults.ocr.ok }">{{ testResults.ocr.text }}</p>
          </div>

          <div class="provider-card">
            <header>
              <h4>第三方 AI API</h4>
              <StatusBadge label="默认关闭" tone="warning" />
            </header>
            <p class="pdesc">
              与本地 AI 分开管理；默认全部关闭。外发前必须展示外发字段预览并写入审计，
              本地 AI 失败时不会静默切换。
            </p>
            <div class="grid">
              <AppField label="提供商名称"><AppInput v-model="thirdParty.name" /></AppField>
              <AppField label="接口类型">
                <AppSelect v-model="thirdParty.interface_type" :options="[
                  { value: 'openai_compatible', label: 'OpenAI 兼容接口' },
                  { value: 'enterprise', label: '企业内部兼容接口' },
                  { value: 'self_hosted', label: '自部署兼容服务' },
                ]" />
              </AppField>
              <AppField label="Base URL"><AppInput v-model="thirdParty.base_url" /></AppField>
              <AppField label="API Key"><AppInput v-model="thirdParty.api_key" sensitive /></AppField>
              <AppField label="默认模型"><AppInput v-model="thirdParty.default_model" /></AppField>
            </div>
            <div class="pfoot">
              <AppButton size="sm" variant="secondary" :loading="testing.third_party" @click="testProvider('third_party', 'third_party_ai')">
                <Plug :size="14" /> 连接测试
              </AppButton>
              <AppButton size="sm" variant="primary" @click="saveProvider('third_party', '/setup/third-party-ai', thirdParty)">
                保存
              </AppButton>
            </div>
            <p v-if="testResults.third_party" class="presult" :class="{ bad: !testResults.third_party.ok }">
              {{ testResults.third_party.text }}
            </p>
          </div>

          <div class="provider-card">
            <header>
              <h4>通知渠道（PushPlus）</h4>
              <StatusBadge :label="testResults.notification?.ok ? '已保存' : '未配置'" :tone="testResults.notification?.ok ? 'success' : 'neutral'" />
            </header>
            <p class="pdesc">通知不是后台任务：打开提醒页实时计算到期状态，用户确认后同步发送。</p>
            <div class="grid">
              <AppField label="PushPlus Token">
                <AppInput v-model="notification.token" sensitive />
              </AppField>
              <AppField label="启用">
                <AppSwitch v-model="notification.enabled" label="启用 PushPlus 通知" />
              </AppField>
            </div>
            <div class="pfoot">
              <AppButton size="sm" variant="secondary" :loading="testing.notification" @click="testProvider('notification', 'notification')">
                <Plug :size="14" /> 测试发送
              </AppButton>
              <AppButton size="sm" variant="primary" @click="saveProvider('notification', '/setup/notification', notification)">
                保存
              </AppButton>
            </div>
            <p v-if="testResults.notification" class="presult" :class="{ bad: !testResults.notification.ok }">
              {{ testResults.notification.text }}
            </p>
          </div>
          <p class="tip">每张卡片独立保存，某项失败不会阻塞其他项。全部可跳过。</p>
        </div>

        <!-- 步骤六：完成 -->
        <div v-else class="finish">
          <ul class="summary">
            <!--
              完成页优先用向导里填过的值：初始化完成后 /setup/summary 对匿名请求
              不再返回管理员与家庭名（避免公开页泄露身份），而这一步还没有登录态。
            -->
            <li>
              <span class="k">管理员</span>
              <span class="v">{{ admin.display_name || (summary?.admin as any)?.display_name || '—' }}（{{ admin.username || (summary?.admin as any)?.username || '—' }}）</span>
            </li>
            <li>
              <span class="k">家庭</span>
              <span class="v">{{ household.name || (summary?.household as any)?.name || '—' }}</span>
            </li>
            <li>
              <span class="k">基础币种 / 时区</span>
              <span class="v">{{ household.base_currency || (summary?.household as any)?.base_currency || '—' }} / {{ household.timezone || (summary?.household as any)?.timezone || '—' }}</span>
            </li>
            <li>
              <span class="k">本地 AI</span>
              <span class="v">{{ providerStates.local_ai_label || '未配置' }}</span>
            </li>
            <li>
              <span class="k">百度 OCR</span>
              <span class="v">{{ providerStates.ocr_label || '未配置' }}</span>
            </li>
            <li>
              <span class="k">第三方 AI</span>
              <span class="v">{{ providerStates.third_party_ai_label || '未配置' }}</span>
            </li>
            <li>
              <span class="k">通知渠道</span>
              <span class="v">{{ providerStates.notification_label || '未配置' }}</span>
            </li>
          </ul>
          <p class="tip">
            完成后公开初始化接口将关闭，进入登录页。首次登录后会显示一次欢迎引导，不会重复进入初始化。
          </p>
        </div>

        <p v-if="message" class="okmsg">{{ message }}</p>
        <p v-if="errorMsg" class="errmsg" role="alert">{{ errorMsg }}</p>

        <template #footer>
          <div class="wz-foot">
            <AppButton v-if="canGoBack" variant="ghost" @click="current -= 1">
              <ArrowLeft :size="15" /> 上一步
            </AppButton>
            <span class="spacer" />
            <AppButton
              v-if="steps[current].key === 'database'"
              variant="secondary"
              :loading="testing.database"
              @click="testDatabase"
            >
              <RefreshCw :size="15" /> 测试连接
            </AppButton>
            <AppButton
              v-if="steps[current].key === 'database'"
              variant="primary"
              :loading="submitting"
              @click="saveDatabase"
            >
              保存并下一步 <ArrowRight :size="15" />
            </AppButton>

            <AppButton v-else-if="steps[current].key === 'admin'" variant="primary" :loading="submitting" @click="saveAdmin">
              创建管理员 <ArrowRight :size="15" />
            </AppButton>
            <AppButton v-else-if="steps[current].key === 'household'" variant="primary" :loading="submitting" @click="saveHousehold">
              创建家庭 <ArrowRight :size="15" />
            </AppButton>
            <AppButton v-else-if="steps[current].key === 'finance'" variant="primary" :loading="submitting" @click="saveFinance">
              保存口径 <ArrowRight :size="15" />
            </AppButton>
            <AppButton v-else-if="steps[current].key === 'providers'" variant="primary" :loading="submitting" @click="goProvidersNext">
              继续 <ArrowRight :size="15" />
            </AppButton>
            <AppButton v-else variant="primary" :loading="submitting" @click="finish">
              完成初始化 <ArrowRight :size="15" />
            </AppButton>
          </div>
        </template>
      </AppCard>
    </div>

    <p v-if="testResults.database" class="db-result" :class="{ bad: !testResults.database.ok }">
      {{ testResults.database.text }}
    </p>
  </div>
</template>

<style scoped>
.wizard {
  min-height: 100vh;
  padding: calc(var(--space-6) + var(--safe-top)) var(--space-5) var(--space-6);
  max-width: 1180px;
  margin: 0 auto;
}
.wz-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: var(--space-3); cursor: pointer; }
.logo {
  width: 44px; height: 44px; object-fit: contain;
  filter: drop-shadow(0 6px 16px rgba(53, 109, 255, 0.3));
}
.brand h1 { font-size: 19px; }
.brand p { font-size: 12.5px; color: var(--ink-muted); }

.wz-progress {
  height: 3px; margin: var(--space-5) 0;
  background: var(--border); border-radius: 3px; overflow: hidden;
}
.wz-progress span { display: block; height: 100%; background: linear-gradient(90deg, var(--primary), var(--accent-ai)); transition: width var(--motion-base) var(--ease-out); }

.wz-body { display: grid; grid-template-columns: 236px minmax(0, 1fr); gap: var(--space-5); align-items: start; }
.steps-nav { display: flex; flex-direction: column; gap: 4px; padding: var(--space-3); border-radius: var(--glass-radius-card); background: var(--glass-1); border: var(--glass-border-line); }
.step-item {
  display: flex; align-items: center; gap: 10px;
  height: 42px; padding: 0 10px; border: none; border-radius: 10px;
  background: transparent; color: var(--ink-muted); font-size: 13.5px; cursor: pointer; text-align: left;
}
.step-item:disabled { cursor: not-allowed; opacity: 0.7; }
.step-item.active { background: var(--nav-active-bg); color: var(--ink-strong); font-weight: 600; box-shadow: var(--glass-inner-highlight); }
.step-item.done .idx { background: var(--success); color: #fff; }
.idx {
  width: 22px; height: 22px; flex: 0 0 22px; display: grid; place-items: center;
  border-radius: 50%; background: var(--glass-2); font-size: 12px; color: inherit;
}
.step-desc { font-size: 13px; color: var(--ink-muted); margin-bottom: var(--space-4); }

.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-4); }
.grid .full { grid-column: 1 / -1; }
.stack { display: flex; flex-direction: column; gap: var(--space-2); }
.opt-row {
  display: flex; align-items: center; justify-content: space-between; gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1);
}
.opt-row strong { font-size: 13.5px; color: var(--ink-strong); }
.opt-row p { font-size: 12.5px; color: var(--ink-muted); }
.w220 { width: 220px; }
.reset-row { display: flex; justify-content: flex-end; }

.strength { display: flex; flex-direction: column; gap: 6px; }
.bars { display: flex; gap: 5px; }
.bars span { flex: 1; height: 5px; border-radius: 3px; background: var(--border); }
.bars span.on { background: var(--success); }
.bars span.on.weak { background: var(--warning); }
.strength .label { font-size: 12.5px; color: var(--ink); }
.warns { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--warning); }

.providers { display: flex; flex-direction: column; gap: var(--space-4); }
.provider-card { border: 1px solid var(--border); border-radius: var(--glass-radius-card); background: var(--glass-1); padding: var(--space-4); }
.provider-card header { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: 4px; }
.provider-card h4 { font-size: 14px; }
.pdesc { font-size: 12.5px; color: var(--ink-muted); margin-bottom: var(--space-3); }
.pfoot { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-3); }
.presult { font-size: 12.5px; color: var(--success); margin-top: 8px; }
.presult.bad { color: var(--danger); }

.tip { font-size: 12.5px; color: var(--ink-muted); }
.note-title { font-size: 13px; color: var(--ink-strong); font-weight: 500; }
.note-body { font-size: 12.5px; color: var(--ink-muted); margin: 4px 0 8px; }
.dev-note { padding: var(--space-3); border: 1px dashed var(--border); border-radius: var(--glass-radius-card); }

.finish .summary { display: flex; flex-direction: column; gap: 2px; border: 1px solid var(--border); border-radius: var(--glass-radius-card); overflow: hidden; }
.summary li { display: flex; justify-content: space-between; gap: var(--space-4); padding: 11px var(--space-4); font-size: 13.5px; }
.summary li:nth-child(odd) { background: var(--glass-1); }
.summary .k { color: var(--ink-muted); }
.summary .v { color: var(--ink-strong); text-align: right; }

.okmsg { margin-top: var(--space-3); font-size: 13px; color: var(--success); }
.errmsg { margin-top: var(--space-3); font-size: 13px; color: var(--danger); }

.wz-foot { display: flex; align-items: center; gap: var(--space-2); width: 100%; }
.spacer { flex: 1; }
.db-result { margin-top: var(--space-4); font-size: 13px; color: var(--success); }
.db-result.bad { color: var(--danger); }

@media (max-width: 900px) {
  .wz-body { grid-template-columns: 1fr; }
  .steps-nav { flex-direction: row; overflow-x: auto; }
  .step-item { flex: 0 0 auto; }
  .w220 { width: 100%; }
  .opt-row { flex-direction: column; align-items: flex-start; }
}
</style>
