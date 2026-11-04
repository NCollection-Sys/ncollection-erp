// Shared Playwright helpers (headless Chromium: no hidden-pane throttling). All waits are bounded.
const path = require('path');
const fs = require('fs');
const { chromium } = require('D:/Projects/ncollection-erp/e2e/node_modules/playwright');

const BASE = 'http://localhost:8169';
const SHOTS = path.join(__dirname, '..', 'shots');
fs.mkdirSync(SHOTS, { recursive: true });

async function rpc(page, model, method, args = [], kwargs = {}) {
  return page.evaluate(async ({ model, method, args, kwargs }) => {
    const r = await fetch('/web/dataset/call_kw', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: { model, method, args, kwargs } }) });
    const j = await r.json();
    if (j.error) throw new Error(j.error.data.message);
    return j.result;
  }, { model, method, args, kwargs });
}

async function login(page, login, password) {
  await page.goto(BASE + '/web/login');
  await page.fill('input[name=login]', login);
  await page.fill('input[name=password]', password);
  await Promise.all([page.waitForURL(/\/(odoo|atlas|web)(\/|$|\?)/, { timeout: 45000 }), page.click('button[type=submit]')]);
}

/** A signed-in browser context in the given language / company / viewport. */
async function session(browser, { user = 'dash_all', password = 'Dash-All-Pass1!', lang = 'en_US', company = 2,
  viewport = { width: 1280, height: 800 } } = {}) {
  const context = await browser.newContext({ viewport, acceptDownloads: true, locale: lang === 'ar_001' ? 'ar' : 'en-US' });
  await context.addCookies([{ name: 'cids', value: String(company), url: BASE }]);
  const page = await context.newPage();
  page.setDefaultTimeout(40000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 240)));
  await login(page, user, password);
  const uid = await page.evaluate(async () => {
    const r = await fetch('/web/session/get_session_info', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"jsonrpc":"2.0","method":"call","params":{}}' });
    return (await r.json()).result.uid;
  });
  try {
    const lids = await rpc(page, 'res.lang', 'search', [[['code', '=', lang], ['active', 'in', [true, false]]]]);
    await rpc(page, 'res.lang', 'write', [lids, { active: true }]);
  } catch (e) { /* only an administrator may activate a language; it is already active */ }
  await rpc(page, 'res.users', 'write', [[uid], { lang }]);
  await page.goto(BASE + '/odoo');
  await page.waitForSelector('.o_main_navbar', { timeout: 60000 });
  return { context, page, errors, uid };
}

async function shot(page, name, opts = {}) {
  const file = path.join(SHOTS, name + '.png');
  await page.screenshot({ path: file, fullPage: false, ...opts });
  return file;
}

/** Opens an action by xmlid-or-id URL and waits for a selector; returns layout metrics of the scroll root. */
async function open(page, url, ready, rootSel) {
  await page.goto(BASE + url);
  await page.waitForSelector(ready, { timeout: 60000 });
  await page.waitForTimeout(1200);          // charts finish drawing (animation is off; this is only a settle)
  return page.evaluate((sel) => {
    const el = document.querySelector(sel) || document.querySelector('.o_action_manager');
    const r = el.getBoundingClientRect();
    return {
      clientW: el.clientWidth, scrollW: el.scrollWidth, clientH: el.clientHeight, scrollH: el.scrollHeight,
      overflowX: el.scrollWidth > el.clientWidth + 1, rect: [Math.round(r.left), Math.round(r.right)],
      dir: getComputedStyle(document.documentElement).direction, vw: innerWidth,
    };
  }, rootSel || '.o_action_manager');
}

const launch = () => chromium.launch({ channel: process.env.PW_CHANNEL || 'msedge', headless: true });
module.exports = { launch, chromium, BASE, SHOTS, rpc, session, shot, open, login };
