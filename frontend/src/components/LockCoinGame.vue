<script setup lang="ts">
/**
 * 锁屏小游戏：马里奥式「跳起来顶砖拿金币」。
 *
 * - ← → / A D 走动，空格 / ↑ / W 起跳；移动端有方向键与跳跃键，也可以直接点画面起跳。
 * - 跳起用头顶问号砖 → 金币弹出、砖块变灰；四块砖都顶完自动刷新，可以一直玩。
 * - 金币数按天存在本地，跨天归零；每 10 枚给一次提示。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Volume2, VolumeX } from 'lucide-vue-next'

/** 逻辑坐标（固定 340×210），外层按容器宽度整体缩放，物理计算不受影响 */
const W = 340
const H = 210
const GROUND = 180
const MARIO_W = 34
const MARIO_H = 46
const STAND_TOP = GROUND - MARIO_H
const SPEED = 124
const GRAVITY = 1600
const JUMP_V = 500
const BLOCK_SIZE = 30
const BLOCK_TOP = 62
const BLOCK_BOTTOM = BLOCK_TOP + BLOCK_SIZE
const BLOCK_XS = [46, 116, 186, 256]
const COIN_KEY = 'fam.lock.coins'
const MUTE_KEY = 'fam.lock.mute'

interface BlockState {
  id: number
  x: number
  used: boolean
  bump: boolean
  flash: boolean
}

const wrapRef = ref<HTMLElement | null>(null)
const scale = ref(1)
const pulse = ref('')

const muted = ref(false)
let audioCtx: AudioContext | null = null

const mario = reactive({
  x: 56, y: STAND_TOP, vy: 0, onGround: true, dir: 1, coins: 0,
})
const keys = reactive({ left: false, right: false })
const blocks = reactive<BlockState[]>(
  BLOCK_XS.map((x, i) => ({ id: i, x, used: false, bump: false, flash: false })),
)
/** 落地阴影：跳得越高越小越淡 */
const shadowStyle = computed(() => {
  const air = Math.max(0, STAND_TOP - mario.y)
  const k = 1 - Math.min(1, air / 120) * 0.5
  return {
    left: `${mario.x + 4}px`,
    transform: `scale(${k.toFixed(3)})`,
    opacity: (0.1 + 0.3 * k).toFixed(2),
  }
})
const moving = computed(() => keys.left || keys.right)

const flyCoins = ref<{ id: number; x: number; y: number }[]>([])
const floats = ref<{ id: number; x: number; y: number }[]>([])

let raf = 0
let lastTs = 0
let refillTimer = 0
let pulseTimer = 0
let seq = 0

function clamp(v: number, min: number, max: number) {
  return Math.min(max, Math.max(min, v))
}

// ---------------- 金币存档（按天） ----------------
function todayKey() {
  const d = new Date()
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
}

function loadCoins() {
  try {
    const raw = JSON.parse(localStorage.getItem(COIN_KEY) || '{}')
    mario.coins = raw.date === todayKey() ? Math.max(0, Number(raw.coins) || 0) : 0
  } catch {
    mario.coins = 0
  }
}

function saveCoins() {
  try {
    localStorage.setItem(COIN_KEY, JSON.stringify({ date: todayKey(), coins: mario.coins }))
  } catch {
    /* 隐私模式下不可写，忽略即可 */
  }
}

// ---------------- 自适应缩放 ----------------
function fit() {
  const width = wrapRef.value?.clientWidth || W
  // 桌面留一点放大空间，窄屏按容器等比缩小
  scale.value = Math.min(1.1, width / W)
}

// ---------------- 物理与碰撞 ----------------
/**
 * 8-bit 音效：只用方波，不加载任何音频文件。
 * 必须在用户操作里首次创建 AudioContext，否则会被自动播放策略拦下。
 */
