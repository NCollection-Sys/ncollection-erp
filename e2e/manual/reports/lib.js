// Shared helpers for the delivery E2E click-through.
const { chromium } = require('../../node_modules/playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DIR = __dirname;
const BASE = 'http://ncollection.localhost:8069';
const STATE_FILE = path.join(DIR, 'state.json');

function readState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch (e) { return {}; }
}
function writeState(patch) {
  const s = Object.assign(readState(), patch);
  fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
  return s;
}

function psql(sql, db) {
  const cmd = 'docker compose exec -T db psql -U odoo -d ' + (db || 'postgres') + ' -tAc "' + sql.replace(/"/g, '\\"') + '"';
  return execSync(cmd, { cwd: require('path').resolve(__dirname, '../../..'), stdio: ['ignore', 'pipe', 'pipe'] }).toString().trim();
}

async function launch(viewport) {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const context = await browser.newContext({ viewport: viewport || { width: 1440, height: 900 } });
  const page = await context.newPage();
  const problems = [];
  page.on('pageerror', e => problems.push('pageerror: ' + e.message.slice(0, 300)));
  page.on('console', m => {
    if (m.type() === 'error') {
      const t = m.text();
      // The browser's own noise about a missing favicon is not an application error.
      if (/favicon/i.test(t)) return;
      problems.push('console.error: ' + t.slice(0, 300));
    }
  });
  page.on('response', r => {
    const u = r.url();
    if (r.status() >= 500) problems.push('http ' + r.status() + ' ' + u.slice(0, 160));
    else if (r.status() >= 400 && !/favicon/.test(u)) { (problems.failedUrls = problems.failedUrls || []).push(r.status() + ' ' + u.slice(0, 200)); }
  });
  return { browser, context, page, problems };
}

async function shot(page, name) {
  const f = path.join(DIR, name + '.png');
  await page.screenshot({ path: f });
  return f;
}

function check(label, ok, detail) {
  const line = (ok ? 'PASS  ' : 'FAIL  ') + label + (detail ? '  -- ' + detail : '');
  console.log(line);
  const s = readState();
  s.checks = s.checks || [];
  s.checks.push({ label, ok: !!ok, detail: detail || '' });
  fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
  return !!ok;
}

async function loginBackend(page, host, login, password) {
  await page.goto('http://' + host + ':8069/web/login');
  await page.fill('input[name="login"]', login);
  await page.fill('input[name="password"]', password);
  await Promise.all([page.waitForLoadState('load'), page.click('button[type="submit"]')]);
}

module.exports = { BASE, DIR, launch, shot, check, readState, writeState, psql, loginBackend };

// ---- UX pass helpers -------------------------------------------------------
async function login(page, user, pass, base) {
  await page.goto((base || BASE) + '/web/login');
  await page.fill('input[name="login"]', user); await page.fill('input[name="password"]', pass);
  await Promise.all([page.waitForLoadState('load'), page.click('button[type="submit"]')]);
  await page.waitForSelector('.o_main_navbar', { timeout: 40000 });
}
async function uiState(page) {
  return page.evaluate(() => ({
    dir: getComputedStyle(document.body).direction,
    lang: document.documentElement.lang,
    userLang: (window.odoo && odoo.__session_info__ && odoo.__session_info__.user_context && odoo.__session_info__.user_context.lang) || null,
  }));
}
// Open the preferences dialog and return the language choices the select offers.
async function openPrefs(page) {
  await page.click('.o_user_menu .dropdown-toggle, .o_user_menu button');
  await page.click('.dropdown-item:has-text("My Preferences"), .dropdown-item:has-text("تفضيلاتي"), .dropdown-item:has-text("Mes préférences")');
  await page.waitForSelector('.modal [name="lang"] .o_select_menu_toggler');
  await page.waitForTimeout(500);
}
async function langChoices(page) {
  await page.waitForTimeout(1200);
  await openPrefs(page);
  await page.click('.modal [name="lang"] .o_select_menu_toggler');
  const items = await page.$$eval('.o_select_menu_menu .o_select_menu_item', e => e.map(x => x.innerText.trim()));
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  return items;
}
// Switch language through the real UI and report whether the page reloaded BY ITSELF.
async function switchLang(page, label) {
  await openPrefs(page);
  await page.evaluate(() => { window.__nc_no_reload_marker = 1; });
  await page.click('.modal [name="lang"] .o_select_menu_toggler');
  await page.click('.o_select_menu_menu .o_select_menu_item:has-text("' + label + '")');
  await page.waitForTimeout(300);
  const t0 = Date.now();
  await page.click('.modal-footer .btn-primary');
  let reloaded = false;
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(500);
    const m = await page.evaluate(() => window.__nc_no_reload_marker).catch(() => 'navigating');
    if (m !== 1) { reloaded = true; break; }
  }
  await page.waitForSelector('.o_main_navbar', { timeout: 40000 });
  await page.waitForTimeout(800);
  return { reloaded, ms: Date.now() - t0 };
}
module.exports.login = login; module.exports.uiState = uiState; module.exports.langChoices = langChoices; module.exports.switchLang = switchLang;
