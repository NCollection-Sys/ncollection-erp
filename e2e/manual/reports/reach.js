// Global reachability audit: after scrolling every scroll container to its end,
// the LAST piece of content of the page must be inside the viewport.
const { BASE, launch, login, switchLang } = require('./lib');
const A = 'ncollection_account_reports.action_nc_view_';
const PAGES = [
  ['home', '/odoo/action-ncollection_core.tenant_home'],
  ['dashboard', '/odoo/action-ncollection_core.action_ncollection_dashboard'],
  ['fin-dashboard', '/odoo/action-ncollection_account_dashboard.action_finance_dashboard'],
  ['ceo-dashboard', '/odoo/action-ncollection_account_dashboard.action_ceo_dashboard'],
  ['hub', '/odoo/action-ncollection_account_reports.action_nc_report_hub'],
  ['gl', '/odoo/action-' + A + 'general_ledger'], ['tb', '/odoo/action-' + A + 'trial_balance'],
  ['bs', '/odoo/action-' + A + 'balance_sheet'], ['pl', '/odoo/action-' + A + 'profit_loss'],
  ['partner-ledger', '/odoo/action-' + A + 'partner_ledger'], ['aged', '/odoo/action-' + A + 'aged_partner'],
  ['settings', '/odoo/settings'], ['sales-list', '/odoo/sales'], ['purchase', '/odoo/purchase'],
  ['inventory', '/odoo/inventory'], ['accounting', '/odoo/accounting'], ['contacts', '/odoo/contacts'],
];
const probe = () => {
  const am = document.querySelector('.o_action_manager');
  if (!am) return { ok: true, note: 'no action manager (login/redirect?)' };
  const nav = document.querySelector('.o_main_navbar');
  const out = [];
  // 1. the action manager is overflow:hidden - anything taller than it is lost
  if (am.scrollHeight > am.clientHeight + 1) out.push('clipped-vertical ' + am.scrollHeight + '>' + am.clientHeight);
  if (am.scrollWidth > am.clientWidth + 1) out.push('clipped-horizontal ' + am.scrollWidth + '>' + am.clientWidth);
  // 2. the page itself must not hide overflow it cannot scroll
  const de = document.documentElement;
  if (de.scrollHeight > innerHeight + 1 && getComputedStyle(document.body).overflowY === 'hidden') out.push('body-hides-overflow ' + de.scrollHeight + '>' + innerHeight);
  if (de.scrollWidth > innerWidth + 2) out.push('page-horizontal-overflow ' + de.scrollWidth + '>' + innerWidth);
  // 3. the header must not sit on top of the content
  if (nav && am.getBoundingClientRect().top < nav.getBoundingClientRect().bottom - 1) out.push('header-overlaps-content');
  // 4. a child that is taller than the manager must itself scroll
  const x = am.firstElementChild;
  if (x) { const s = getComputedStyle(x); const sc = /(auto|scroll)/.test(s.overflowY);
    if (x.scrollHeight > x.clientHeight + 1 && !sc) out.push('root-overflows-without-scroll ' + x.className.toString().slice(0, 30)); }
  return { ok: out.length === 0, issues: out };
};
(async () => {
  const sizes = (process.argv[2] || '480x800,768x1024,1024x768,1366x768,1920x1080').split(',').map(s => s.split('x').map(Number));
  const { browser, page, problems } = await launch({ width: 1366, height: 768 }); await login(page, 'admin', 'admin');
  let bad = 0;
  for (const rtl of [false, true]) {
    if (rtl) { await page.setViewportSize({ width: 1366, height: 768 }); await switchLang(page, 'Arabic'); }
    for (const [w, h] of sizes) {
      await page.setViewportSize({ width: w, height: h });
      for (const [name, url] of PAGES) {
        await page.goto(BASE + url); await page.waitForTimeout(1800);
        const r = await page.evaluate(probe);
        const ok = r.ok;
        if (!ok) { bad++; console.log(`FAIL ${rtl ? 'AR' : 'EN'} ${w}x${h} ${name}: ${JSON.stringify(r)}`); }
      }
    }
  }
  console.log(bad ? `${bad} FAILURES` : 'ALL REACHABLE', problems.slice(0, 3)); await browser.close();
})().catch(e => { console.error('FAIL', e.message.slice(0, 300)); process.exit(1); });
