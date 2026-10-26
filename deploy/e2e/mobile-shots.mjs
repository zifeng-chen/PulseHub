#!/usr/bin/env node
/**
 * 移动端截图工具（只用于人工审查，不参与门禁）。
 *
 * 复用 E2E 的 SQLite 数据库（backend/data/_e2e.db，已有造数），
 * 启动后端 + 前端 + 无头 Chromium，登录后按 375×812 逐页截图。
 *
 * 用法：node deploy/e2e/mobile-shots.mjs
 * 产物：deploy/e2e/screenshots/mobile/*.png
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
const WEB = 'http://127.0.0.1:5173'
const SHOTS = path.join(ROOT, 'deploy', 'e2e', 'screenshots', 'mobile')

const require = createRequire(path.join(FRONTEND, 'package.json'))
const { WebSocket } = require('ws')

const PAGES = [
  ['/dashboard', '脉枢台', '01-dashboard'],
  ['/ledger', '家庭台账总览', '02-ledger'],
  ['/ledger/accounts', '账户', '03-accounts'],
  ['/ledger/transactions', '流水', '04-transactions'],
  ['/ledger/assets', '资产', '05-assets'],
  ['/ledger/liabilities', '负债', '06-liabilities'],
  ['/ledger/investments', '投资', '07-investments'],
  ['/ledger/leases', '租赁', '08-leases'],
  ['/ledger/reconciliation', '对账', '09-reconciliation'],
  ['/insights', '综合脉诊', '12-insights'],
  ['/insights/expenses', '消费分析', '13-insights-expenses'],
  ['/ledger/capture', '智能录入', '14-capture'],
  ['/ledger/reminders', '提醒', '15-reminders'],
  ['/settings', '设置', '16-settings'],
  ['/settings/categories', '分类管理', '17-categories'],
  ['/settings/finance', '财务口径', '18-finance'],
  ['/ledger/credit-cards', '信用卡', '19-credit-cards'],
  ['/insights/assets', '资产分析', '20-insights-assets'],
  ['/insights/income', '收入分析', '21-insights-income'],
  ['/insights/data-quality', '数据质量', '22-data-quality'],
  ['/settings/household', '家庭与成员', '23-household'],
  ['/settings/ai-providers', '第三方 AI 接口', '24-ai-providers'],
  ['/settings/notifications', '提醒通知', '25-notifications'],
  ['/settings/system', '系统状态与审计', '26-system'],
  ['/settings/backup', '数据备份与恢复', '27-backup'],
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitHttp(url, timeoutMs = 90000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url)
      if (res.ok) return true
    } catch { /* retry */ }
    await sleep(400)
  }
  return false
}