function blip(kind: 'coin' | 'jump') {
  if (muted.value) return
  try {
    const Ctor = window.AudioContext
      ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return
    audioCtx = audioCtx || new Ctor()
    if (audioCtx.state === 'suspended') void audioCtx.resume()
    const now = audioCtx.currentTime
    const osc = audioCtx.createOscillator()
    const gain = audioCtx.createGain()
    osc.type = 'square'
    if (kind === 'coin') {
      osc.frequency.setValueAtTime(988, now)
      osc.frequency.setValueAtTime(1319, now + 0.06)
      gain.gain.setValueAtTime(0.0001, now)
      gain.gain.exponentialRampToValueAtTime(0.05, now + 0.008)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.2)
      osc.stop(now + 0.22)
    } else {
      osc.frequency.setValueAtTime(660, now)
      osc.frequency.exponentialRampToValueAtTime(1320, now + 0.1)
      gain.gain.setValueAtTime(0.0001, now)
      gain.gain.exponentialRampToValueAtTime(0.035, now + 0.01)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.14)
      osc.stop(now + 0.16)
    }
    osc.connect(gain).connect(audioCtx.destination)
    osc.start(now)
  } catch {
    /* 浏览器不支持或被禁用时静默降级 */
  }
}

function toggleMute() {
  muted.value = !muted.value
  try {
    localStorage.setItem(MUTE_KEY, muted.value ? '1' : '0')
  } catch {
    /* 忽略 */
  }
}

function jump() {
  if (!mario.onGround) return
  mario.vy = -JUMP_V
  mario.onGround = false
  blip('jump')
}

function step(dt: number) {
  let vx = 0
  if (keys.left) vx -= 1
  if (keys.right) vx += 1
  if (vx !== 0) {
    mario.dir = vx
    mario.x = clamp(mario.x + vx * SPEED * dt, 8, W - 8 - MARIO_W)
  }

  const prevTop = mario.y
  mario.vy += GRAVITY * dt
  mario.y += mario.vy * dt

  if (mario.y >= STAND_TOP) {
    mario.y = STAND_TOP
    mario.vy = 0
    mario.onGround = true
  } else {
    mario.onGround = false
  }

  // 上升途中头部穿过砖块底部 → 顶砖
  if (mario.vy < 0 && prevTop > BLOCK_BOTTOM && mario.y <= BLOCK_BOTTOM) {
    hitBlock()
  }
}

function hitBlock() {
  const cx = mario.x + MARIO_W / 2
  const block = blocks.find(
    (b) => !b.used && cx >= b.x - 6 && cx <= b.x + BLOCK_SIZE + 6,
  )
  if (!block) return

  mario.y = BLOCK_BOTTOM
  mario.vy = 150
  blip('coin')
  block.used = true
  block.bump = true
  window.setTimeout(() => { block.bump = false }, 200)
  collect(block)
}

function collect(block: BlockState) {
  mario.coins += 1
  saveCoins()

  seq += 1
  const id = seq
  flyCoins.value.push({ id, x: block.x + BLOCK_SIZE / 2 - 9, y: BLOCK_TOP + 2 })
  floats.value.push({ id, x: block.x + BLOCK_SIZE / 2 - 10, y: BLOCK_TOP - 14 })
  window.setTimeout(() => { flyCoins.value = flyCoins.value.filter((c) => c.id !== id) }, 780)
  window.setTimeout(() => { floats.value = floats.value.filter((f) => f.id !== id) }, 900)

  if (mario.coins > 0 && mario.coins % 10 === 0) {
    showPulse(`金币 × ${mario.coins}`)
  }
  if (blocks.every((b) => b.used)) scheduleRefill()
}

function showPulse(text: string) {
  pulse.value = text
  window.clearTimeout(pulseTimer)
  pulseTimer = window.setTimeout(() => { pulse.value = '' }, 1700)
}

function scheduleRefill() {
  window.clearTimeout(refillTimer)
  refillTimer = window.setTimeout(() => {
    blocks.forEach((b) => { b.used = false; b.flash = true })
    window.setTimeout(() => blocks.forEach((b) => { b.flash = false }), 500)
  }, 950)
}

function tick(ts: number) {
  if (!lastTs) lastTs = ts
  const dt = Math.min(0.034, (ts - lastTs) / 1000)
  lastTs = ts
  step(dt)
  raf = window.requestAnimationFrame(tick)
}

