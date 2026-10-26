#!/usr/bin/env node
/**
 * FAM 全链路 E2E（真实后端 + 真实前端 + 真实 Chromium）。
 *
 * 覆盖：
 *   1. 首次启动初始化（API）与造数
 *   2. 浏览器真实登录（表单 → 跳转 → 首屏可用、引导不遮挡）
 *   3. 全量路由巡检（标题 / 5xx / JS 异常 / 内容渲染）
 *   4. 跨层一致性（后端金额精度 → 页面显示）
 *   5. 未登录访问受保护路由的守卫行为
 *
 * 用法：node deploy/e2e/run.mjs
 * 产物：E2E-全链路测试报告.md、deploy/e2e/screenshots/*.png
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const ROOT = path.resolve(process.cwd())
const BACKEND = path.join(ROOT, 'backend')
const FRONTEND = path.join(ROOT, 'frontend')
const PY = process.env.FAM_PYTHON || 'python'
const CHROME = process.env.FAM_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const API = 'http://127.0.0.1:8000/api/v1'
const WEB = 'http://127.0.0.1:5173'
const DB = path.join(BACKEND, 'data', '_e2e.db')
const SHOTS = path.join(ROOT, 'deploy', 'e2e', 'screenshots')

const require = createRequire(path.join(FRONTEND, 'package.json'))
const { WebSocket } = require('ws')

const ADMIN = {
  display_name: 'E2E 管理员',
  username: 'e2eadmin',
  password: 'E2ePass!2345',
  confirm_password: 'E2ePass!2345',
}

const PAGES = [
  ['/dashboard', '脉枢台'],
  ['/ledger', '家庭台账总览'],
  ['/ledger/assets', '资产'],
  ['/ledger/assets/real-estates', '不动产'],
  ['/ledger/assets/vehicles', '车辆'],
  ['/ledger/assets/precious-metals', '贵金属'],
  ['/ledger/accounts', '账户'],
  ['/ledger/transactions', '流水'],
  ['/ledger/reconciliation', '对账'],
  ['/ledger/liabilities', '负债'],
  ['/ledger/credit-cards', '信用卡'],
  ['/ledger/investments', '投资'],
  ['/ledger/leases', '租赁'],
  ['/ledger/capture', '智能录入'],
  ['/ledger/reminders', '提醒'],
  ['/insights', '综合脉诊'],
  ['/insights/assets', '资产分析'],
  ['/insights/income', '收入分析'],
  ['/insights/expenses', '消费分析'],
  ['/insights/liabilities', '负债分析'],
  ['/insights/investments', '投资分析'],
  ['/insights/leases', '租赁分析'],
  ['/insights/spending-habits', '消费习惯分析'],
  ['/insights/data-quality', '数据质量'],
  ['/settings', '设置'],
  ['/settings/household', '家庭与成员'],
  ['/settings/categories', '分类管理'],
  ['/settings/finance', '财务口径'],
  ['/settings/notifications', '提醒通知'],
  ['/settings/ocr', '百度 OCR'],
  ['/settings/beike', '房产数据（贝壳）'],
  ['/settings/local-ai', '本地 AI'],
  ['/settings/ai-providers', '第三方 AI 接口'],
  ['/settings/privacy', '隐私与安全'],
  ['/settings/backup', '数据备份与恢复'],
  ['/settings/system', '系统状态与审计'],
]

/** PC 端关键页抽查：覆盖卡片、表格、图表、表单四类主要布局 */
const KEY_PAGE_PATHS = new Set([
  '/dashboard', '/ledger', '/ledger/accounts', '/ledger/transactions', '/ledger/assets',
  '/insights', '/insights/expenses', '/settings', '/settings/categories', '/settings/finance',
])
const KEY_PAGES = PAGES.filter(([route]) => KEY_PAGE_PATHS.has(route))

/** 注入到页面里的通用表单助手（按 AppField 标签定位控件） */
const HELPERS = `
window.__e2e = {
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  norm: (s) => (s || '').replace(/\\s+/g, '').replace(/\\*$/, ''),
  fields: () => Array.from(document.querySelectorAll('.drawer-panel .field, [role="dialog"] .field')),
  fieldOf(label) {
    const want = this.norm(label);
    return this.fields().find((f) => {
      const l = f.querySelector('label.lbl');
      return l && this.norm(l.textContent).startsWith(want);
    });
  },
  fill(map) {
    const out = {};
    for (const label of Object.keys(map)) {
      const value = map[label];
      const f = this.fieldOf(label);
      if (!f) { out[label] = 'NO_FIELD'; continue; }
      const el = f.querySelector('input, select, textarea');
      if (!el) { out[label] = 'NO_CTRL'; continue; }
      if (el.tagName === 'SELECT') {
        const opt = Array.from(el.options).find((o) => o.textContent.includes(value));
        if (!opt) {
          const shown = Array.from(el.options).map((o) => (o.textContent || '').trim().slice(0, 12)).slice(0, 4).join('|');
          out[label] = 'NO_OPTION:' + shown;
          continue;
        }
        el.value = opt.value;
      } else {
        el.value = value;
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      out[label] = 'OK';
    }
    return out;
  },
  clickText(names) {
    const list = Array.isArray(names) ? names : [names];
    const buttons = Array.from(document.querySelectorAll('button'));
    let btn = buttons.find((b) => list.some((n) => b.textContent.trim() === n));
    if (!btn) btn = buttons.find((b) => list.some((n) => b.textContent.includes(n)));
    if (!btn) return 'NO_BUTTON';
    btn.click();
    return 'CLICKED';
  }
};
`

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
const failures = []

function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  const icon = ok ? 'PASS' : 'FAIL'
  console.log(`[${icon}] ${name}${detail ? ' — ' + detail : ''}`)
  if (!ok) failures.push({ name, detail })
  return ok
}

async function waitHttp(url, timeoutMs = 90000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url)
      if (res.ok) return true
    } catch { /* not ready */ }
    await sleep(300)
  }
  return false
}

/** 端口若已有服务在跑，直接失败：否则 waitHttp 会把别人的服务当成自己启动成功。 */
async function portTaken(port) {
  try {
    await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(900) })
    return true
  } catch {
    return false
  }
}

async function post(pathname, body, token) {
  const res = await fetch(API + pathname, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body ?? {}),
  })
  const json = await res.json().catch(() => ({}))
  if (!json.success) throw new Error(`${pathname} -> HTTP ${res.status} ${JSON.stringify(json).slice(0, 240)}`)
  return json.data
}

async function get(pathname, token) {
  const res = await fetch(API + pathname, { headers: { Authorization: `Bearer ${token}` } })
  const json = await res.json().catch(() => ({}))
  return { status: res.status, body: json }
}

