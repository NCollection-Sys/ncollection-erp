// Screenshots + layout metrics of every NCollection dashboard. usage: node survey.js <lang> <WxH> [tag]
const { launch, session, shot, open } = require('./lib');

const PAGES = [
  ['workspace', '/atlas/action-ncollection_core.action_ncollection_dashboard', '.nc-dashboard', '.nc-dashboard'],
  ['finance', '/atlas/action-ncollection_account_dashboard.action_finance_dashboard', '.nc-fin-dashboard', '.nc-fin-dashboard'],
  ['accountant', '/atlas/action-ncollection_account_dashboard.action_accountant_dashboard', '.nc-fin-dashboard', '.nc-fin-dashboard'],
  ['cash', '/atlas/action-ncollection_account_dashboard.action_cash_dashboard', '.nc-fin-dashboard', '.nc-fin-dashboard'],
  ['ceo', '/atlas/action-ncollection_account_dashboard.action_ceo_dashboard', '.nc-fin-dashboard', '.nc-fin-dashboard'],
  ['sales_dept', '/atlas/action-ncollection_account_dashboard.action_sales_dashboard', '.nc-fin-dashboard', '.nc-fin-dashboard'],
  ['hr', '/atlas/action-ncollection_account_dashboard.action_hr_dashboard', '.nc-fin-dashboard', '.nc-fin-dashboard'],
  ['warehouse', '/atlas/action-ncollection_account_dashboard.action_warehouse_dashboard', '.nc-fin-dashboard', '.nc-fin-dashboard'],
  ['sales_premium', '/atlas/action-ncollection_account_dashboard.action_sales_premium', '.o_nc_kpi', '.o_nc_prem'],
  ['invoicing_premium', '/atlas/action-ncollection_account_dashboard.action_invoicing_premium', '.o_nc_kpi', '.o_nc_prem'],
  ['platform', '/atlas/action-ncollection_subscription.action_ncollection_subscription_dashboard', '.o_form_view', '.o_form_view'],
];

(async () => {
  const [lang = 'en_US', size = '1280x800', tag = ''] = process.argv.slice(2);
  const [width, height] = size.split('x').map(Number);
  const only = process.env.ONLY ? process.env.ONLY.split(',') : null;
  const browser = await launch();
  const { page, errors } = await session(browser, { lang, viewport: { width, height } });
  const out = [];
  for (const [name, url, ready, root] of PAGES) {
    if (only && !only.includes(name)) continue;
    try {
      const m = await open(page, url, ready, root);
      const f = await shot(page, `${tag}${name}_${lang === 'ar_001' ? 'ar' : 'en'}_${width}`);
      out.push({ name, ok: true, ...m });
    } catch (e) {
      await shot(page, `${tag}${name}_${lang === 'ar_001' ? 'ar' : 'en'}_${width}_FAIL`).catch(() => {});
      out.push({ name, ok: false, error: String(e).split('\n')[0].slice(0, 160) });
    }
  }
  console.log(JSON.stringify({ lang, size, errors, results: out }, null, 1));
  await browser.close();
})();