// ---------------- 键盘 / 触屏 ----------------
function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

function onKeyDown(e: KeyboardEvent) {
  if (isTyping(e.target)) return
  if (e.code === 'ArrowLeft' || e.code === 'KeyA') {
    keys.left = true
    e.preventDefault()
  } else if (e.code === 'ArrowRight' || e.code === 'KeyD') {
    keys.right = true
    e.preventDefault()
  } else if (e.code === 'Space' || e.code === 'ArrowUp' || e.code === 'KeyW') {
    jump()
    e.preventDefault()
  }
}

function onKeyUp(e: KeyboardEvent) {
  if (e.code === 'ArrowLeft' || e.code === 'KeyA') keys.left = false
  if (e.code === 'ArrowRight' || e.code === 'KeyD') keys.right = false
}

function resetKeys() {
  keys.left = false
  keys.right = false
}

function press(dir: 'left' | 'right', down: boolean) {
  keys[dir] = down
}

// ---------------- 生命周期 ----------------
let ro: ResizeObserver | null = null

onMounted(() => {
  loadCoins()
  try {
    muted.value = localStorage.getItem(MUTE_KEY) === '1'
  } catch {
    muted.value = false
  }
  fit()
  ro = new ResizeObserver(fit)
  if (wrapRef.value) ro.observe(wrapRef.value)
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
  window.addEventListener('blur', resetKeys)
  raf = window.requestAnimationFrame(tick)
})

onBeforeUnmount(() => {
  window.cancelAnimationFrame(raf)
  window.clearTimeout(refillTimer)
  window.clearTimeout(pulseTimer)
  ro?.disconnect()
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('keyup', onKeyUp)
  window.removeEventListener('blur', resetKeys)
})
</script>