class Cdp {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    ws.on('message', (raw) => {
      const msg = JSON.parse(String(raw))
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        resolve(msg)
      }
    })
  }

  send(method, params = {}) {
    const id = ++this.id
    return new Promise((resolve) => {
      this.pending.set(id, { resolve })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  async eval(expression) {
    const res = await this.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    })
    return res.result?.result?.value
  }

  async goto(url) {
    await this.send('Page.navigate', { url })
    for (let i = 0; i < 60; i += 1) {
      const state = await this.eval('document.readyState')
      if (state === 'complete') return true
      await sleep(200)
    }
    return false
  }

  async screenshot(file) {
    const res = await this.send('Page.captureScreenshot', { format: 'png' })
    const data = res.result?.data
    if (data) fs.writeFileSync(file, Buffer.from(data, 'base64'))
  }
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true })

  const env = { ...process.env, FAM_DATABASE_URL: 'sqlite+aiosqlite:///./data/_e2e.db', FAM_APP_ENV: 'development' }
  const backend = spawn(PY, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'], {
    cwd: BACKEND, env, stdio: 'ignore',
  })
  if (!(await waitHttp('http://127.0.0.1:8000/api/v1/health'))) {
    console.error('后端未就绪'); backend.kill(); process.exit(1)
  }
  const frontend = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--port', '5173', '--strictPort'], {
    cwd: FRONTEND, stdio: 'ignore',
  })
  if (!(await waitHttp(WEB))) {
    console.error('前端未就绪'); backend.kill(); frontend.kill(); process.exit(1)
  }

  const profile = path.join(process.env.TEMP || '.', 'fam-shots-' + Date.now())
  const chrome = spawn(CHROME, [
    '--headless=new', '--remote-debugging-port=9333', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--window-size=375,812', 'about:blank',
  ], { stdio: 'ignore' })

  let ready = false
  for (let i = 0; i < 80; i += 1) {
    try { await fetch('http://127.0.0.1:9333/json/version'); ready = true; break } catch { await sleep(250) }
  }
  if (!ready) { console.error('Chrome 未就绪'); process.exit(1) }

  const target = await (await fetch('http://127.0.0.1:9333/json/new?' + encodeURIComponent(WEB + '/login'), { method: 'PUT' })).json()
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((r) => ws.once('open', r))
  const cdp = new Cdp(ws)
  await cdp.send('Page.enable')

  // 登录
  await cdp.goto(WEB + '/login')
  await sleep(1200)
  await cdp.eval(`(() => {
    const u = document.querySelector('#login-username');
    const p = document.querySelector('#login-password');
    if (!u || !p) return 'NO_FORM';
    u.value = 'e2eadmin'; u.dispatchEvent(new Event('input', { bubbles: true }));
    p.value = 'E2ePass!2345'; p.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('form').requestSubmit();
    return 'SUBMITTED';
  })()`)
  await sleep(3000)

  const isPc = process.argv.includes('--pc')
  await cdp.send('Emulation.setDeviceMetricsOverride', isPc
    ? { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }
    : { width: 375, height: 812, deviceScaleFactor: 2, mobile: true })
  await sleep(400)

  const list = isPc ? PAGES.slice(0, 6) : PAGES
  for (const [route, title, name] of list) {
    await cdp.goto(WEB + route)
    await sleep(1200)
    const body = await cdp.eval('document.body.innerText.length')
    if (process.argv.includes('--probe')) {
      const info = await cdp.eval(`JSON.stringify((() => {
        const tb = document.querySelector('.app-toolbar');
        if (!tb) return { none: true };
        const main = tb.querySelector('.tb-main');
        const cs = main ? getComputedStyle(main) : null;
        const kids = main ? Array.from(main.children).map((el) => ({
          cls: el.className, w: Math.round(el.getBoundingClientRect().width),
          x: Math.round(el.getBoundingClientRect().left),
          minW: getComputedStyle(el).minWidth, flex: getComputedStyle(el).flex,
        })) : [];
        return { tbW: Math.round(tb.getBoundingClientRect().width), mainW: cs && Math.round(main.getBoundingClientRect().width), kids };
      })())`)
      console.log('PROBE', route, info)
    }
    await cdp.screenshot(path.join(isPc ? path.join(SHOTS, '..') : SHOTS, `${isPc ? 'pc-' : ''}${name}.png`))
    console.log(`shot ${route} (${body} chars)`)
  }

  if (process.argv.includes('--sheets')) {
    // 资产：打开筛选弹层
    await cdp.goto(WEB + '/ledger/assets')
    await sleep(1200)
    await cdp.eval(`document.querySelector('.tb-filter')?.click()`)
    await sleep(600)
    await cdp.screenshot(path.join(SHOTS, '10-assets-filter-sheet.png'))

    // 负债：打开新增抽屉，检查表单与底部按钮
    await cdp.goto(WEB + '/ledger/liabilities')
    await sleep(1200)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.includes('新增负债'));
      if (b) b.click(); return true;
    })()`)
    await sleep(900)
    await cdp.screenshot(path.join(SHOTS, '11-liability-form.png'))
  }

  if (process.argv.includes('--plan')) {
    // 负债详情 → 还款计划页签：检查表头对齐与未还金额
    await cdp.goto(WEB + '/ledger/liabilities')
    await sleep(1200)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '详情');
      if (b) b.click(); return true;
    })()`)
    await sleep(1200)
    await cdp.eval(`(() => {
      const t = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '还款计划');
      if (t) t.click(); return true;
    })()`)
    await sleep(800)
    await cdp.screenshot(path.join(SHOTS, '28-plan-tab.png'))
    console.log('plan shot done')
  }

  if (process.argv.includes('--lock')) {
    await cdp.goto(WEB + '/dashboard')
    await sleep(1500)
    // 打开用户菜单 → 锁定屏幕
    await cdp.eval(`(() => {
      const btn = document.querySelector('.mt-right .user-btn') || document.querySelector('.user-btn');
      if (btn) btn.click(); return true;
    })()`)
    await sleep(600)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.includes('锁定屏幕'));
      if (b) b.click(); return true;
    })()`)
    await sleep(900)
    const info = await cdp.eval(`JSON.stringify((() => {
      const h2 = document.querySelector('.lock-card h2');
      const card = document.querySelector('.lock-card');
      return {
        hasCard: Boolean(card),
        h2Text: h2 ? h2.textContent.trim() : null,
        h2Display: h2 ? getComputedStyle(h2).display : null,
        h2Color: h2 ? getComputedStyle(h2).color : null,
        brandInCard: Boolean(card && card.querySelector('.lock-brand')),
        cardText: card ? card.innerText.slice(0, 120) : null,
      };
    })())`)
    console.log('LOCK', info)
    await cdp.screenshot(path.join(SHOTS, '29-lock.png'))
  }

  if (process.argv.includes('--mortgage')) {
    await cdp.goto(WEB + '/ledger/liabilities')
    await sleep(1200)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.includes('新增负债'));
      if (b) b.click(); return true;
    })()`)
    await sleep(900)
    await cdp.eval(`(() => {
      const sel = Array.from(document.querySelectorAll('.drawer-panel select')).find((s) =>
        Array.from(s.options).some((o) => o.textContent.trim() === '房贷'));
      if (!sel) return 'NO_SELECT';
      const opt = Array.from(sel.options).find((o) => o.textContent.trim() === '房贷');
      sel.value = opt.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      return 'OK';
    })()`)
    await sleep(700)
    await cdp.eval(`(() => {
      const panel = document.querySelector('.drawer-panel .drawer-body');
      const labels = Array.from(document.querySelectorAll('.drawer-panel .field label.lbl')).map((l) => l.textContent.trim());
      console.log(JSON.stringify(labels));
      return true;
    })()`)
    // 滚到中段看剩余期数
    await cdp.eval(`(() => {
      const body = document.querySelector('.drawer-panel .drawer-body');
      if (!body) return false;
      const f = Array.from(document.querySelectorAll('.drawer-panel .field')).find((x) => (x.textContent || '').includes('剩余期数'));
      if (f) f.scrollIntoView({ block: 'center' });
      return true;
    })()`)
    await sleep(500)
    await cdp.screenshot(path.join(SHOTS, '30-mortgage-form.png'))
    console.log('mortgage shot done')
  }

  if (process.argv.includes('--valuation')) {
    await cdp.goto(WEB + '/ledger/assets/real-estates')
    await sleep(1500)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '详情');
      if (b) b.click(); return true;
    })()`)
    await sleep(1500)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.includes('新增估值'));
      if (b) b.click(); return true;
    })()`)
    await sleep(800)
    // 面积（档案没有时临时填）+ 市场单价，看自动算总值
    await cdp.eval(`(() => {
      const fields = Array.from(document.querySelectorAll('.modal-panel .field, [role="dialog"] .field'));
      const areaField = fields.find((x) => (x.textContent || '').includes('建筑面积'));
      const areaInput = areaField && areaField.querySelector('input:not([disabled])');
      if (areaInput) {
        areaInput.value = '100';
        areaInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
      const priceField = fields.find((x) => (x.textContent || '').includes('市场单价'));
      const priceInput = priceField && priceField.querySelector('input');
      if (!priceInput) return 'NO_PRICE';
      priceInput.value = '42000';
      priceInput.dispatchEvent(new Event('input', { bubbles: true }));
      return 'OK';
    })()`)
    await sleep(600)
    const info = await cdp.eval(`document.querySelector('.calc-hint')?.innerText || 'no hint'`)
    console.log('VALUATION', info)
    await cdp.screenshot(path.join(SHOTS, '31-valuation.png'))
  }

  if (process.argv.includes('--interest')) {
    await cdp.goto(WEB + '/ledger/liabilities')
    await sleep(1500)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '详情');
      if (b) b.click(); return true;
    })()`)
    await sleep(1500)
    await cdp.eval(`(() => {
      const t = Array.from(document.querySelectorAll('button.tab, .drawer-panel button'))
        .find((x) => x.textContent.trim() === '利息费用');
      if (t) t.click(); return true;
    })()`)
    await sleep(800)
    const info = await cdp.eval(`document.querySelector('.drawer-panel')?.innerText.slice(0, 400) || 'no panel'`)
    console.log('INTEREST', JSON.stringify(info))
    await cdp.screenshot(path.join(SHOTS, '32-interest-tab.png'))
  }

  if (process.argv.includes('--repay')) {
    // 登记一笔还款，然后看流水里是不是只出现一条「还款」记录
    await cdp.goto(WEB + '/ledger/liabilities')
    await sleep(1500)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '详情');
      if (b) b.click(); return true;
    })()`)
    await sleep(1500)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.includes('登记还款'));
      if (b) b.click(); return true;
    })()`)
    await sleep(1000)
    const filled = await cdp.eval(`JSON.stringify((() => {
      const modal = document.querySelector('.modal-card');
      if (!modal) return { err: 'no modal' };
      const inputs = Array.from(modal.querySelectorAll('input'));
      const amount = inputs[0];
      if (amount && !amount.value) { amount.value = '1000'; amount.dispatchEvent(new Event('input', { bubbles: true })); }
      const sel = modal.querySelector('select');
      if (sel && !sel.value) {
        const opt = Array.from(sel.options).find((o) => o.value);
        if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
      }
      return { amount: amount ? amount.value : null, selects: modal.querySelectorAll('select').length };
    })())`)
    console.log('REPAY_FORM', filled)
    await sleep(400)
    await cdp.eval(`(() => {
      const modal = document.querySelector('.modal-card');
      const b = Array.from(modal.querySelectorAll('button')).find((x) => x.textContent.includes('确认还款'));
      if (b) b.click(); return true;
    })()`)
    await sleep(2000)
    await cdp.goto(WEB + '/ledger/transactions')
    await sleep(1800)
    const rows = await cdp.eval(`JSON.stringify(Array.from(document.querySelectorAll('.mini-card'))
      .map((c) => c.innerText.split('\\n').slice(0, 3).join(' | ')).slice(0, 6))`)
    console.log('TX_ROWS', rows)
    await cdp.screenshot(path.join(SHOTS, '33-transactions-merged.png'))

    // 打开合并行详情，确认能看到本金/利息拆分与两条记账明细
    await cdp.eval(`(() => {
      const card = document.querySelector('.mini-card');
      const b = card && Array.from(card.querySelectorAll('button')).find((x) => x.textContent.trim() === '详情');
      if (b) b.click(); return true;
    })()`)
    await sleep(1200)
    const detailText = await cdp.eval(`document.querySelector('.drawer-panel')?.innerText.slice(0, 500) || 'no drawer'`)
    console.log('TX_DETAIL', JSON.stringify(detailText))
    await cdp.screenshot(path.join(SHOTS, '33b-tx-detail.png'))
  }

  if (process.argv.includes('--asset-mortgage')) {
    // 不动产详情 → 房贷页签：确认「关联负债」显示真实贷款名
    await cdp.goto(WEB + '/ledger/assets/real-estates')
    await sleep(1500)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '详情');
      if (b) b.click(); return true;
    })()`)
    await sleep(1500)
    await cdp.eval(`(() => {
      const t = Array.from(document.querySelectorAll('.drawer-panel button, button.tab'))
        .find((x) => x.textContent.trim() === '房贷');
      if (t) t.click(); return true;
    })()`)
    await sleep(800)
    const info = await cdp.eval(`document.querySelector('.drawer-panel')?.innerText.slice(0, 500) || 'no panel'`)
    console.log('ASSET_MORTGAGE', JSON.stringify(info))
    await cdp.screenshot(path.join(SHOTS, '34-asset-mortgage.png'))
  }

  if (process.argv.includes('--recurring')) {
    await cdp.goto(WEB + '/ledger/transactions')
    await sleep(1500)
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.includes('固定支出'));
      if (b) b.click(); return true;
    })()`)
    await sleep(1200)
    const info = await cdp.eval(`document.querySelector('.drawer-panel')?.innerText.slice(0, 300) || 'no drawer'`)
    console.log('RECURRING', JSON.stringify(info))
    await cdp.screenshot(path.join(SHOTS, '35-recurring.png'))
  }

  ws.close()
  chrome.kill()
  frontend.kill()
  backend.kill()
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
