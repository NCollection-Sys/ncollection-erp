const { BASE, launch, shot, login, uiState, langChoices, switchLang } = require('./lib');
const R = []; const ck = (l, ok, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + l + (d ? ' -- ' + d : '')); R.push(ok); };
const ACT = k => BASE + '/odoo/action-ncollection_account_reports.action_nc_view_' + k;
async function open(page, k) { await page.goto(ACT(k)); await page.waitForSelector('.o_nc_rep__table, .o_nc_rep__state', { timeout: 30000 }); await page.waitForTimeout(400); }
async function formTitle(page) { await page.waitForSelector('.o_form_view', { timeout: 15000 }); await page.waitForTimeout(300); return page.url().replace(BASE, ''); }
(async () => {
  const { browser, page, problems } = await launch({ width: 1366, height: 768 }); await login(page, 'admin', 'admin');
  // the link texts asserted below ("Item", "Payment") are English: do not depend on the language the last run left behind
  await switchLang(page, 'English');
  await open(page, 'general_ledger');
  // account
  await page.click('.o_nc_rep__section .o_nc_rep__link >> nth=0'); let u = await formTitle(page); ck('GL account link opens account form', /account\.account\/\d+/.test(u), u);
  await page.goBack(); await page.waitForSelector('.o_nc_rep__table');
  // entry
  await page.click('.o_nc_rep__line .o_nc_rep__link >> nth=0'); u = await formTitle(page); ck('GL entry link opens journal entry', /account\.move\/\d+/.test(u), u);
  await page.goBack(); await page.waitForSelector('.o_nc_rep__table');
  // journal item chip
  await page.click('.o_nc_rep__chiplink:has-text("Item") >> nth=0'); u = await formTitle(page); ck('GL item chip opens journal item', /account\.move\.line\/\d+/.test(u), u);
  await page.goBack(); await page.waitForSelector('.o_nc_rep__table');
  // payment chip
  await page.click('.o_nc_rep__chiplink:has-text("Payment") >> nth=0'); u = await formTitle(page); ck('GL payment chip opens payment', /account\.payment\/\d+/.test(u), u);
  await page.goBack(); await page.waitForSelector('.o_nc_rep__table');
  // partner
  const plink = await page.$('.o_nc_rep__line td:nth-child(5) .o_nc_rep__link'); await page.click('.o_nc_rep__line .o_nc_rep__link:not([title]) >> nth=2').catch(() => {});
  console.log('after partner-ish click', page.url().replace(BASE, ''));
  for (const k of ['trial_balance', 'balance_sheet', 'profit_loss', 'cash_flow', 'partner_ledger', 'aged_partner', 'financial_summary', 'reference', 'equity_changes', 'uae_vat', 'profitability']) {
    await open(page, k);
    const rows = (await page.$$('.o_nc_rep__table tbody tr')).length; const t = await page.textContent('.o_nc_rep__title').catch(() => '?');
    ck(k + ' renders (' + t.trim() + ')', rows > 0 || !!(await page.$('.o_nc_rep__state--empty')), 'rows ' + rows);
  }
  await open(page, 'trial_balance'); await page.click('.o_nc_rep__row .o_nc_rep__link >> nth=0'); u = await formTitle(page); ck('TB account link opens account', /account\.account\/\d+/.test(u), u);
  await open(page, 'partner_ledger'); await shot(page, 'pl1');
  await page.click('.o_nc_rep__section .o_nc_rep__link >> nth=0'); await page.waitForSelector('.o_form_view'); u = page.url().replace(BASE, ''); ck('Partner ledger partner link opens partner', /res\.partner\/\d+/.test(u), u);
  console.log('problems', problems); console.log(R.every(Boolean) ? 'ALL PASS' : 'SOME FAIL'); await browser.close();
})().catch(e => { console.error('FAIL', e.message.slice(0, 500)); process.exit(1); });