<template>
  <div class="coin-game">
    <div ref="wrapRef" class="stage-wrap">
      <div
        class="stage"
        :style="{ width: `${W * scale}px`, height: `${H * scale}px` }"
        role="application"
        aria-label="跳起来顶砖拿金币的小游戏，点按画面起跳"
        @pointerdown="jump"
      >
        <div class="stage-inner" :style="{ transform: `scale(${scale})` }">
          <!-- 背景：云、远山、地面 -->
          <span class="cloud c1" aria-hidden="true" />
          <span class="cloud c2" aria-hidden="true" />
          <span class="cloud c3" aria-hidden="true" />
          <span class="hill" aria-hidden="true" />
          <span class="ground" aria-hidden="true" />
          <span class="bush b1" aria-hidden="true" />
          <span class="bush b2" aria-hidden="true" />

          <!-- 问号砖 -->
          <div
            v-for="b in blocks" :key="b.id"
            class="block"
            :class="{ used: b.used, bump: b.bump, flash: b.flash }"
            :style="{ left: `${b.x}px`, top: `${BLOCK_TOP}px` }"
            aria-hidden="true"
          >
            <span v-if="!b.used">?</span>
            <span v-else class="dot" />
          </div>

          <!-- 弹出的金币与 +1 -->
          <span
            v-for="c in flyCoins" :key="`c${c.id}`" class="fly-coin"
            :style="{ left: `${c.x}px`, top: `${c.y}px` }" aria-hidden="true"
          >¥</span>
          <span
            v-for="t in floats" :key="`t${t.id}`" class="float-text"
            :style="{ left: `${t.x}px`, top: `${t.y}px` }" aria-hidden="true"
          >+1</span>

          <!-- 马里奥 -->
          <span class="shadow" :style="shadowStyle" aria-hidden="true" />
          <div
            class="mario"
            :class="{ moving }"
            :style="{ left: `${mario.x}px`, top: `${mario.y}px`, transform: mario.dir < 0 ? 'scaleX(-1)' : 'none' }"
            aria-hidden="true"
          >
            <svg viewBox="0 0 34 46" xmlns="http://www.w3.org/2000/svg">
              <!-- 帽子 -->
              <path d="M7.2 13.8c0-5.1 4.4-7.9 9.8-7.9s9.8 2.8 9.8 7.9z" fill="#E4322B" />
              <path d="M8.6 9.6c1.2-1.9 4.2-3.1 8.4-3.1 2 0 3.8.3 5.3 1-2.9.4-8.6 1.2-13.7 2.1z" fill="#F2544C" opacity="0.75" />
              <rect x="4.8" y="12.4" width="24.4" height="4.2" rx="2.1" fill="#C4241F" />
              <!-- 脸 -->
              <rect x="10" y="16.2" width="14.4" height="11.6" rx="4.4" fill="#FFC59B" />
              <rect x="8.8" y="16" width="2.9" height="5" rx="1.45" fill="#57301C" />
              <rect x="22.6" y="16" width="2.4" height="4.4" rx="1.2" fill="#57301C" />
              <!-- 眼睛、鼻子、胡子 -->
              <rect x="18.6" y="19" width="2.3" height="3.9" rx="1.15" fill="#2A1D14" />
              <circle cx="22.6" cy="22.6" r="1.9" fill="#F5B489" />
              <rect x="13.3" y="24.2" width="9.9" height="3.2" rx="1.6" fill="#57301C" />
              <rect x="12.2" y="24.6" width="2.2" height="2.3" rx="1.1" fill="#57301C" />
              <!-- 上衣 -->
              <path d="M9.4 27.5h15.2a1.6 1.6 0 0 1 1.6 1.6v5.3H7.8v-5.3a1.6 1.6 0 0 1 1.6-1.6z" fill="#E4322B" />
              <!-- 背带裤 -->
              <path d="M11 31.3h12v8.5a1.4 1.4 0 0 1-1.4 1.4H12.4a1.4 1.4 0 0 1-1.4-1.4z" fill="#2C63CB" />
              <rect x="11.9" y="27.3" width="2.5" height="5.2" rx="0.6" fill="#2C63CB" />
              <rect x="19.6" y="27.3" width="2.5" height="5.2" rx="0.6" fill="#2C63CB" />
              <circle cx="13.2" cy="32.5" r="1.2" fill="#F5C542" />
              <circle cx="20.8" cy="32.5" r="1.2" fill="#F5C542" />
              <!-- 手套 -->
              <rect x="6.2" y="28.6" width="3.4" height="6.2" rx="1.7" fill="#FFC59B" />
              <rect x="24.4" y="28.6" width="3.4" height="6.2" rx="1.7" fill="#FFC59B" />
              <circle cx="7.9" cy="35.6" r="2.3" fill="#FFF8F1" />
              <circle cx="26.1" cy="35.6" r="2.3" fill="#FFF8F1" />
              <!-- 鞋 -->
              <rect x="8.2" y="41.2" width="8.8" height="3.9" rx="1.95" fill="#4E2C18" />
              <rect x="17" y="41.2" width="8.8" height="3.9" rx="1.95" fill="#4E2C18" />
            </svg>
          </div>

          <Transition name="pulse">
            <span v-if="pulse" class="pulse-badge">{{ pulse }}</span>
          </Transition>
        </div>
      </div>
    </div>

    <div class="hud">
      <div class="pad">
        <button
          class="pad-btn" type="button" aria-label="向左走"
          @pointerdown.prevent="press('left', true)"
          @pointerup="press('left', false)"
          @pointerleave="press('left', false)"
          @pointercancel="press('left', false)"
        >◀</button>
        <button
          class="pad-btn pad-jump" type="button" aria-label="跳跃"
          @pointerdown.prevent="jump"
        >跳</button>
        <button
          class="pad-btn" type="button" aria-label="向右走"
          @pointerdown.prevent="press('right', true)"
          @pointerup="press('right', false)"
          @pointerleave="press('right', false)"
          @pointercancel="press('right', false)"
        >▶</button>
      </div>
      <div class="coin-count">
        <button
          class="mute-btn" type="button"
          :aria-label="muted ? '开启音效' : '关闭音效'"
          :title="muted ? '开启音效' : '关闭音效'"
          @click="toggleMute"
        >
          <VolumeX v-if="muted" :size="14" />
          <Volume2 v-else :size="14" />
        </button>
        <span class="coin-badge" aria-hidden="true">¥</span>
        <strong>{{ mario.coins }}</strong>
        <small>今日金币</small>
      </div>
    </div>
    <p class="tip">← → 走动，空格 / 点画面起跳，顶问号砖出金币</p>
  </div>
