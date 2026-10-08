// Partner/Statement report search - the flow that used to throw
// "Cannot read properties of undefined (reading 'state')" (bare method call in
// an OWL handler). Covers: type, pick, clear, change partner, change date,
// switch report, EN<->AR, reload with filters, browser back/forward.
const { BASE, launch, login, switchLang } = require('./lib');
const R = []; const ck = (l, ok, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + l + (d ? ' -- ' + d : '')); R.push(ok); };
const ACT = k => BASE + '/odoo/action-ncollection_account_reports.action_nc_view_' + k;
const PARTNER_INPUT = '.o_nc_rep__f--picker >> nth=2 >> input';
(async () => {
  const { browser, page, problems } = await launch({ width: 1366, height: 900 });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await login(page, 'admin', 'admin');
  await switchLang(page, 'English');
  for (const report of ['partner_ledger', 'partner_statement']) {
    await page.goto(ACT(report)); await page.waitForSelector('.o_nc_rep__table, .o_nc_rep__state');
    const box = page.locator(PARTNER_INPUT);
    await box.click(); await box.pressSequentially('Fa', { delay: 60 }); await page.waitForSelector('.o_nc_rep__options button', { timeout: 8000 });
    ck(report + ': typing in the partner search lists matches', (await page.$$('.o_nc_rep__options button')).length > 0);
    await box.fill(''); await page.waitForTimeout(300);
    ck(report + ': clearing the search clears the suggestions', (await page.$$('.o_nc_rep__options button')).length === 0);
    await box.pressSequentially('Fadi', { delay: 40 }); await page.waitForSelector('.o_nc_rep__options button');
    await page.click('.o_nc_rep__options button >> nth=0'); await page.waitForTimeout(400);
    ck(report + ': picking a partner adds a tag', (await page.locator('.o_nc_rep__f--picker').nth(2).locator('.o_nc_rep__tag').count()) === 1);
    await page.click('.o_nc_rep__filters button[type=submit]'); await page.waitForTimeout(1500);
    const sections = await page.$$eval('.o_nc_rep__section .o_nc_rep__section-name', e => e.map(x => x.innerText.trim()));
    ck(report + ': applying filters to one partner shows only that partner', sections.length === 1 && /Fadi/.test(sections[0]), sections.join('|'));
    await page.locator('.o_nc_rep__f--picker').nth(2).locator('.o_nc_rep__tag button').click(); await page.fill('#f_df', '2026-03-01'); await page.click('.o_nc_rep__filters button[type=submit]'); await page.waitForTimeout(1200);
    ck(report + ': changing the date re-runs the report', (await page.inputValue('#f_df')) === '2026-03-01');
    // language switch keeps report, filters and URL state
    await switchLang(page, 'Arabic'); await page.waitForSelector('.o_nc_rep__title');
    ck(report + ': EN->AR keeps the filter', (await page.inputValue('#f_df')) === '2026-03-01' && /nc_df=2026-03-01/.test(page.url()));
    await page.reload(); await page.waitForSelector('.o_nc_rep__title');
    ck(report + ': a reload with filters restores them', (await page.inputValue('#f_df')) === '2026-03-01');
    await switchLang(page, 'English'); await page.waitForSelector('.o_nc_rep__title');
  }
  // real navigation (hub), then browser back / forward. Each Apply is a history
  // entry, so Back steps through the previous filter set and then reaches the hub.
  await page.goto(BASE + '/odoo/action-ncollection_account_reports.action_nc_report_hub'); await page.waitForSelector('.o_nc_hub__grid');
  await page.click('.o_nc_hub__link[data-xmlid="ncollection_account_reports.menu_report_partner_ledger"]'); await page.waitForSelector('.o_nc_rep__title');
  await page.waitForTimeout(800); const first = await page.inputValue('#f_df');
  await page.fill('#f_df', '2026-02-01'); await page.click('.o_nc_rep__filters button[type=submit]'); await page.waitForTimeout(1200);
  await page.goBack(); await page.waitForFunction(v => document.querySelector('#f_df') && document.querySelector('#f_df').value === v, first, { timeout: 15000 }).catch(() => {});
  ck('back restores the previous filter set', (await page.inputValue('#f_df')) === first, first);
  await page.goForward(); await page.waitForFunction(() => document.querySelector('#f_df') && document.querySelector('#f_df').value === '2026-02-01', null, { timeout: 15000 }).catch(() => {});
  ck('forward restores the later filter set', (await page.inputValue('#f_df')) === '2026-02-01');
  await page.goBack(); await page.goBack(); await page.waitForSelector('.o_nc_hub__grid', { timeout: 15000 }).catch(() => {});
  ck('back again reaches the hub', !!(await page.$('.o_nc_hub__grid')));
  ck('no uncaught client errors', errors.length === 0 && problems.length === 0, errors.concat(problems).join(' ; ').slice(0, 200));
  console.log(R.every(Boolean) ? 'ALL PASS' : 'SOME FAIL'); await browser.close();
})().catch(e => { console.error('FAIL', e.message.slice(0, 400)); process.exit(1); });