// ---------------- CDP 浏览器 ----------------
class Browser {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.pageErrors = []
    this.pageNetErrors = []
    ws.on('message', (raw) => {
      let msg
      try { msg = JSON.parse(raw.toString()) } catch { return }
      if (msg.id && this.pending.has(msg.id)) {
        this.pending.get(msg.id)(msg)
        this.pending.delete(msg.id)
        return
      }
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params?.exceptionDetails
        this.pageErrors.push(`[exception] ${(d?.exception?.description || d?.text || '').slice(0, 260)}`)
      }
      if (msg.method === 'Runtime.consoleAPICalled' && msg.params?.type === 'error') {
        const text = (msg.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ')
        this.pageErrors.push(`[console] ${text.slice(0, 260)}`)
      }
      if (msg.method === 'Network.responseReceived') {
        const { url, status } = msg.params.response
        if (status >= 500) this.pageNetErrors.push(`${status} ${url.replace(WEB, '')}`)
      }
    })
  }

  send(method, params = {}) {
    return new Promise((resolve) => {
      const id = ++this.seq
      this.pending.set(id, resolve)
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  async eval(expression) {
    const res = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    return res.result?.result?.value
  }

  resetSignals() {
    this.pageErrors = []
    this.pageNetErrors = []
  }

  async goto(pathname, expectTitle, timeoutMs = 15000) {
    this.resetSignals()
    await this.send('Page.navigate', { url: WEB + pathname })
    const start = Date.now()
    let last = {}
    while (Date.now() - start < timeoutMs) {
      const raw = await this.eval(`JSON.stringify({
        ready: document.readyState,
        title: document.title,
        len: (document.body?.innerText || '').trim().length,
        path: location.pathname
      })`)
      try { last = JSON.parse(raw || '{}') } catch { last = {} }
      if (last.ready === 'complete' && (last.title || '').includes(expectTitle) && (last.len || 0) > 40) {
        await sleep(350) // 等首屏请求与图表稳定
        return { ok: true, ...last }
      }
      await sleep(150)
    }
    return { ok: false, ...last }
  }

  async screenshot(file) {
    const res = await this.send('Page.captureScreenshot', { format: 'png' })
    if (res.result?.data) fs.writeFileSync(file, Buffer.from(res.result.data, 'base64'))
  }

  /** 给 file input 设置本地文件（需要 DOM.enable） */
  async setFile(selector, filePath) {
    const doc = await this.send('DOM.getDocument', { depth: -1 })
    const rootId = doc.result?.root?.nodeId
    if (!rootId) return false
    const found = await this.send('DOM.querySelector', { nodeId: rootId, selector })
    const nodeId = found.result?.nodeId
    if (!nodeId) return false
    await this.send('DOM.setFileInputFiles', { files: [filePath], nodeId })
    return true
  }

  close() { try { this.ws.close() } catch { /* ignore */ } }
}

// ---------------- 主流程 ----------------
async function main() {
  for (const port of [8000, 5173, 9333]) {
    if (await portTaken(port)) {
      console.error(`端口 ${port} 已被占用，请先停掉本地 dev server / 浏览器调试实例后重试。`)
      process.exitCode = 1
      return
    }
  }

  fs.mkdirSync(SHOTS, { recursive: true })
  for (const f of fs.readdirSync(SHOTS)) {
    const target = path.join(SHOTS, f)
    if (fs.statSync(target).isDirectory()) fs.rmSync(target, { recursive: true, force: true })
    else fs.unlinkSync(target)
  }

  // 1) 干净数据库
  for (const suffix of ['', '-wal', '-shm']) {
    const f = DB + suffix
    if (fs.existsSync(f)) fs.unlinkSync(f)
  }
  const env = { ...process.env, FAM_DATABASE_URL: 'sqlite+aiosqlite:///./data/_e2e.db', FAM_APP_ENV: 'development' }

  // 建表
  const createAll = spawn(PY, ['-c', 'import asyncio\nfrom app.db.base import create_all\nasyncio.run(create_all())'], { cwd: BACKEND, env, stdio: 'ignore' })
  await new Promise((r) => createAll.on('exit', r))

  // 2) 启动后端与前端
  const backend = spawn(PY, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'], { cwd: BACKEND, env, stdio: 'ignore' })
  const backendUp = await waitHttp(`${API}/health`, 60000)
  check('后端启动', backendUp, backendUp ? '127.0.0.1:8000' : '60s 内未就绪')
  if (!backendUp) { backend.kill(); process.exitCode = 1; return }

  const frontend = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--port', '5173', '--strictPort'], { cwd: FRONTEND, stdio: 'ignore' })
  const frontendUp = await waitHttp(WEB, 90000)
  check('前端启动', frontendUp, frontendUp ? '127.0.0.1:5173' : '90s 内未就绪')
  if (!frontendUp) { backend.kill(); frontend.kill(); process.exitCode = 1; return }

  let chrome
  let browser
  try {
    // 3) 初始化与造数
    try {
      await post('/setup/database', {
        use_embedded_dev_database: true, host: '127.0.0.1', port: 3306,
        database_name: 'fam', username: 'root', password: 'x',
        ssl_enabled: false, connect_timeout: 5,
      })
      await post('/setup/admin', ADMIN)
      await post('/setup/household', { name: 'E2E 家庭', base_currency: 'CNY', timezone: 'Asia/Shanghai', is_multi_member: true })
      await post('/setup/finance', { asset_purchase_separate: true, investment_valuation_in_net_worth: true })
      await post('/setup/complete')
      const login = await post('/auth/login', { username: ADMIN.username, password: ADMIN.password, remember_device: true })
      const token = login.access_token
      check('初始化 + 管理员登录（API）', true, `用户 ${ADMIN.username}`)

      const acc1 = await post('/ledger/accounts', {
        name: 'E2E 储蓄卡', account_type: 'bank', institution: '招商银行',
        currency: 'CNY', opening_balance: '10000', include_in_net_worth: true, card_last4: '1234',
      }, token)
      const acc2 = await post('/ledger/accounts', {
        name: 'E2E 信用卡', account_type: 'bank', institution: '招商银行',
        currency: 'CNY', opening_balance: '0', include_in_net_worth: true, card_last4: '5678',
        credit_limit: '50000', credit_used: '12345.67', card_expiry: '2029-05', bill_day: 5, due_day: 23,
      }, token)
      await post('/ledger/accounts', {
        name: 'E2E 现金', account_type: 'cash', currency: 'CNY', opening_balance: '500', include_in_net_worth: true,
      }, token)

      const ev = await post('/ledger/events', {
        event_type: 'expense', title: 'E2E 超市购物', amount: '200',
        account_id: acc1.id, currency: 'CNY',
      }, token)
      await post(`/ledger/events/${ev.id}/confirm`, {}, token)
      const ev2 = await post('/ledger/events', {
        event_type: 'income', title: 'E2E 工资', amount: '3000',
        account_id: acc1.id, currency: 'CNY',
      }, token)
      await post(`/ledger/events/${ev2.id}/confirm`, {}, token)
      check('造数（3 账户 + 2 笔已确认流水）', true, '信用卡额度 50000 / 已用 12345.67')
      check('可用额度跨层计算', acc2.available_credit === '37654.33', `available_credit=${acc2.available_credit}`)
    } catch (err) {
      check('初始化与造数', false, String(err).slice(0, 300))
      throw err
    }

    // 4) 浏览器
    chrome = spawn(CHROME, [
      '--headless=new', '--remote-debugging-port=9333',
      `--user-data-dir=${path.join(process.env.TEMP || '.', 'fam-e2e-' + Date.now())}`,
      '--no-first-run', '--no-default-browser-check', '--disable-gpu',
      '--window-size=1440,900', 'about:blank',
    ], { stdio: 'ignore' })

    let ready = false
    for (let i = 0; i < 80; i++) {
      try { await fetch('http://127.0.0.1:9333/json/version'); ready = true; break } catch { await sleep(250) }
    }
    check('Chromium 启动（CDP）', ready)
    if (!ready) throw new Error('Chrome CDP 未就绪')

    const target = await (await fetch('http://127.0.0.1:9333/json/new?' + encodeURIComponent(WEB + '/login'), { method: 'PUT' })).json()
    const ws = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((r) => ws.once('open', r))
    browser = new Browser(ws)
    await browser.send('Page.enable')
    await browser.send('Runtime.enable')
    await browser.send('Network.enable')
    await sleep(2500)

    // 4.1 登录页
    const loginPage = await browser.goto('/login', '登录')
    check('登录页渲染', loginPage.ok, loginPage.ok ? '' : JSON.stringify(loginPage))
    await browser.screenshot(path.join(SHOTS, '01-login.png'))

    // 4.2 真实登录
    browser.resetSignals()
    const submitted = await browser.eval(`(() => {
      const u = document.querySelector('#login-username');
      const p = document.querySelector('#login-password');
      if (!u || !p) return 'NO_FORM';
      u.value = ${JSON.stringify(ADMIN.username)}; u.dispatchEvent(new Event('input', { bubbles: true }));
      p.value = ${JSON.stringify(ADMIN.password)}; p.dispatchEvent(new Event('input', { bubbles: true }));
      const f = document.querySelector('form');
      if (f && f.requestSubmit) { f.requestSubmit(); return 'SUBMITTED' }
      return 'NO_SUBMIT';
    })()`)
    check('登录表单可提交', submitted === 'SUBMITTED', String(submitted))

    let afterLogin = {}
    for (let i = 0; i < 60; i++) {
      const raw = await browser.eval(`JSON.stringify({ path: location.pathname, title: document.title, len: (document.body.innerText||'').length })`)
      try { afterLogin = JSON.parse(raw || '{}') } catch { afterLogin = {} }
      if (afterLogin.path === '/dashboard' && (afterLogin.len || 0) > 100) break
      await sleep(200)
    }
    check('登录后跳转脉枢台', afterLogin.path === '/dashboard', `path=${afterLogin.path} title=${afterLogin.title}`)
    check('登录后无 5xx / JS 异常', browser.pageNetErrors.length === 0 && browser.pageErrors.length === 0,
      [...browser.pageNetErrors, ...browser.pageErrors].slice(0, 3).join(' | '))
    await browser.screenshot(path.join(SHOTS, '02-dashboard-after-login.png'))

    // 4.2.1 锁屏：点击后必须真的遮住界面，输入密码才能解锁
    await browser.eval(`(() => { document.querySelector('.user-btn')?.click(); return true })()`)
    await sleep(300)
    const lockClicked = await browser.eval(`(() => {
      const btn = Array.from(document.querySelectorAll('.user-menu button'))
        .find((b) => (b.textContent || '').includes('锁定屏幕'))
      if (btn) btn.click()
      return Boolean(btn)
    })()`)
    await sleep(400)
    const lockRaw = await browser.eval(`JSON.stringify({
      visible: Boolean(document.querySelector('.lock-screen')),
      clock: (document.querySelector('.clock-main')?.textContent || '').trim(),
      date: (document.querySelector('.clock-date')?.textContent || '').trim(),
      quote: (document.querySelector('.quote')?.textContent || '').trim(),
      hasUnlock: Boolean(document.querySelector('.lock-input')),
      game: Boolean(document.querySelector('.coin-game .mario')),
      blocks: document.querySelectorAll('.coin-game .block').length,
      pad: Boolean(document.querySelector('.coin-game .pad-jump')),
    })`)
    let lockState = {}
    try { lockState = JSON.parse(lockRaw || '{}') } catch { lockState = {} }
    await browser.screenshot(path.join(SHOTS, '03b-lock-screen.png'))
    check('锁屏生效（遮罩出现 / 左侧时钟 / 每日一言 / 右侧解锁）',
      lockClicked === true && lockState.visible === true && /^\d{2}:\d{2}$/.test(lockState.clock || '')
        && Boolean(lockState.date) && Boolean(lockState.quote) && lockState.hasUnlock === true,
      lockRaw)

    // 锁屏小游戏：马里奥 + 4 块问号砖 + 跳跃按钮
    check('锁屏小游戏（马里奥 / 问号砖 / 跳跃键）',
      lockState.game === true && lockState.blocks === 4 && lockState.pad === true,
      `game=${lockState.game} blocks=${lockState.blocks} pad=${lockState.pad}`)

    // 输入正确密码解锁
    await browser.eval(`(() => {
      const input = document.querySelector('.lock-input')
      if (!input) return false
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(input, ${JSON.stringify(ADMIN.password)})
      input.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    })()`)
    await sleep(200)
    await browser.eval(`(() => { document.querySelector('.lock-btn')?.click(); return true })()`)
    let unlocked = false
    for (let i = 0; i < 15; i += 1) {
      unlocked = await browser.eval(`!document.querySelector('.lock-screen')`)
      if (unlocked) break
      await sleep(200)
    }
    check('输入正确密码后解锁', unlocked === true, `unlocked=${unlocked}`)

    // 4.2.2 账户与流水已拆成两个页签：从台账总览一键直达流水，并可直接进入记账
    await browser.goto('/ledger', '家庭台账总览')
    const tabClick = await browser.eval(`(() => {
      const tabs = Array.from(document.querySelectorAll('.tab-list .tab'))
      const labels = tabs.map((b) => (b.textContent || '').trim())
      const target = tabs.find((b) => (b.textContent || '').trim() === '流水')
      if (target) target.click()
      return JSON.stringify({ labels, clicked: Boolean(target) })
    })()`)
    let tabInfo = {}
    try { tabInfo = JSON.parse(tabClick || '{}') } catch { tabInfo = {} }
    let tabPath = ''
    for (let i = 0; i < 20; i += 1) {
      tabPath = await browser.eval(`location.pathname`)
      if (tabPath === '/ledger/transactions') break
      await sleep(150)
    }
    const expectedTabs = ['总览', '账户', '对账', '资产', '流水', '负债', '投资', '租赁', '智能录入', '提醒']
    check('台账页签顺序与命名（总览/账户/对账/资产/流水/负债/投资/租赁/智能录入/提醒）',
      JSON.stringify(tabInfo.labels || []) === JSON.stringify(expectedTabs)
        && tabInfo.clicked === true && tabPath === '/ledger/transactions',
      tabClick + ` path=${tabPath}`)

    await browser.goto('/ledger/transactions?new=1', '流水')
    let quickAdd = {}
    for (let i = 0; i < 20; i += 1) {
      const raw = await browser.eval(`JSON.stringify({
        path: location.pathname,
        drawer: Boolean(document.querySelector('.drawer-panel')),
        title: (document.querySelector('.drawer-head h3')?.textContent || '').trim(),
      })`)
      try { quickAdd = JSON.parse(raw || '{}') } catch { quickAdd = {} }
      if (quickAdd.drawer) break
      await sleep(200)
    }
    check('流水页支持一键记账（?new=1 直接打开记账抽屉）',
      quickAdd.path === '/ledger/transactions' && quickAdd.drawer === true,
      JSON.stringify(quickAdd))

    const catOpts = await browser.eval(`JSON.stringify(
      Array.from(document.querySelectorAll('.cat-picker select option')).map((o) => o.textContent.trim()).filter(Boolean)
    )`)
    let catList = []
    try { catList = JSON.parse(catOpts || '[]') } catch { catList = [] }
    check('分类下拉不再出现「未分类」（改为必须选择）',
      catList.length > 0 && !catList.some((t) => t.includes('未分类')),
      catOpts)

    await browser.eval(`(() => { document.querySelector('.drawer-head .close')?.click(); return true })()`)
    await sleep(250)

    // 4.3 引导浮层不遮挡
    const welcome = await browser.eval(`(() => {
      const el = document.querySelector('.welcome-card');
      if (!el) return JSON.stringify({ present: false });
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return JSON.stringify({ present: true, position: cs.position, w: Math.round(r.width), h: Math.round(r.height), vw: innerWidth, vh: innerHeight });
    })()`)
    const welcomeInfo = JSON.parse(welcome || '{}')
    const notBlocking = !welcomeInfo.present
      || (welcomeInfo.position === 'fixed' && welcomeInfo.w < welcomeInfo.vw * 0.6 && welcomeInfo.h < welcomeInfo.vh * 0.8)
    check('首登引导不遮挡首页', notBlocking, welcome)

    // 4.4 多视口巡检：PC 1440 / PC 1366 关键页 / 移动 375
    if (process.argv.includes('--probe-forms')) {
      const targets = [
        ['/ledger/transactions', '流水'],
        ['/ledger/assets', '资产'],
        ['/ledger/liabilities', '负债'],
        ['/ledger/investments', '投资'],
        ['/ledger/leases', '租赁'],
        ['/ledger/reconciliation', '对账'],
        ['/settings/categories', '分类管理'],
      ]
      const out = []
      for (const [route, title] of targets) {
        await browser.goto(route, title)
        const raw = await browser.eval(`(async () => {
          const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
          const buttons = Array.from(document.querySelectorAll('button'))
            .map((b) => b.textContent.trim()).filter(Boolean);
          const target = Array.from(document.querySelectorAll('button'))
            .find((b) => /新增|添加|登记|记一笔|新建|发起/.test(b.textContent));
          if (!target) return JSON.stringify({ buttons, error: 'NO_ADD_BUTTON' });
          const clicked = target.textContent.trim();
          target.click();
          await sleep(600);
          const panel = document.querySelector('.drawer-panel, .modal-panel, [role="dialog"]');
          if (!panel) return JSON.stringify({ buttons, clicked, error: 'NO_PANEL' });
          const fields = Array.from(panel.querySelectorAll('.field')).map((f) => {
            const label = (f.querySelector('label.lbl')?.textContent || '').replace(/\\s+/g, '').replace(/\\*$/, '');
            const ctrl = f.querySelector('input, select, textarea');
            return {
              label,
              tag: ctrl ? ctrl.tagName.toLowerCase() : null,
              type: ctrl && ctrl.type ? ctrl.type : null,
              required: Boolean(f.querySelector('.req')),
              options: ctrl && ctrl.tagName === 'SELECT'
                ? Array.from(ctrl.options).map((o) => o.textContent.trim()).slice(0, 8) : undefined,
            };
          });
          const foot = Array.from(panel.querySelectorAll('.drawer-foot button, .modal-foot button'))
            .map((b) => b.textContent.trim());
          return JSON.stringify({ buttons, clicked, fields, foot });
        })()`)
        let parsed = {}
        try { parsed = JSON.parse(raw || '{}') } catch { parsed = { raw } }
        out.push({ route, ...parsed })
      }
      fs.writeFileSync(path.join(ROOT, 'deploy', 'e2e', '_form-probe.json'),
        JSON.stringify(out, null, 2), 'utf-8')
      console.log('探针结果已写入 deploy/e2e/_form-probe.json')
      return
    }

    if (process.argv.includes('--probe-flows')) {
      const out = []
      await browser.goto('/ledger/transactions', '流水')
      const txnRaw = await browser.eval(`(async () => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        const rows = Array.from(document.querySelectorAll('tbody tr'));
        if (!rows.length) return JSON.stringify({ error: 'NO_ROWS' });
        const first = rows[0];
        const rowButtons = Array.from(first.querySelectorAll('button')).map((b) => b.textContent.trim());
        const detailBtn = Array.from(first.querySelectorAll('button')).find((b) => b.textContent.includes('详情'));
        let panelFound = false, panelButtons = [], panelFields = [], panelText = '';
        if (detailBtn) {
          detailBtn.click();
          await sleep(1000);
          const panel = document.querySelector('.drawer-panel, [role="dialog"], .detail-pane, .pane');
          if (panel) {
            panelFound = true;
            panelButtons = Array.from(panel.querySelectorAll('button')).map((b) => b.textContent.trim());
            panelFields = Array.from(panel.querySelectorAll('.field')).map((f) => {
              const l = f.querySelector('label.lbl');
              return l ? l.textContent.trim() : '';
            });
            panelText = (panel.innerText || '').replace(/\\s+/g, ' ').slice(0, 350);
          }
        }
        return JSON.stringify({ rowButtons, panelFound, panelButtons, panelFields, panelText });
      })()`)
      out.push({ page: 'transactions-detail', ...JSON.parse(txnRaw || '{}') })

      await browser.goto('/ledger/reconciliation', '对账')
      const reconRaw = await browser.eval(`(async () => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        const selects = Array.from(document.querySelectorAll('select')).map((s) => {
          const box = s.closest('.field');
          return {
            label: box && box.querySelector('label.lbl') ? box.querySelector('label.lbl').textContent.trim() : '(无标签)',
            options: Array.from(s.options).map((o) => o.textContent.trim()).slice(0, 6),
          };
        });
        const buttons = Array.from(document.querySelectorAll('button'))
          .map((b) => b.textContent.trim())
          .filter((t) => t && !['脉枢台', '家庭台账', '脉诊', '设置', 'AI 助手', '收起侧栏'].includes(t));
        return JSON.stringify({ selects, buttons, text: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 260) });
      })()`)
      out.push({ page: 'reconciliation', ...JSON.parse(reconRaw || '{}') })

      for (const [route, title] of [['/ledger/capture', '智能录入'], ['/settings/backup', '数据备份与恢复']]) {
        await browser.goto(route, title)
        const infoRaw = await browser.eval(`JSON.stringify({
          buttons: Array.from(document.querySelectorAll('button'))
            .map((b) => b.textContent.trim())
            .filter((t) => t && !['脉枢台', '家庭台账', '脉诊', '设置', 'AI 助手', '收起侧栏'].includes(t))
            .slice(0, 14),
          fileInputs: Array.from(document.querySelectorAll('input[type=file]')).length,
          text: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 380)
        })`)
        out.push({ page: route, ...JSON.parse(infoRaw || '{}') })
      }

      console.log(JSON.stringify(out, null, 1))
      return
    }

    const auditPages = async (label, routes) => {
      const issues = []
      for (const [route, title] of routes) {
        const page = await browser.goto(route, title, 15000)
        const raw = await browser.eval(`JSON.stringify({
          sw: Math.round(document.documentElement.scrollWidth),
          iw: Math.round(window.innerWidth),
          placeholder: (document.body.innerText || '').includes('尚未启用')
        })`)
        let m = {}
        try { m = JSON.parse(raw || '{}') } catch { m = {} }
        const overflow = (m.sw || 0) > (m.iw || 0) + 2
        const netErr = browser.pageNetErrors.slice(0, 2)
        const jsErr = browser.pageErrors.slice(0, 2)
        const ok = page.ok && !overflow && !m.placeholder && netErr.length === 0 && jsErr.length === 0
        if (!ok) {
          issues.push({
            route, rendered: page.ok, overflow, width: `${m.sw}/${m.iw}`,
            placeholder: m.placeholder, net: netErr[0] || '', js: jsErr[0] || '',
          })
          await browser.screenshot(path.join(
            SHOTS, `fail-${label.replace(/[^a-z0-9]+/gi, '_')}-${route.replace(/[^a-z0-9]+/gi, '_')}.png`))
        }
      }
      check(`${label} 巡检（${routes.length} 页：渲染 + 无横向溢出 + 无占位 + 无异常）`,
        issues.length === 0, issues.length ? JSON.stringify(issues.slice(0, 3)) : '全部通过')
    }

    const auditLayout = async (label, expectSidebar, expectBottomNav) => {
      const raw = await browser.eval(`JSON.stringify({
        sidebar: Boolean(document.querySelector('.shell-sidebar')?.offsetWidth),
        bottomNav: Boolean(document.querySelector('.bottom-nav')?.offsetWidth)
      })`)
      let m = {}
      try { m = JSON.parse(raw || '{}') } catch { m = {} }
      check(`${label} 布局（侧栏${expectSidebar ? '显示' : '隐藏'} / 底部导航${expectBottomNav ? '显示' : '隐藏'}）`,
        m.sidebar === expectSidebar && m.bottomNav === expectBottomNav, raw)

      // 移动端：顶部栏必须吸顶（滚动后仍在视口顶部），并禁用双击缩放
      if (expectBottomNav) {
        const probe = await browser.eval(`(async () => {
          const bar = document.querySelector('.mobile-topbar')
          if (!bar) return JSON.stringify({ ok: false, reason: 'no-topbar' })
          const pos = getComputedStyle(bar).position
          window.scrollTo(0, 900)
          await new Promise((r) => setTimeout(r, 350))
          const after = bar.getBoundingClientRect().top
          const scrolled = window.scrollY
          const vp = document.querySelector('meta[name="viewport"]')?.content || ''
          window.scrollTo(0, 0)
          await new Promise((r) => setTimeout(r, 150))
          return JSON.stringify({
            pos, after, scrolled,
            zoomLocked: vp.includes('user-scalable=no'),
          })
        })()`)
        let p = {}
        try { p = JSON.parse(probe || '{}') } catch { p = {} }
        check('移动端顶部栏吸顶（滚动 900px 后仍在视口顶部）',
          p.pos === 'sticky' && Math.abs(p.after) <= 1, probe)
        check('移动端禁用双击缩放（viewport 已锁定）', p.zoomLocked === true, probe)
      }
    }

    // PC 主视口：全量页面
    await browser.send('Emulation.setDeviceMetricsOverride', {
      width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
    })
    await sleep(350)
    await auditPages('PC 1440×900', PAGES)
    await auditLayout('PC 1440×900', true, false)
    await browser.screenshot(path.join(SHOTS, '05-pc-dashboard.png'))

    // 根路径必须直达工作台（此前没有 '/' 路由，访问域名会落到无匹配页）
    await browser.goto('/')
    const rootProbe = await browser.eval(`JSON.stringify({ path: location.pathname })`)
    let rootState = {}
    try { rootState = JSON.parse(rootProbe || '{}') } catch { rootState = {} }
    check('访问根路径直达脉枢台', rootState.path === '/dashboard', rootProbe)

    // 常见的较小 PC 分辨率：关键页抽查
    await browser.send('Emulation.setDeviceMetricsOverride', {
      width: 1366, height: 768, deviceScaleFactor: 1, mobile: false,
    })
    await sleep(300)
    await auditPages('PC 1366×768 关键页', KEY_PAGES)
    await auditLayout('PC 1366×768', true, false)
    await browser.screenshot(path.join(SHOTS, '06-pc-1366-accounts.png'))

    // 回到桌面主视口，后续交互场景都在 PC 布局下进行
    await browser.send('Emulation.setDeviceMetricsOverride', {
      width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
    })
    await sleep(300)

    // 4.5 账户页数据一致性
    const accountsPage = await browser.goto('/ledger/accounts', '账户')
    const pageText = await browser.eval('document.body.innerText')
    check('账户页显示造数账户', Boolean(pageText && pageText.includes('E2E 储蓄卡') && pageText.includes('E2E 信用卡')))
    // 储蓄卡余额 = 期初 10000 − 支出 200 + 收入 3000 = 12800；信用卡可用 = 50000 − 12345.67
    const amounts2 = (pageText || '').match(/-?\d[\d,]*\.\d{2,4}/g) || []
    const has = (v) => amounts2.some((a) => a.replace(/,/g, '') === v)
    check('账户页金额为两位小数', has('12800.00') && has('37654.33'),
      `命中金额：${amounts2.slice(0, 8).join(' ')}`)
    // 同一页面不应混用「带千分位」与「裸数字」两种金额写法
    const unformatted = (pageText || '').match(/(?<![\d,])\d{5,}\.\d{2}(?!\d)/g) || []
    check('账户页无未格式化的长金额', unformatted.length === 0, unformatted.slice(0, 5).join(' '))

    // 浏览器标签：平台名在前（财资脉枢 · 账户）
    const titleNow = await browser.eval('document.title')
    check('网页标题以平台名开头（财资脉枢 · 页面名）',
      String(titleNow || '').startsWith('财资脉枢 · '), String(titleNow))

    // 账户卡片 + 右上角标
    const cardRaw = await browser.eval(`JSON.stringify({
      cards: document.querySelectorAll('.acc-card').length,
      corners: Array.from(document.querySelectorAll('.acc-card .corner')).map((e) => e.textContent.trim()),
    })`)
    let cardInfo = {}
    try { cardInfo = JSON.parse(cardRaw || '{}') } catch { cardInfo = {} }
    check('账户列表为卡片式（带类型角标）',
      (cardInfo.cards || 0) >= 3 && (cardInfo.corners || []).length === cardInfo.cards
        && (cardInfo.corners || []).some((t) => t === '信用卡' || t === '信用类'),
      cardRaw)
    await browser.screenshot(path.join(SHOTS, '07-accounts-cards.png'))

    const dueRaw = await browser.eval(`JSON.stringify({
      count: document.querySelectorAll('.acc-card .acc-due').length,
      text: (document.querySelector('.acc-card .acc-due')?.textContent || '').replace(/\s+/g, ' ').trim(),
    })`)
    let dueInfoCard = {}
    try { dueInfoCard = JSON.parse(dueRaw || '{}') } catch { dueInfoCard = {} }
    check('还款日账户卡片显示还款日与倒计时',
      (dueInfoCard.count || 0) >= 1 && /还款日/.test(dueInfoCard.text || '')
        && /(还有 \d+ 天|今天还款)/.test(dueInfoCard.text || ''),
      dueRaw)

    // 电子钱包：分类字段改名为「开户机构」，且不再出现第二个开户机构输入
    await browser.eval(`(() => {
      const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent.includes('新增账户'))
      if (btn) btn.click()
      return Boolean(btn)
    })()`)
    await sleep(500)
    const typeSwitched = await browser.eval(`(() => {
      const fieldOf = (label) => Array.from(document.querySelectorAll('.drawer-panel .field')).find((f) => {
        const l = f.querySelector('label.lbl');
        return l && l.textContent.replace(/\\s+/g, '').startsWith(label);
      });
      const typeField = fieldOf('账户类型');
      const sel = typeField && typeField.querySelector('select');
      if (!sel) return 'NO_TYPE_SELECT';
      const opt = Array.from(sel.options).find((o) => o.textContent.includes('电子钱包'));
      if (!opt) return 'NO_WALLET_OPTION';
      sel.value = opt.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      return 'OK';
    })()`)
    await sleep(600)
    const walletRaw = await browser.eval(`(() => {
      const labels = Array.from(document.querySelectorAll('.drawer-panel .field label.lbl'))
        .map((l) => l.textContent.replace(/\\s+/g, '').replace(/\\*$/, ''));
      const instFields = labels.filter((l) => l.startsWith('开户机构'));
      const instField = Array.from(document.querySelectorAll('.drawer-panel .field')).find((f) => {
        const l = f.querySelector('label.lbl');
        return l && l.textContent.replace(/\\s+/g, '').startsWith('开户机构');
      });
      const options = instField
        ? Array.from(instField.querySelectorAll('select option')).map((o) => o.textContent.trim()).filter(Boolean)
        : [];
      return JSON.stringify({ labels, instFields, options });
    })()`)
    let walletInfo = {}
    try { walletInfo = JSON.parse(walletRaw || '{}') } catch { walletInfo = {} }
    walletInfo.typeSwitched = typeSwitched
    await browser.eval(`(() => { document.querySelector('.drawer-head .close')?.click(); return true })()`)
    check('电子钱包：二级分类即开户机构（只有一个字段）',
      typeSwitched === 'OK' && (walletInfo.instFields || []).length === 1
        && (walletInfo.options || []).includes('支付宝')
        && !(walletInfo.options || []).includes('电子钱包'),
      `${walletRaw} typeSwitched=${typeSwitched}`)
    await sleep(300)
    await sleep(300)

    // 4.5b 表单级：在抽屉里真实新增一张信用卡（含越界校验 → 修正 → 提交）
    browser.resetSignals()
    const formFill = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const fields = () => Array.from(document.querySelectorAll('.field'));
      const fieldOf = (label) => fields().find((f) => {
        const l = f.querySelector('label.lbl');
        return l && l.textContent.replace(/\\s+/g, '').startsWith(label);
      });
      const setInput = (label, value) => {
        const f = fieldOf(label); if (!f) return 'NO_FIELD:' + label;
        const el = f.querySelector('input, select, textarea'); if (!el) return 'NO_CTRL:' + label;
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return 'OK';
      };
      const setSelect = (label, inc) => {
        const f = fieldOf(label); if (!f) return 'NO_FIELD:' + label;
        const el = f.querySelector('select'); if (!el) return 'NO_SELECT:' + label;
        const opt = Array.from(el.options).find((o) => o.textContent.includes(inc));
        if (!opt) return 'NO_OPTION:' + Array.from(el.options).map((o) => o.textContent.trim()).join('|');
        el.value = opt.value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return 'OK';
      };

      const addBtn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent.includes('新增账户'));
      if (!addBtn) return JSON.stringify({ error: 'NO_ADD_BUTTON' });
      addBtn.click();
      await sleep(500);
      if (!document.querySelector('.drawer-panel')) return JSON.stringify({ error: 'NO_DRAWER' });

      const log = {};
      log['账户类型'] = setSelect('账户类型', '银行卡');
      await sleep(180);
      log['分类'] = setSelect('分类', '信用卡');
      await sleep(420);
      log['开户机构'] = setSelect('开户机构', '招商银行');
      await sleep(120);
      log['信用额度'] = setInput('信用额度', '60000');
      log['当前已用额度'] = setInput('当前已用额度', '10000');
      await sleep(140);
      log['卡片有效期'] = setInput('卡片有效期', '2030-01');
      log['账单日'] = setInput('账单日', '31');
      log['还款日'] = setInput('还款日', '25');
      log['账户名称'] = setInput('账户名称', 'E2E 表单信用卡');
      log['卡号后四位'] = setInput('卡号后四位', '9999');
      await sleep(180);

      const saveBtn = Array.from(document.querySelectorAll('.drawer-foot button')).find((b) => b.textContent.includes('创建账户'));
      if (!saveBtn) return JSON.stringify({ error: 'NO_SAVE_BUTTON', log });
      saveBtn.click();
      return JSON.stringify({ log });
    })()`)

    const fillInfo = JSON.parse(formFill || '{}')
    check('抽屉表单可填写（分类联动出信用字段）',
      !fillInfo.error && fillInfo.log?.['分类'] === 'OK' && fillInfo.log?.['信用额度'] === 'OK',
      String(formFill).slice(0, 300))

    await sleep(1300)
    const invalid = await browser.eval(`JSON.stringify({
      drawerOpen: Boolean(document.querySelector('.drawer-panel')),
      err: (document.querySelector('.field.has-error .err')?.textContent || '').trim(),
      mentioned: (document.body.innerText || '').includes('1-28')
    })`)
    const invalidInfo = JSON.parse(invalid || '{}')
    check('账单日越界被拦截并提示',
      invalidInfo.drawerOpen === true && (Boolean(invalidInfo.err) || invalidInfo.mentioned === true),
      invalid)

    const fixSave = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const fields = () => Array.from(document.querySelectorAll('.field'));
      const fieldOf = (label) => fields().find((f) => {
        const l = f.querySelector('label.lbl');
        return l && l.textContent.replace(/\\s+/g, '').startsWith(label);
      });
      const f = fieldOf('账单日');
      const el = f && f.querySelector('input');
      if (!el) return 'NO_BILL_INPUT';
      el.value = '8';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      await sleep(220);
      const saveBtn = Array.from(document.querySelectorAll('.drawer-foot button')).find((b) => b.textContent.includes('创建账户'));
      if (!saveBtn) return 'NO_SAVE';
      saveBtn.click();
      return 'SAVED';
    })()`)
    check('修正账单日后可提交', fixSave === 'SAVED', String(fixSave))

    await sleep(2200)
    const created = await browser.eval(`JSON.stringify({
      drawerOpen: Boolean(document.querySelector('.drawer-panel')),
      hasRow: (document.body.innerText || '').includes('E2E 表单信用卡'),
      creditShown: /60,000\\.00/.test(document.body.innerText || '')
    })`)
    const createdInfo = JSON.parse(created || '{}')
    check('抽屉关闭且列表出现新信用卡',
      createdInfo.drawerOpen === false && createdInfo.hasRow === true, created)
    check('新信用卡额度 60,000.00 已展示', createdInfo.creditShown === true, created)
    check('表单新增过程无 5xx / JS 异常',
      browser.pageNetErrors.length === 0 && browser.pageErrors.length === 0,
      [...browser.pageNetErrors, ...browser.pageErrors].slice(0, 3).join(' | '))
    await browser.screenshot(path.join(SHOTS, '04-account-form-created.png'))

    // 4.6 金额精度跨层：后端改 4 位 → 页面同步
    try {
      const token2 = (await post('/auth/login', { username: ADMIN.username, password: ADMIN.password, remember_device: true })).access_token
      const cur = await get('/settings/finance', token2)
      const original = cur.body?.data?.amount_precision ?? 2
      await fetch(`${API}/settings/finance`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token2}` },
        body: JSON.stringify({ amount_precision: 4 }),
      })
      const accAt4 = await get('/ledger/accounts', token2)
      const bal4 = accAt4.body?.data?.items?.find((a) => a.name === 'E2E 储蓄卡')?.balance
      check('精度改为 4 位后接口输出 4 位', bal4 === '12800.0000', `balance=${bal4}`)

      const page4 = await browser.goto('/ledger/accounts', '账户')
      const text4 = await browser.eval('document.body.innerText')
      const amounts4 = (text4 || '').match(/-?\d[\d,]*\.\d{2,4}/g) || []
      check('精度改为 4 位后页面同步显示',
        amounts4.some((a) => a.replace(/,/g, '') === '12800.0000'),
        `命中金额：${amounts4.slice(0, 8).join(' ')}`)

      await fetch(`${API}/settings/finance`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token2}` },
        body: JSON.stringify({ amount_precision: original }),
      })
    } catch (err) {
      check('金额精度跨层验证', false, String(err).slice(0, 200))
    }

    // 5. PC 全功能：各模块真实创建流程（点按钮 → 分阶段填表单 → 提交 → 校验落库）
    const runModule = async (route, title, opts) => {
      browser.resetSignals()
      await browser.goto(route, title)
      await browser.eval(HELPERS)

      const openRaw = await browser.eval(`(async () => {
        const e = window.__e2e;
        const opened = e.clickText(${JSON.stringify(opts.addText)});
        await e.sleep(720);
        return JSON.stringify({ opened, panel: Boolean(document.querySelector('.drawer-panel, [role="dialog"]')) });
      })()`)
      const openInfo = JSON.parse(openRaw || '{}')

      // 分阶段填：部分字段（如「资金来源账户」「关联资产」）要等上游选择后才会出现
      const filledAll = {}
      for (const stage of (opts.stages || [opts.fields])) {
        const one = await browser.eval(`JSON.stringify(window.__e2e.fill(${JSON.stringify(stage)}))`)
        try { Object.assign(filledAll, JSON.parse(one || '{}')) } catch { /* ignore */ }
        await sleep(550)
      }

      const submitRaw = await browser.eval(`(async () => {
        const e = window.__e2e;
        const submitted = e.clickText(${JSON.stringify(opts.submitText)});
        await e.sleep(1500);
        const errs = Array.from(document.querySelectorAll('.drawer-panel .field.has-error')).map((f) => ({
          label: (f.querySelector('label.lbl')?.textContent || '').trim(),
          err: (f.querySelector('.err')?.textContent || '').trim(),
        }));
        const toastText = Array.from(document.querySelectorAll('[class*="toast"]'))
          .map((t) => (t.textContent || '').trim()).filter(Boolean).join(' | ');
        return JSON.stringify({ submitted, errs, toastText });
      })()`)
      let info = {}
      try { info = JSON.parse(submitRaw || '{}') } catch { info = { raw: submitRaw } }

      await sleep(600)
      const text = (await browser.eval('document.body.innerText')) || ''
      const drawerOpen = await browser.eval("Boolean(document.querySelector('.drawer-panel'))")
      const noErr = browser.pageNetErrors.length === 0 && browser.pageErrors.length === 0
      const filledOk = Object.values(filledAll).every((v) => v === 'OK')
      const ok = !openInfo.error && openInfo.panel && filledOk
        && info.submitted === 'CLICKED' && !drawerOpen && text.includes(opts.expect) && noErr
      const detail = ok
        ? `已落库：${opts.expect}`
        : `fill=${JSON.stringify(filledAll)} errs=${JSON.stringify(info.errs || [])} ${info.toastText || ''} | drawerOpen=${drawerOpen} | net=${browser.pageNetErrors[0] || '-'}`
      check(`PC 功能 · ${opts.name}`, ok, detail.slice(0, 340))
      return ok
    }

    await runModule('/ledger/transactions', '流水', {
      name: '流水新增支出（草稿）',
      addText: '新增记录',
      fields: { 事件类型: '支出', 标题: 'E2E 午餐', 金额: '50', 账户: 'E2E 储蓄卡' },
      submitText: '保存草稿',
      expect: 'E2E 午餐',
    })

    await runModule('/ledger/assets', '资产', {
      name: '资产登记（不动产 · 直接确认购置）',
      addText: '新增资产',
      stages: [
        { 资产类型: '不动产', 分类: '住宅', 资产名称: 'E2E 测试房产', 购入价格: '800000', 登记方式: '待扣资金资产' },
        { 资金来源账户: 'E2E 储蓄卡' },
      ],
      submitText: '创建资产',
      expect: 'E2E 测试房产',
    })

    await runModule('/ledger/liabilities', '负债', {
      name: '负债新增（房贷 · 关联房产）',
      addText: '新增负债',
      stages: [
        { 债务类型: '房贷', 债务名称: 'E2E 测试房贷', 贷款金额: '1000000', 剩余金额: '800000', 年利率: '4.2' },
        { 关联资产: 'E2E 测试房产', 还款账户: 'E2E 储蓄卡' },
      ],
      submitText: '创建负债',
      expect: 'E2E 测试房贷',
    })

    await runModule('/ledger/investments', '投资', {
      name: '投资账户新增（证券账户）',
      addText: '新增投资账户',
      fields: { 账户类型: '证券账户', 账户名称: 'E2E 证券账户', 初始可用资金: '50000' },
      submitText: '创建账户',
      expect: 'E2E 证券账户',
    })

    await runModule('/ledger/leases', '租赁', {
      name: '租约新增（关联上一步房产）',
      addText: '新建租约',
      fields: { 关联房产: 'E2E 测试房产', 租客名称: 'E2E 租客', 月租: '5000', 收款账户: 'E2E 储蓄卡' },
      submitText: '保存租约',
      expect: 'E2E 租客',
    })

    await runModule('/settings/categories', '分类管理', {
      name: '分类新增',
      addText: '新增分类',
      fields: { 分类名称: 'E2E 测试分类' },
      submitText: '创建',
      expect: 'E2E 测试分类',
    })

    // 6. 流水完整状态链：草稿 → 编辑 → 确认 → 冲销
    const TXN_BEFORE = 'E2E 待确认支出'
    const TXN_AFTER = 'E2E 已编辑支出'
    await browser.goto('/ledger/transactions', '流水')
    await browser.eval(HELPERS)
    await browser.eval(`(async () => {
      const e = window.__e2e;
      e.clickText('新增记录'); await e.sleep(700);
      e.fill({ 事件类型: '支出', 标题: ${JSON.stringify(TXN_BEFORE)}, 金额: '30', 账户: 'E2E 储蓄卡' });
      await e.sleep(260);
      e.clickText('保存草稿');
      await e.sleep(1700);
      return 'OK';
    })()`)

    // 6a 编辑草稿标题
    const editRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const e = window.__e2e;
      const findRow = () => Array.from(document.querySelectorAll('tbody tr'))
        .find((tr) => tr.innerText.includes(${JSON.stringify(TXN_BEFORE)}));
      const row = findRow();
      if (!row) return JSON.stringify({ error: 'ROW_NOT_FOUND' });
      const editBtn = Array.from(row.querySelectorAll('button')).find((b) => b.textContent.includes('编辑'));
      if (!editBtn) {
        const buttons = Array.from(row.querySelectorAll('button')).map((b) => b.textContent.trim());
        return JSON.stringify({ error: 'NO_EDIT_BUTTON', buttons });
      }
      editBtn.click();
      await sleep(950);
      const filled = e.fill({ 标题: ${JSON.stringify(TXN_AFTER)} });
      await sleep(280);
      const saved = e.clickText('保存修改');
      await sleep(1600);
      const text = document.body.innerText || '';
      return JSON.stringify({ filled, saved, renamed: text.includes(${JSON.stringify(TXN_AFTER)}) });
    })()`)
    let editInfo = {}
    try { editInfo = JSON.parse(editRaw || '{}') } catch { editInfo = { raw: editRaw } }
    check('流水草稿可编辑并保存',
      !editInfo.error && editInfo.saved === 'CLICKED' && editInfo.renamed === true,
      JSON.stringify(editInfo).slice(0, 300))

    // 6b 确认草稿
    const confirmRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const findRow = () => Array.from(document.querySelectorAll('tbody tr'))
        .find((tr) => tr.innerText.includes(${JSON.stringify(TXN_AFTER)}));
      const row = findRow();
      if (!row) return JSON.stringify({ error: 'ROW_NOT_FOUND' });
      const buttons = Array.from(row.querySelectorAll('button')).map((b) => b.textContent.trim());
      const btn = Array.from(row.querySelectorAll('button')).find((b) => b.textContent.includes('确认'));
      if (!btn) return JSON.stringify({ error: 'NO_CONFIRM_BUTTON', buttons });
      btn.click();
      await sleep(1200);
      const dlg = Array.from(document.querySelectorAll('[role="dialog"] button'))
        .find((b) => /确定|确认/.test(b.textContent));
      if (dlg) { dlg.click(); await sleep(1500); }
      const after = findRow();
      return JSON.stringify({ buttons, afterText: after ? after.innerText.replace(/\\s+/g, ' ').slice(0, 180) : '(行已消失)' });
    })()`)
    let confirmInfo = {}
    try { confirmInfo = JSON.parse(confirmRaw || '{}') } catch { confirmInfo = { raw: confirmRaw } }
    check('流水草稿可确认（状态机）',
      !confirmInfo.error && /已确认/.test(confirmInfo.afterText || ''),
      JSON.stringify(confirmInfo).slice(0, 300))

    // 6c 冲销已确认事件
    const reverseRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const findRow = () => Array.from(document.querySelectorAll('tbody tr'))
        .find((tr) => tr.innerText.includes(${JSON.stringify(TXN_AFTER)}));
      const row = findRow();
      if (!row) return JSON.stringify({ error: 'ROW_NOT_FOUND' });
      const btn = Array.from(row.querySelectorAll('button')).find((b) => b.textContent.includes('冲销'));
      if (!btn) {
        const buttons = Array.from(row.querySelectorAll('button')).map((b) => b.textContent.trim());
        return JSON.stringify({ error: 'NO_REVERSE_BUTTON', buttons });
      }
      btn.click();
      await sleep(900);
      // 冲销必须填原因（ConfirmDialog requireReason），否则提交会被拦下
      const reason = document.querySelector('#confirm-reason');
      if (reason) {
        reason.value = 'E2E 自动化：验证冲销流程';
        reason.dispatchEvent(new Event('input', { bubbles: true }));
        await sleep(180);
      }
      const dlg = Array.from(document.querySelectorAll('[role="dialog"] button'))
        .find((b) => /确认冲销|确认|确定/.test(b.textContent.trim()));
      const dlgText = dlg ? dlg.textContent.trim() : 'NO_DLG';
      if (dlg) { dlg.click(); await sleep(1800); }
      const stillOpen = Boolean(document.querySelector('#confirm-reason'));
      const toastText = Array.from(document.querySelectorAll('[class*="toast"]'))
        .map((t) => (t.textContent || '').trim()).filter(Boolean).join(' | ');
      const dialogCount = document.querySelectorAll('[role="dialog"]').length;
      const kindHint = (document.body.innerText || '').includes('冲销业务事件') ? 'has-reverse-dialog' : 'no-reverse-dialog';
      const after = findRow();
      const afterText = after ? after.innerText.replace(/\\s+/g, ' ').slice(0, 160) : '(原行已消失)';
      return JSON.stringify({
        dlgText, stillOpen, dialogCount, kindHint, toastText: toastText.slice(0, 60),
        afterText, reversed: /已冲销|已作废|已撤销/.test(afterText),
      });
    })()`)
    let reverseInfo = {}
    try { reverseInfo = JSON.parse(reverseRaw || '{}') } catch { reverseInfo = { raw: reverseRaw } }
    await browser.screenshot(path.join(SHOTS, '08-reverse.png'))
    check('已确认流水可冲销', reverseInfo.reversed === true, JSON.stringify(reverseInfo).slice(0, 300))

    // 6d 退款：从已确认支出发起 → 抽屉预填 → 生成退款草稿
    const REFUND_SRC = 'E2E 可退款支出'
    await browser.goto('/ledger/transactions', '流水')
    await browser.eval(HELPERS)
    await browser.eval(`(async () => {
      const e = window.__e2e;
      e.clickText('新增记录'); await e.sleep(700);
      e.fill({ 事件类型: '支出', 标题: ${JSON.stringify(REFUND_SRC)}, 金额: '300', 账户: 'E2E 储蓄卡' });
      await e.sleep(260);
      e.clickText('保存草稿'); await e.sleep(1600);
      return 'OK';
    })()`)
    await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const row = Array.from(document.querySelectorAll('tbody tr'))
        .find((tr) => tr.innerText.includes(${JSON.stringify(REFUND_SRC)}));
      const btn = row && Array.from(row.querySelectorAll('button')).find((b) => b.textContent.includes('确认'));
      if (btn) { btn.click(); await sleep(1100); }
      const dlg = Array.from(document.querySelectorAll('[role="dialog"] button'))
        .find((b) => /确定|确认/.test(b.textContent));
      if (dlg) { dlg.click(); await sleep(1400); }
      return 'OK';
    })()`)

    const refundRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const e = window.__e2e;
      const row = Array.from(document.querySelectorAll('tbody tr'))
        .find((tr) => tr.innerText.includes(${JSON.stringify(REFUND_SRC)}));
      if (!row) return JSON.stringify({ error: 'ROW_NOT_FOUND' });
      const btn = Array.from(row.querySelectorAll('button')).find((b) => b.textContent.includes('退款'));
      if (!btn) {
        return JSON.stringify({ error: 'NO_REFUND_BUTTON',
          buttons: Array.from(row.querySelectorAll('button')).map((b) => b.textContent.trim()) });
      }
      btn.click();
      await sleep(900);
      const drawerTitle = (document.querySelector('.drawer-head')?.innerText || '').trim().slice(0, 20);
      const filled = e.fill({ 金额: '100' });
      await sleep(250);
      const saved = e.clickText('保存草稿');
      await sleep(1700);
      const text = document.body.innerText || '';
      return JSON.stringify({ drawerTitle, filled, saved, listed: text.includes('退款 · ' + ${JSON.stringify(REFUND_SRC)}) });
    })()`)
    let refundInfo = {}
    try { refundInfo = JSON.parse(refundRaw || '{}') } catch { refundInfo = { raw: refundRaw } }
    check('支出可发起退款（抽屉预填 → 退款草稿）',
      !refundInfo.error && refundInfo.saved === 'CLICKED' && refundInfo.listed === true,
      JSON.stringify(refundInfo).slice(0, 300))

    // 7. 对账：选账户 → 加载 → 提交对账
    await browser.goto('/ledger/reconciliation', '对账')
    const reconRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const norm = (s) => (s || '').replace(/\\s+/g, '').replace(/\\*$/, '');
      const fieldOf = (label) => Array.from(document.querySelectorAll('.field')).find((f) => {
        const l = f.querySelector('label.lbl');
        return l && norm(l.textContent).startsWith(norm(label));
      });
      const set = (label, value) => {
        const f = fieldOf(label); if (!f) return 'NO_FIELD:' + label;
        const el = f.querySelector('input, select, textarea'); if (!el) return 'NO_CTRL';
        if (el.tagName === 'SELECT') {
          const opt = Array.from(el.options).find((o) => o.textContent.includes(value));
          if (!opt) return 'NO_OPTION';
          el.value = opt.value;
        } else { el.value = value; }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return 'OK';
      };
      const click = (text) => {
        const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.includes(text));
        if (!b) return 'NO_BUTTON';
        b.click(); return 'CLICKED';
      };

      const sel = document.querySelector('select');
      if (!sel) return JSON.stringify({ error: 'NO_SELECT' });
      const opt = Array.from(sel.options).find((o) => o.textContent.includes('E2E 储蓄卡'));
      if (!opt) return JSON.stringify({ error: 'NO_ACCOUNT_OPTION' });
      sel.value = opt.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      await sleep(400);
      const loaded = click('加载');
      await sleep(1700);

      const fieldLabels = Array.from(document.querySelectorAll('.field'))
        .map((f) => (f.querySelector('label.lbl')?.textContent || '').trim()).filter(Boolean);
      const setActual = set('人工实际余额', '13000');
      const setReason = set('差额说明', 'E2E 对账差异');
      await sleep(260);
      const submitted = click('提交对账');
      await sleep(1700);
      const toastText = Array.from(document.querySelectorAll('[class*="toast"]'))
        .map((t) => t.textContent.trim()).filter(Boolean).join(' | ');
      return JSON.stringify({ loaded, fieldLabels, setActual, setReason, submitted, toastText });
    })()`)
    let reconInfo = {}
    try { reconInfo = JSON.parse(reconRaw || '{}') } catch { reconInfo = { raw: reconRaw } }
    check('对账提交（选账户 → 加载 → 提交）',
      reconInfo.submitted === 'CLICKED' && reconInfo.setActual === 'OK' && reconInfo.setReason === 'OK',
      JSON.stringify(reconInfo).slice(0, 320))

    // 10. 批量导入（CSV）：预览 → 生成批次 → 批量确认 → 流水可见
    await browser.goto('/ledger/capture', '智能录入')
    browser.resetSignals()
    const importRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const click = (text, scope) => {
        const root = scope || document;
        const b = Array.from(root.querySelectorAll('button')).find((x) => x.textContent.includes(text));
        if (!b) return 'NO_BUTTON';
        b.click(); return 'CLICKED';
      };
      const csv = '类型,标题,金额,日期,账户,分类,备注\\n'
        + '支出,E2E 导入餐饮,88,2026-10-15,E2E 储蓄卡,餐饮,E2E 导入测试\\n'
        + '收入,E2E 导入工资,5000,2026-10-16,E2E 储蓄卡,,E2E 导入测试';

      click('数据导入');
      await sleep(700);
      const area = document.querySelector('.csv-area');
      if (!area) return JSON.stringify({ error: 'NO_CSV_AREA' });
      area.value = csv;
      area.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(220);

      click('预览');
      await sleep(1600);
      const preview = (document.querySelector('.imp-sum')?.textContent || '').trim();

      click('生成导入批次');
      await sleep(1900);
      const batchText = (document.querySelector('.imp-batches')?.innerText || '').replace(/\\s+/g, ' ').slice(0, 140);

      const confirmBtn = Array.from(document.querySelectorAll('.imp-batches button'))
        .find((b) => /确认/.test(b.textContent));
      let confirmed = false;
      if (confirmBtn) {
        confirmBtn.click();
        await sleep(900);
        const dlg = Array.from(document.querySelectorAll('[role="dialog"] button'))
          .find((b) => /确认导入|确认|确定/.test(b.textContent));
        if (dlg) { dlg.click(); await sleep(1900); confirmed = true; }
      }
      const toastText = Array.from(document.querySelectorAll('[class*="toast"]'))
        .map((t) => (t.textContent || '').trim()).filter(Boolean).join(' | ');
      return JSON.stringify({ preview, batchText, confirmed, toastText: toastText.slice(0, 80) });
    })()`)
    let importInfo = {}
    try { importInfo = JSON.parse(importRaw || '{}') } catch { importInfo = { raw: importRaw } }
    check('CSV 导入预览与建批', /成功/.test(importInfo.preview || '') && Boolean(importInfo.batchText),
      JSON.stringify(importInfo).slice(0, 300))
    check('导入批次可批量确认', importInfo.confirmed === true && /确认/.test(importInfo.toastText || ''),
      JSON.stringify(importInfo).slice(0, 260))

    const importedPage = await browser.goto('/ledger/transactions', '流水')
    const importedText = await browser.eval("document.body.innerText || ''")
    check('导入数据在流水页可见', importedPage.ok && importedText.includes('E2E 导入餐饮'),
      importedPage.ok ? '未找到导入记录' : '流水页未渲染')

    // 11. 备份：创建备份 → 列表出现记录
    await browser.goto('/settings/backup', '数据备份与恢复')
    await browser.eval(HELPERS)
    const backupRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const clicked = window.__e2e.clickText('创建备份');
      await sleep(900);
      // 创建备份需要二次确认（确认创建），确认后还要换 confirm token
      const dlg = Array.from(document.querySelectorAll('[role="dialog"] button'))
        .find((b) => /确认创建|确认|确定/.test(b.textContent));
      const confirmed = Boolean(dlg);
      if (dlg) dlg.click();
      let hasRecord = false;
      for (let i = 0; i < 20; i++) {
        await sleep(500);
        hasRecord = !(document.body.innerText || '').includes('还没有备份');
        if (hasRecord) break;
      }
      const toastText = Array.from(document.querySelectorAll('[class*="toast"]'))
        .map((t) => (t.textContent || '').trim()).filter(Boolean).join(' | ');
      return JSON.stringify({ clicked, confirmed, hasRecord, toastText: toastText.slice(0, 80) });
    })()`)
    let backupInfo = {}
    try { backupInfo = JSON.parse(backupRaw || '{}') } catch { backupInfo = { raw: backupRaw } }
    check('创建备份并出现在列表', backupInfo.clicked === 'CLICKED' && backupInfo.hasRecord === true,
      JSON.stringify(backupInfo).slice(0, 260))

    // 12. OCR 附件上传（覆盖上传链路与安全校验；识别本身依赖外部 OCR 服务）
    const adminToken = (await post('/auth/login', {
      username: ADMIN.username, password: ADMIN.password, remember_device: true,
    })).access_token
    const samplePath = path.join(SHOTS, 'upload-sample.png')
    fs.writeFileSync(samplePath, Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
      'base64'))
    await browser.goto('/ledger/capture', '智能录入')
    await browser.send('DOM.enable')
    await browser.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '上传识别');
      if (b) b.click();
      return 'OK';
    })()`)
    await sleep(700)
    const fileSet = await browser.setFile('input[type=file]', samplePath)
    await sleep(400)
    const uploadClicked = await browser.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '上传');
      if (!b) return 'NO_UPLOAD_BTN';
      b.click(); return 'CLICKED';
    })()`)
    await sleep(2600)
    const attachText = await browser.eval("document.body.innerText || ''")
    check('OCR 附件上传成功', fileSet === true && uploadClicked === 'CLICKED' && attachText.includes('upload-sample'),
      `setFile=${fileSet} click=${uploadClicked}`)

    // 13. 提醒：六类实时计算 + 发送链路
    const remRes = await get('/ledger/reminders', adminToken)
    const groups = remRes.body?.data?.groups ?? []
    check('提醒实时计算可返回', remRes.status === 200 && Array.isArray(groups),
      `status=${remRes.status} groups=${groups.length}`)

    const sendRes = await fetch(`${API}/ledger/reminders/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
      body: JSON.stringify({}),
    })
    const sendBody = await sendRes.json().catch(() => ({}))
    check('提醒发送链路可用（未配置渠道也不报 500）',
      sendRes.status === 200 && sendBody?.data !== undefined,
      `status=${sendRes.status} ${JSON.stringify(sendBody?.data || {}).slice(0, 120)}`)

    // 14. 备份：完整性校验 + 恢复预览 + 恢复请求（确认不执行破坏性回滚）
    const listRes = await get('/settings/backup', adminToken)
    const backupRows = listRes.body?.data?.items ?? []
    check('备份列表可读', Array.isArray(backupRows) && backupRows.length > 0, `count=${backupRows.length}`)
    const backupId = backupRows[0]?.id
    if (backupId) {
      const verifyRes = await post(`/settings/backup/${backupId}/verify`, {}, adminToken)
      check('备份完整性校验', Boolean(verifyRes && (verifyRes.status || verifyRes.checksum)),
        JSON.stringify(verifyRes || {}).slice(0, 140))

      const previewRes = await get(`/settings/backup/${backupId}/restore-preview`, adminToken)
      const phrase = previewRes.body?.data?.confirm_phrase_required
      check('恢复预览返回确认短语', previewRes.status === 200 && Boolean(phrase), `phrase=${phrase}`)

      const beforeCount = (await get('/ledger/accounts', adminToken)).body?.data?.total ?? -1

      const badRestore = await fetch(`${API}/settings/backup/${backupId}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
        body: JSON.stringify({ confirm_phrase: '错误短语' }),
      })
      check('恢复短语错误被拒绝', badRestore.status === 422, `status=${badRestore.status}`)

      const goodRestore = await fetch(`${API}/settings/backup/${backupId}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
        body: JSON.stringify({ confirm_phrase: phrase }),
      })
      const goodBody = await goodRestore.json().catch(() => ({}))
      check('恢复请求记录为「已提交（待运维执行）」',
        goodRestore.status === 200 && goodBody?.data?.status === 'preview'
        && String(goodBody?.data?.status_label || '').includes('待运维'),
        `${goodRestore.status} ${goodBody?.data?.status_label || ''}`)

      const afterCount = (await get('/ledger/accounts', adminToken)).body?.data?.total ?? -1
      check('恢复请求不执行破坏性回滚（数据未被改动）', beforeCount === afterCount,
        `before=${beforeCount} after=${afterCount}`)
    }

    // 4.6.9 借出款入口：总览卡片 → 资产新增（预选借出款，含借款人字段）
    await browser.goto('/ledger', '家庭台账总览')
    const recEntry = await browser.eval(`(() => {
      const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent.includes('借出 / 收回'))
      if (btn) btn.click()
      return Boolean(btn)
    })()`)
    await sleep(1100)
    const recRaw = await browser.eval(`JSON.stringify({
      path: location.pathname,
      hasDrawer: Boolean(document.querySelector('.drawer-panel')),
      hasBorrower: Array.from(document.querySelectorAll('.drawer-panel .field label.lbl'))
        .some((l) => l.textContent.includes('借款人')),
    })`)
    let recInfo = {}
    try { recInfo = JSON.parse(recRaw || '{}') } catch { recInfo = {} }
    check('借出款入口可用（总览 → 资产新增，含借款人字段）',
      recEntry === true && recInfo.path === '/ledger/assets' && recInfo.hasDrawer === true
        && recInfo.hasBorrower === true,
      recRaw)
    await browser.eval(`(() => { document.querySelector('.drawer-head .close')?.click(); return true })()`)
    await sleep(300)

    // 4.6.10 侧栏收起/展开：宽度平滑过渡，文字淡出但不被移除
    await browser.goto('/dashboard', '脉枢台')
    const collapseRaw = await browser.eval(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const side = document.querySelector('.shell-sidebar');
      const btn = () => Array.from(document.querySelectorAll('.shell-sidebar button'))
        .find((b) => b.textContent.includes('收起侧栏'));
      if (side.getBoundingClientRect().width < 120) { btn()?.click(); await sleep(450); }
      const before = Math.round(side.getBoundingClientRect().width);
      btn()?.click();
      await sleep(120);
      const mid = Math.round(side.getBoundingClientRect().width);
      await sleep(450);
      const end = Math.round(side.getBoundingClientRect().width);
      const label = document.querySelector('.nav-item .label');
      const opacity = label ? getComputedStyle(label).opacity : '';
      const cs = getComputedStyle(side);
      const transition = cs.transitionProperty + ' ' + cs.transitionDuration;
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      btn()?.click();
      await sleep(450);
      return JSON.stringify({ before, mid, end, opacity, transition, reduced,
        restored: Math.round(side.getBoundingClientRect().width) });
    })()`)
    let collapseInfo = {}
    try { collapseInfo = JSON.parse(collapseRaw || '{}') } catch { collapseInfo = {} }
    check('侧栏收起/展开为平滑过渡（宽度动画、文字淡出、可恢复）',
      collapseInfo.before > 200 && collapseInfo.end === 72
        && String(collapseInfo.transition || '').includes('width')
        && Number(collapseInfo.opacity) === 0 && collapseInfo.restored > 200,
      collapseRaw)

    // 4.7 移动视口：全量页面 + 布局切换（侧栏隐藏、底部导航显示）
    await browser.send('Emulation.setDeviceMetricsOverride', {
      width: 375, height: 812, deviceScaleFactor: 2, mobile: true,
    })
    await sleep(500)
    await auditPages('移动 375×812', PAGES)
    await auditLayout('移动 375×812', false, true)
    await browser.goto('/dashboard', '脉枢台')
    await browser.screenshot(path.join(SHOTS, '07-mobile-dashboard.png'))

    // --shots：给关键页补移动端截图（人工审查移动布局用）
    if (process.argv.includes('--shots')) {
      const shotDir = path.join(SHOTS, 'mobile')
      fs.mkdirSync(shotDir, { recursive: true })
      const targets = [
        ['/dashboard', '脉枢台', '01-dashboard'],
        ['/ledger', '家庭台账总览', '02-ledger'],
        ['/ledger/accounts', '账户', '03-accounts'],
        ['/ledger/transactions', '流水', '04-transactions'],
        ['/ledger/assets', '资产', '05-assets'],
        ['/ledger/liabilities', '负债', '06-liabilities'],
        ['/ledger/investments', '投资', '07-investments'],
        ['/ledger/reconciliation', '对账', '08-reconciliation'],
      ]
      for (const [route, title, name] of targets) {
        await browser.goto(route, title)
        await sleep(500)
        await browser.screenshot(path.join(shotDir, `${name}.png`))
      }
      console.log(`移动端截图已写入 ${shotDir}`)
    }

    await browser.send('Emulation.clearDeviceMetricsOverride')
    await sleep(300)

    // 9. 权限矩阵：只读成员只能看、不能写
    const adminLogin = await post('/auth/login', {
      username: ADMIN.username, password: ADMIN.password, remember_device: true,
    })
    const member = await post('/settings/members', {
      username: 'e2eviewer', display_name: 'E2E 只读成员',
      password: 'ViewerPass!234', role: 'readonly',
    }, adminLogin.access_token)
    check('创建只读成员', member.role === 'readonly', `id=${member.id}`)

    await browser.eval('localStorage.clear()')
    await browser.goto('/login', '登录')
    await browser.eval(`(() => {
      const u = document.querySelector('#login-username');
      const p = document.querySelector('#login-password');
      if (!u || !p) return 'NO_FORM';
      u.value = 'e2eviewer'; u.dispatchEvent(new Event('input', { bubbles: true }));
      p.value = 'ViewerPass!234'; p.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('form').requestSubmit();
      return 'SUBMITTED';
    })()`)
    await sleep(3200)
    const viewerPath = await browser.eval('location.pathname')
    check('只读成员可登录', viewerPath === '/dashboard', `path=${viewerPath}`)

    await browser.goto('/ledger/accounts', '账户')
    const hasCreateBtn = await browser.eval(
      "Array.from(document.querySelectorAll('button')).some((b) => b.textContent.includes('新增账户'))")
    check('只读成员看不到新增入口', hasCreateBtn === false, `hasCreateBtn=${hasCreateBtn}`)

    const viewerToken = await browser.eval("localStorage.getItem('fam.access_token') || ''")
    const readRes = await get('/ledger/accounts', viewerToken)
    check('只读成员可读账户列表', readRes.status === 200, `status=${readRes.status}`)

    const writeRes = await fetch(`${API}/ledger/accounts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${viewerToken}` },
      body: JSON.stringify({ name: '越权账户', account_type: 'cash', currency: 'CNY', opening_balance: '0' }),
    })
    check('只读成员写接口被拒绝（403）', writeRes.status === 403, `status=${writeRes.status}`)

    // 4.8 未登录守卫
    await browser.eval('localStorage.clear()')
    const guard = await browser.goto('/ledger/accounts', '登录')
    check('未登录访问受保护页 → 登录页', guard.ok && guard.path === '/login', `path=${guard.path}`)

    await browser.screenshot(path.join(SHOTS, '03-guard-redirect.png'))
  } catch (err) {
    check('E2E 主流程', false, String(err).slice(0, 300))
  } finally {
    if (browser) browser.close()
    if (chrome) chrome.kill()
    backend.kill()
    frontend.kill()
    await sleep(600)
    writeReport()
  }
}

