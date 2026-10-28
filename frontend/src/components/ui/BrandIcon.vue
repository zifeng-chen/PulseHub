<script setup lang="ts">
/**
 * 机构品牌图标：使用各银行 / 支付平台的官方标志（public/brands 内置，离线可用）。
 * 机构名支持常见简称与全称（工行 / 中国工商银行 / ICBC…）；
 * 未收录的机构回退为「品牌色 + 首字」字母标，不会出现空白或破图。
 *
 * 加新机构：图标取自 iconfont「银行LOGO合集」
 * https://www.iconfont.cn/collections/detail?cid=23316
 * 步骤见 docs/guides/品牌图标维护.md（脚本：deploy/import_brand_icons.py）
 */
import { computed, ref, watch } from 'vue'
import { Banknote, Coins, Wallet } from 'lucide-vue-next'

interface Brand {
  key: string
  file: string
  color: string
  aliases: string[]
}

const BRANDS: Brand[] = [
  { key: 'icbc', file: 'icbc.svg', color: '#C7000B', aliases: ['工商银行', '工行', 'icbc'] },
  { key: 'ccb', file: 'ccb.svg', color: '#005BAC', aliases: ['建设银行', '建行', 'ccb'] },
  { key: 'abc', file: 'abc.svg', color: '#009A44', aliases: ['农业银行', '农行', 'abc'] },
  { key: 'boc', file: 'boc.svg', color: '#B22222', aliases: ['中国银行', '中行', 'boc'] },
  { key: 'bocom', file: 'bocom.svg', color: '#005BAC', aliases: ['交通银行', '交行', 'bocom'] },
  { key: 'psbc', file: 'psbc.svg', color: '#007A33', aliases: ['邮政储蓄', '邮储', 'psbc'] },
  { key: 'cmb', file: 'cmb.svg', color: '#C7000B', aliases: ['招商银行', '招行', 'cmb'] },
  { key: 'spdb', file: 'spdb.svg', color: '#0055A5', aliases: ['浦东发展', '浦发', 'spdb'] },
  { key: 'citic', file: 'citic.svg', color: '#C7000B', aliases: ['中信银行', '中信', 'citic', 'cncb'] },
  { key: 'cmbc', file: 'cmbc.svg', color: '#005BAC', aliases: ['民生银行', '民生', 'cmbc'] },
  { key: 'cib', file: 'cib.svg', color: '#005BAC', aliases: ['兴业银行', '兴业', 'cib'] },
  { key: 'ceb', file: 'ceb.svg', color: '#6A2C91', aliases: ['光大银行', '光大', 'ceb'] },
  { key: 'hxb', file: 'hxb.svg', color: '#C7000B', aliases: ['华夏银行', '华夏', 'hxb'] },
  { key: 'pab', file: 'pab.png', color: '#F36F21', aliases: ['平安银行', '平安', 'pab'] },
  { key: 'cgb', file: 'cgb.svg', color: '#C7000B', aliases: ['广发银行', '广发', 'cgb'] },
  { key: 'bob', file: 'bob.svg', color: '#C7000B', aliases: ['北京银行', 'bob'] },
  { key: 'bos', file: 'bos.svg', color: '#005BAC', aliases: ['上海银行', 'bos'] },
  { key: 'nbcb', file: 'nbcb.svg', color: '#F5A623', aliases: ['宁波银行', 'nbcb'] },
  { key: 'alipay', file: 'alipay.png', color: '#1677FF', aliases: ['支付宝', 'alipay'] },
  { key: 'wechatpay', file: 'wechatpay.png', color: '#07C160', aliases: ['微信支付', '微信零钱', '微信', 'wechat', 'weixin'] },
  { key: 'unionpay', file: 'unionpay.png', color: '#E60012', aliases: ['云闪付', '银联', 'unionpay'] },
  { key: 'meituan', file: 'meituan.png', color: '#FFD100', aliases: ['美团', 'meituan'] },
  { key: 'douyin', file: 'douyin.png', color: '#111111', aliases: ['抖音', 'douyin', '字节'] },
]

const props = withDefaults(
  defineProps<{
    institution?: string | null
    name?: string | null
    /** 账户类型，用于无机构时兜底 */
    type?: string | null
    size?: 'md' | 'sm' | 'lg'
  }>(),
  { institution: '', name: '', type: 'bank', size: 'md' },
)

const failed = ref(false)
watch(() => props.institution, () => { failed.value = false })
watch(() => props.name, () => { failed.value = false })

const haystack = computed(() =>
  `${props.institution || ''} ${props.name || ''}`.toLowerCase().replace(/\s+/g, ''),
)

const brand = computed<Brand | null>(() => {
  const text = haystack.value
  if (!text) return null
  for (const b of BRANDS) {
    if (b.aliases.some((a) => text.includes(a.toLowerCase()))) return b
  }
  return null
})

const src = computed(() => {
  if (!brand.value || failed.value) return ''
  return `${import.meta.env.BASE_URL}brands/${brand.value.file}`
})

/** 未收录机构：品牌色首字标；颜色由名称稳定散列，同名永远同色 */
const fallback = computed(() => {
  const text = (props.institution || props.name || '').trim()
  if (!text) return null
  const palette = ['#C7000B', '#005BAC', '#009A44', '#6A2C91', '#E1251B', '#0F766E', '#B45309', '#7C3AED']
  let hash = 0
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) % 100000
  return {
    char: text.replace(/[（(].*$/, '').slice(0, 1) || text.slice(0, 1),
    color: brand.value?.color || palette[hash % palette.length],
  }
})

const genericIcon = computed(() => {
  if (props.type === 'cash') return Banknote
  if (props.type === 'other') return Coins
  return Wallet
})
</script>

<template>
  <span class="brand" :class="[`s-${size}`]" :aria-label="institution || name || '账户'">
    <img
      v-if="src"
      :src="src"
      :alt="institution || name || '机构标志'"
      class="logo"
      loading="lazy"
      decoding="async"
      @error="failed = true"
    />
    <span
      v-else-if="fallback"
      class="mono"
      :style="{ background: fallback.color }"
      aria-hidden="true"
    >{{ fallback.char }}</span>
    <component :is="genericIcon" v-else :size="size === 'sm' ? 14 : 17" class="generic" />
  </span>
</template>

<style scoped>
.brand {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 11px;
  /* 官方标志多为深色文字，深色主题下也保持白底，否则不可见 */
  background: #fff;
  box-shadow: inset 0 0 0 1px rgba(16, 32, 64, 0.08), 0 1px 2px rgba(16, 32, 64, 0.06);
  overflow: hidden;
}
.s-md { width: 60px; height: 36px; padding: 4px 6px; }
.s-sm { width: 34px; height: 30px; padding: 4px; border-radius: 9px; }
.s-lg { width: 72px; height: 42px; padding: 5px 6px; }
.logo { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.mono {
  display: grid;
  place-items: center;
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  border-radius: 7px;
  letter-spacing: 0.02em;
}
.s-md .mono { width: 26px; height: 26px; }
.s-lg .mono { width: 32px; height: 32px; font-size: 18px; }
.s-sm .mono { width: 22px; height: 22px; font-size: 13px; }
.generic { color: var(--ink-muted); }
</style>