</template>

<style scoped>
.coin-game {
  display: grid;
  /* 显式 1fr：默认 auto 轨道会按 max-content 撑开，窄屏上会横向溢出 */
  grid-template-columns: minmax(0, 1fr);
  gap: 9px;
  justify-items: start;
  margin-top: 26px;
}
.stage-wrap { width: 100%; max-width: 374px; }
.stage {
  position: relative;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: linear-gradient(#0b1730, #14294b 60%, #1d3f6b);
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.42);
  cursor: pointer;
  touch-action: manipulation;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
}
.stage-inner {
  position: relative;
  width: 340px;
  height: 210px;
  transform-origin: top left;
}

/* 背景 */
.cloud {
  position: absolute;
  height: 11px;
  width: 44px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.13);
  box-shadow: 13px -7px 0 -2px rgba(255, 255, 255, 0.13), -9px -5px 0 -3px rgba(255, 255, 255, 0.13);
  animation: drift 30s linear infinite;
}
.c1 { top: 24px; left: -70px; }
.c2 { top: 50px; left: -160px; animation-duration: 42s; animation-delay: -14s; opacity: 0.75; }
.c3 { top: 8px; left: -220px; animation-duration: 54s; animation-delay: -30s; opacity: 0.55; }
@keyframes drift { to { transform: translateX(520px); } }