function writeReport() {
  const total = results.length
  const passed = results.filter((r) => r.ok).length
  const failed = total - passed
  const lines = []
  lines.push('# E2E 全链路测试报告')
  lines.push('')
  lines.push(`- 执行时间：${new Date().toLocaleString('zh-CN')}`)
  lines.push(`- 环境：本地 SQLite（\`backend/data/_e2e.db\`，每次重建）+ 后端 uvicorn + 前端 Vite + Chromium（CDP）`)
  lines.push(`- 结果：**${passed}/${total} 通过**，失败 ${failed}`)
  lines.push('')
  lines.push('## 结果明细')
  lines.push('')
  lines.push('| # | 检查项 | 结果 | 说明 |')
  lines.push('|---|---|---|---|')
  results.forEach((r, i) => {
    const detail = (r.detail || '').replace(/\|/g, '\\|').slice(0, 160)
    lines.push(`| ${i + 1} | ${r.name} | ${r.ok ? '通过' : '**失败**'} | ${detail} |`)
  })
  if (failed) {
    lines.push('')
    lines.push('## 失败详情')
    lines.push('')
    failures.forEach((f) => lines.push(`- **${f.name}**：${(f.detail || '').slice(0, 500)}`))
  }
  lines.push('')
  lines.push('## 这套测试补的是什么缺口')
  lines.push('')
  lines.push('- 后端 pytest（109 项）验证接口契约与计算，**不渲染页面**；')
  lines.push('- 前端单测（12 项）只覆盖路由菜单派生逻辑；')
  lines.push('- 两者都回答不了"点登录会不会跳转""金额显示对不对""某页会不会白屏"。')
  lines.push('')
  lines.push('E2E 用真实 Chromium 打开真实页面，采集页面标题、正文、网络 5xx 与 JS 异常，并跨层比对接口与页面数据。')
  lines.push('')
  lines.push('## E2E 首次运行发现并已修复的问题')
  lines.push('')
  lines.push('1. 登录后「欢迎引导」为全屏模态弹窗，遮住整个首页 → 改为右下角非模态浮层（不遮挡、可关闭）。')
  lines.push('2. 同一页面金额格式不一致（余额 `12,800.00` 带千分位，信用卡额度 `37654.33` 不带）→ 统一 `formatAmount`（千分位 + 家庭精度）。')
  lines.push('3. 登录跳转前 `await` 主题接口、导航失败无兜底、懒加载 chunk 失败无恢复 → 均已加固。')
  lines.push('')
  lines.push('## 覆盖范围')
  lines.push('')
  lines.push('1. 首次启动初始化（数据库配置 → 管理员 → 家庭 → 财务口径 → 完成）')
  lines.push('2. 真实浏览器登录：表单填写 → 提交 → 跳转 `/dashboard` → 首屏渲染 → 引导浮层不遮挡')
  lines.push(`3. 多视口巡检：PC 1440×900（${PAGES.length} 页全量）、PC 1366×768（${KEY_PAGES.length} 页关键）、移动 375×812（${PAGES.length} 页全量）；检查渲染、横向溢出、占位文案、5xx、JS 异常`)
  lines.push('4. 响应式布局切换：PC 显示侧栏且隐藏底部导航；移动隐藏侧栏且显示底部导航')
  lines.push('5. 表单级落库：抽屉内真实新增信用卡（分类联动出额度 / 有效期 / 账单日 / 还款日）→ 越界账单日被拦截 → 修正后保存并回显')
  lines.push('6. **PC 功能级（6 个模块真实操作）**：流水新增支出草稿、资产登记（不动产·直接确认购置）、负债新增（房贷·关联房产）、投资账户新增、租约新增（关联房产）、分类新增；每项都校验抽屉关闭 + 列表回显 + 无 5xx/JS 异常')
  lines.push('7. 账务状态机：流水 草稿 → 编辑 → 确认 → 冲销，每一步都校验列表中的真实状态')
  lines.push('8. 对账流程：选账户 → 加载 → 填人工实际余额与差额说明 → 提交（生成调整事件）')
  lines.push('9. 批量导入：CSV 预览（成功/错误/重复统计）→ 生成导入批次 → 批量确认 → 流水页可见')
  lines.push('10. 数据备份：创建备份（二次确认 + 确认令牌）→ 备份列表出现记录 → 完整性校验 → 恢复预览 → 错误短语被拒 → 恢复请求记录为「已提交（待运维执行）」→ **断言数据未被改动**')
  lines.push('11. OCR 附件上传：真实文件上传 → 附件列表可见（识别依赖外部 OCR 服务，未覆盖真实识别）')
  lines.push('12. 提醒：六类提醒实时计算（groups）+ 发送链路（未配置渠道时 failed 计数，不 500）')
  lines.push('13. 权限矩阵：新建只读成员 → 可登录、无「新增」入口、可读接口、写接口返回 403')
  lines.push('14. 跨层一致性：信用卡可用额度计算、账户页金额与接口一致、金额精度改 4 位后页面同步')
  lines.push('15. 路由守卫：清除登录态后访问受保护页应回登录页')
  lines.push('')
  lines.push('## 重跑方式')
  lines.push('')
  lines.push('```bash')
  lines.push('node deploy/e2e/run.mjs')
  lines.push('```')
  lines.push('')
  lines.push('每次运行都会重建数据库并清空旧截图，失败页会保留截图在 `deploy/e2e/screenshots/`。')
  fs.writeFileSync(path.join(ROOT, 'E2E-全链路测试报告.md'), lines.join('\n'), 'utf-8')
  console.log(`\n报告已生成：E2E-全链路测试报告.md（${passed}/${total} 通过）`)
}

main().catch((err) => {
  console.error(err)
  process.exitCode = 1
})
