const { BASE, launch, shot, login, uiState, langChoices, switchLang } = require('./lib');
const R = []; const ck = (l, ok, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + l + (d ? ' -- ' + d : '')); R.push(ok); };
(async () => {
  const { browser, page, problems } = await launch({ width: 1366, height: 768 }); await login(page, 'admin', 'admin');
  for (const k of (process.argv[2] || 'general_ledger,trial_balance,balance_sheet,profit_loss,partner_ledger').split(',')) {
    await page.goto(BASE + '/odoo/action-ncollection_account_reports.action_nc_view_' + k); await page.waitForSelector('.o_nc_rep__table, .o_nc_rep__state');
    // change a filter and apply so there is state to preserve
    await page.fill('#f_df', '2026-03-01'); await page.click('.o_nc_rep__filters button[type=submit]'); await page.waitForTimeout(1200);
    const title0 = (await page.textContent('.o_nc_rep__title')).trim(); const per0 = (await page.textContent('.o_nc_rep__meta')).replace(/\s+/g, ' ');
    const ar = 'Arabic', en = 'English';
    let r = await switchLang(page, ar); await page.waitForSelector('.o_nc_rep__table, .o_nc_rep__state', { timeout: 30000 }); await page.waitForTimeout(500);
    let s = await uiState(page); const dfv = await page.inputValue('#f_df'); const title1 = (await page.textContent('.o_nc_rep__title')).trim();
    ck(k + ' EN->AR: reloaded, RTL, same report + filter kept', r.reloaded && s.dir === 'rtl' && /03-01|01\/03|1\/3|٠٣|2026/.test(dfv) && dfv.includes('2026-03-01'), `${title0} -> ${title1} | from=${dfv} | ${s.dir}`);
    if (k === 'general_ledger') await shot(page, 'gl_ar');
    r = await switchLang(page, en); await page.waitForSelector('.o_nc_rep__table, .o_nc_rep__state', { timeout: 30000 }); await page.waitForTimeout(500);
    s = await uiState(page); const df2 = await page.inputValue('#f_df');
    ck(k + ' AR->EN: reloaded, LTR, filter kept', r.reloaded && s.dir === 'ltr' && df2 === '2026-03-01', `from=${df2} ${s.dir} per=${(await page.textContent('.o_nc_rep__meta')).replace(/\s+/g, ' ') === per0}`);
  }
  console.log('problems', problems); console.log(R.every(Boolean) ? 'ALL PASS' : 'SOME FAIL'); await browser.close();
})().catch(e => { console.error('FAIL', e.message.slice(0, 500)); process.exit(1); });