.hill {
  position: absolute;
  left: 168px;
  bottom: 29px;
  width: 150px;
  height: 74px;
  border-radius: 75px 75px 0 0;
  background: linear-gradient(#1f4d3a, #143327);
  opacity: 0.85;
}
.ground {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 30px;
  background:
    repeating-linear-gradient(90deg, rgba(0, 0, 0, 0.16) 0 2px, transparent 2px 24px),
    repeating-linear-gradient(0deg, rgba(0, 0, 0, 0.14) 0 2px, transparent 2px 15px),
    linear-gradient(#8B5A2B, #633E1C);
  border-top: 4px solid #3FA34D;
}

/* 问号砖 */
.block {
  position: absolute;
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  border: 2px solid #b87327;
  font-size: 17px;
  font-weight: 800;
  color: #6b3e10;
  background: linear-gradient(#ffd766, #f2a93b);
  box-shadow:
    inset 0 -3px 0 rgba(150, 80, 10, 0.35),
    inset 0 2px 0 rgba(255, 255, 255, 0.55),
    0 4px 10px rgba(0, 0, 0, 0.28);
  transition: filter 200ms ease;
}
.block.used {
  border-color: #4e3722;
  color: transparent;
  background: linear-gradient(#8c6a4a, #6c4e33);
  box-shadow: inset 0 -3px 0 rgba(0, 0, 0, 0.3), 0 3px 8px rgba(0, 0, 0, 0.24);
}
.block .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.35);
}
.block.bump { animation: block-bump 200ms ease; }
@keyframes block-bump {
  50% { transform: translateY(-5px); }
}
.block.flash { animation: block-flash 500ms ease; }
@keyframes block-flash {
  40% { filter: brightness(1.6); }
}

/* 金币与 +1 */
.fly-coin {
  position: absolute;
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  font-size: 10px;
  font-weight: 800;
  color: #8a5a00;
  background: radial-gradient(circle at 35% 30%, #fff3b0, #ffc83d 60%, #e39a14);
  box-shadow: 0 0 10px rgba(255, 200, 61, 0.6);
  pointer-events: none;
  animation: coin-pop 760ms cubic-bezier(0.22, 0.68, 0.36, 1) forwards;
}
@keyframes coin-pop {
  0% { transform: translateY(0) scale(0.7); opacity: 0; }
  18% { transform: translateY(-10px) scale(1); opacity: 1; }
  55% { transform: translateY(-42px) scale(1.06); opacity: 1; }
  100% { transform: translateY(-8px) scale(0.5); opacity: 0; }
}
.float-text {
  position: absolute;
  font-size: 12px;
  font-weight: 800;
  color: #ffe07a;
  text-shadow: 0 2px 6px rgba(0, 0, 0, 0.55);
  pointer-events: none;
  animation: float-up 880ms ease-out forwards;
}
@keyframes float-up {
  from { transform: translateY(0); opacity: 0; }
  20% { opacity: 1; }
  to { transform: translateY(-26px); opacity: 0; }
}
.pulse-badge {
  position: absolute;
  left: 50%;
  top: 22px;
  transform: translateX(-50%);
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  color: #3a2600;
  background: linear-gradient(#ffe9a3, #ffc83d);
  box-shadow: 0 6px 18px rgba(255, 200, 61, 0.35);
}
.pulse-enter-active, .pulse-leave-active { transition: opacity 260ms ease, transform 260ms ease; }
.pulse-enter-from, .pulse-leave-to { opacity: 0; transform: translate(-50%, -6px); }

/* 马里奥 */
.mario {
  position: absolute;
  width: 34px;
  height: 46px;
  will-change: top, left;
}
.mario svg {
  display: block;
  width: 100%;
  height: 100%;
  filter: drop-shadow(0 3px 5px rgba(0, 0, 0, 0.42));
}
.mario.moving svg { animation: mario-bob 0.34s steps(2, end) infinite; }
@keyframes mario-bob {
  50% { transform: translateY(-1.5px); }
}
.shadow {
  position: absolute;
  top: 176px;
  width: 26px;
  height: 7px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.5);
  filter: blur(1.5px);
  transform-origin: center;
  pointer-events: none;
}
.bush {
  position: absolute;
  bottom: 28px;
  height: 20px;
  border-radius: 12px 12px 0 0;
  background: linear-gradient(#2f8f4a, #1c6234);
  box-shadow: -8px 3px 0 -3px #2f8f4a, 8px 3px 0 -3px #2f8f4a;
  opacity: 0.85;
}
.b1 { left: 6px; width: 40px; height: 15px; }
.b2 { right: 34px; width: 34px; height: 13px; opacity: 0.7; }

/* 底部：操作与金币计数 */
.hud {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  max-width: 374px;
  justify-content: space-between;
}
.pad { display: flex; gap: 6px; }
.pad-btn {
  width: 38px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.07);
  color: #dce6f7;
  font-size: 13px;
  cursor: pointer;
  touch-action: none;
  transition: background var(--motion-fast) var(--ease-out);
}
.pad-btn:active { background: rgba(255, 255, 255, 0.18); }
.pad-jump {
  width: 52px;
  border-color: rgba(124, 92, 252, 0.5);
  background: rgba(124, 92, 252, 0.26);
}
.coin-count {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  color: #e8eef9;
  font-variant-numeric: tabular-nums;
}
.coin-count strong { font-size: 18px; font-weight: 700; }
.coin-count small { font-size: 11px; color: #8fa3c4; }
.mute-btn {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  align-self: center;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: #8fa3c4;
  cursor: pointer;
  transition: color var(--motion-fast) var(--ease-out), background var(--motion-fast) var(--ease-out);
}
.mute-btn:hover { color: #dce6f7; background: rgba(255, 255, 255, 0.08); }
.coin-badge {
  width: 18px;
  height: 18px;
  display: inline-grid;
  place-items: center;
  border-radius: 50%;
  font-size: 10px;
  font-weight: 800;
  color: #8a5a00;
  background: radial-gradient(circle at 35% 30%, #fff3b0, #ffc83d 60%, #e39a14);
  box-shadow: 0 0 8px rgba(255, 200, 61, 0.42);
  align-self: center;
}
.tip { margin: 0; font-size: 11.5px; color: #8fa3c4; }

@media (max-width: 760px) {
  .coin-game { justify-items: center; }
  .tip { text-align: center; }
}

@media (prefers-reduced-motion: reduce) {
  .cloud { animation: none; }
  .fly-coin { animation-duration: 0.4s; }
  .float-text { animation-duration: 0.4s; }
}
</style>
