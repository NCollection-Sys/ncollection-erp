// Trial Balance pilot: OCA-backed source inside the NCollection viewer.
// Needs: a data-rich database (here ncperf) with ncollection_account_reports_oca installed and Arabic installed,
// plus the platform database WITHOUT the adapter (section B must stay native).
// The tenant owner / accounting / restricted-user checks were run by hand with temporary users (not stored here).
const fs = require('fs');
const { launch, login, psql, switchLang } = require('./lib');
const R = []; const ck = (l, ok, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + l + (d ? ' -- ' + d : '')); R.push(ok); };
const ACT = '/odoo/action-ncollection_account_reports.action_nc_view_trial_balance';
const sig = async (dl) => { const p = await dl.path(); return fs.readFileSync(p).slice(0, 4).toString('latin1'); };
async function rpc(page, base, model, method, args, kwargs) {
  return page.evaluate(async ([base, model, method, args, kwargs]) => { const r = await fetch(base + '/web/dataset/call_kw/' + model + '/' + method, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: { model, method, args, kwargs: kwargs || {} } }) }); return r.json(); }, [base, model, method, args, kwargs]);
}
const num = t => Number(String(t).replace(/[^0-9.\-]/g, '') || 0);
(async () => {
  // ---------------- A. full journey on the data-rich database, as its administrator
  const base = 'http://ncperf.localhost:8069';
  const A = await launch({ width: 1366, height: 900 }); const page = A.page; await login(page, 'admin', 'admin', base);
  await rpc(page, base, 'ir.config_parameter', 'set_param', ['ncollection_account_reports.source.trial_balance', 'oca']);
  await switchLang(page, 'English');
  await page.goto(base + ACT); await page.waitForSelector('.o_nc_rep__table');
  const meta = (await page.innerText('.o_nc_rep__meta')).replace(/\s+/g, ' ');
  ck('A1 OCA-backed Trial Balance renders inside the NCollection viewer (badge shown)', /Engine\s*:?\s*OCA/.test(meta), meta.slice(0, 90));
  ck('A2 no raw OCA screen: viewer toolbar + filter bar present', !!(await page.$('.o_nc_rep__actions')) && !!(await page.$('.o_nc_rep__filters')));
  const filters = await page.$$eval('.o_nc_rep__f label, .o_nc_rep__f--check label', l => l.map(x => x.innerText.trim()));
  ck('A3 OCA options appear as ordinary filters', filters.some(f => /Hide accounts at zero/i.test(f)) && filters.some(f => /account groups/i.test(f)), filters.join('|'));
  const sqlTotal = psql("select round(sum(debit),2) from account_move_line where parent_state='posted' and date between '2026-01-01' and current_date", 'ncperf');
  const totalRow = await page.$$eval('.o_nc_rep__row.level-0', r => r.map(x => x.innerText.replace(/\s+/g, ' ')).pop());
  ck('A4 the Total row debit equals independent SQL', Math.abs(num((totalRow.match(/[\d,]+\.\d\d/g) || [])[1]) - Number(sqlTotal)) < 0.01, totalRow + ' vs ' + sqlTotal);
  await page.click('.o_nc_rep__row .o_nc_rep__link >> nth=0'); await page.waitForSelector('.o_form_view');
  ck('A5 drill-down opens the exact account', /account\.account\/\d+/.test(page.url()), page.url().replace(base, ''));
  await page.goBack(); await page.waitForSelector('.o_nc_rep__table');
  await page.fill('#f_df', '2026-03-01'); await page.fill('#f_dt', '2026-06-30');
  await page.locator('label:has-text("Show account groups") input').check().catch(() => {});
  await page.click('.o_nc_rep__filters button[type=submit]'); await page.waitForTimeout(1500);
  const urlA = page.url();
  ck('A6 filters are in the URL', /nc_df=2026-03-01/.test(urlA) && /nc_x=/.test(urlA), urlA.replace(base, '').slice(0, 120));
  let r = await switchLang(page, 'Arabic'); await page.waitForSelector('.o_nc_rep__table');
  const rtl = await page.evaluate(() => getComputedStyle(document.body).direction);
  ck('A7 EN->AR reloads by itself, RTL, same report + date filters kept', r.reloaded && rtl === 'rtl' && (await page.inputValue('#f_df')) === '2026-03-01' && (await page.inputValue('#f_dt')) === '2026-06-30', JSON.stringify(r));
  const arTitle = await page.textContent('.o_nc_rep__title');
  ck('A8 the Arabic header is translated', /ميزان/.test(arTitle), arTitle.trim());
  r = await switchLang(page, 'English'); await page.waitForSelector('.o_nc_rep__table');
  const ltr = await page.evaluate(() => getComputedStyle(document.body).direction);
  ck('A9 AR->EN reloads by itself, LTR, filters kept', r.reloaded && ltr === 'ltr' && (await page.inputValue('#f_df')) === '2026-03-01');
  await page.reload(); await page.waitForSelector('.o_nc_rep__table');
  ck('A10 a hard reload restores the filters', (await page.inputValue('#f_df')) === '2026-03-01' && (await page.inputValue('#f_dt')) === '2026-06-30');
  await page.click('.o_nc_rep__menu button');
  const [xl] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.click('.o_nc_rep__dropdown button:has-text("Excel")')]);
  ck('A11 Excel export downloads a real workbook', (await sig(xl)).startsWith('PK'), xl.suggestedFilename());
  // The toolbar's Print goes through Odoo's client download helper, which fails identically for a NATIVE Odoo
  // report in this headless harness (verified), so the PDF is fetched from the same server route Odoo uses.
  const pdfInfo = await page.evaluate(async (base) => {
    const run = await (await fetch(base + '/web/dataset/call_kw/ncollection.report.service/nc_report_run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: { model: 'ncollection.report.service', method: 'nc_report_run', args: ['ncollection.account.report.trial.balance', { date_from: '2026-03-01', date_to: '2026-06-30' }], kwargs: { context: { lang: 'ar_001' } } } }) })).json();
    const w = run.result.wizard; const name = w.model.endsWith('.tb.oca') ? 'ncollection_account_reports_oca.report_trial_balance_oca' : 'ncollection_account_reports.report_trial_balance';
    const g = await fetch(base + '/report/pdf/' + name + '/' + w.id); const b = new Uint8Array(await g.arrayBuffer());
    return { model: w.model, status: g.status, type: g.headers.get('content-type'), head: String.fromCharCode(...b.slice(0, 5)), len: b.length };
  }, base);
  ck('A12 the OCA-backed report prints a real PDF (Arabic, filtered period)', pdfInfo.model.endsWith('.tb.oca') && pdfInfo.head === '%PDF-' && pdfInfo.len > 5000, JSON.stringify(pdfInfo));
  ck('A13 no JS/RPC errors in the journey', A.problems.length === 0, A.problems.join(' ; ').slice(0, 200));
  await rpc(page, base, 'ir.config_parameter', 'set_param', ['ncollection_account_reports.source.trial_balance', 'native']);
  await A.browser.close();

  // ---------------- B. platform database WITHOUT the adapter: must stay native
  const P = await launch({ width: 1366, height: 900 }); await login(P.page, 'admin', 'admin', 'http://ncollection.localhost:8069');
  await P.page.goto('http://ncollection.localhost:8069' + ACT); await P.page.waitForSelector('.o_nc_rep__table');
  ck('B1 platform admin: native Trial Balance, no OCA badge (adapter not installed there)', !/OCA/.test(await P.page.innerText('.o_nc_rep__meta')));
  await P.browser.close();

  console.log(R.every(Boolean) ? 'ALL PASS' : 'SOME FAIL');
})().catch(e => { console.error('FAIL', e.message.slice(0, 500)); process.exit(1); });
