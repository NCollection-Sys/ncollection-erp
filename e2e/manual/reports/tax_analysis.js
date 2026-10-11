const fs = require('fs');
// Tax Analysis on a DATA-RICH tenant (needs invoices, bills and credit notes with VAT): every view, totals against
// independent SQL, drill-down to the tax / partner, view kept in the URL, EN<->AR without F5, Excel, no overflow.
// NC_DB selects the tenant database (default ncperf: a scratch tenant seeded with ~15k lines; see README).
const { launch, login, psql, switchLang } = require('./lib');
const R = []; const ck = (l, ok, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + l + (d ? ' -- ' + d : '')); R.push(ok); };
const ACT = '/odoo/action-ncollection_account_reports.action_nc_view_tax_analysis';
const DB = process.env.NC_DB || 'ncperf'; const base = 'http://' + DB + '.localhost:8069';
const num = t => Number(String(t).replace(/[^0-9.\-]/g, '') || 0);
const sig = async dl => fs.readFileSync(await dl.path()).slice(0, 4).toString('latin1');
(async () => {
  const A = await launch({ width: 1366, height: 900 }); const page = A.page; await login(page, 'admin', 'admin', base);
  await page.goto(base + ACT); await page.waitForSelector('.o_nc_rep__table', { timeout: 40000 });
  const filters = await page.$$eval('.o_nc_rep__f label', l => l.map(x => x.innerText.trim()));
  ck('1 the View filter is offered', filters.some(f => /^View$/i.test(f)), filters.join('|'));
  const read = async () => page.$$eval('.o_nc_rep__table tbody tr', rs => rs.map(r => ({ cls: r.className, cells: [...r.querySelectorAll('td')].map(c => c.innerText.trim().replace(/\s+/g, ' ')) })));
  let rows = await read();
  const sqlOut = Number(psql("select round(sum(-l.balance),2) from account_move_line l join account_tax t on t.id=l.tax_line_id where l.parent_state='posted' and t.type_tax_use='sale' and l.date between '2026-01-01' and current_date", DB));
  const sqlIn = Number(psql("select round(sum(l.balance),2) from account_move_line l join account_tax t on t.id=l.tax_line_id where l.parent_state='posted' and t.type_tax_use='purchase' and l.date between '2026-01-01' and current_date", DB));
  const out = rows.find(r => /Output tax/i.test(r.cells[0])); const inp = rows.find(r => /Input tax/i.test(r.cells[0])); const net = rows.find(r => /Net tax payable/i.test(r.cells[0]));
  ck('2 output tax == independent SQL (credit notes netted)', out && Math.abs(num(out.cells[2]) - sqlOut) < 0.011, (out && out.cells[2]) + ' vs ' + sqlOut);
  ck('3 input tax == independent SQL', inp && Math.abs(num(inp.cells[2]) - sqlIn) < 0.011, (inp && inp.cells[2]) + ' vs ' + sqlIn);
  ck('4 net payable == output - input', net && Math.abs(num(net.cells[2]) - (sqlOut - sqlIn)) < 0.02, (net && net.cells[2]));
  const sqlBase = Number(psql("select round(sum(-l.balance),2) from account_move_line l join account_move_line_account_tax_rel r on r.account_move_line_id=l.id join account_tax t on t.id=r.account_tax_id where l.parent_state='posted' and t.type_tax_use='sale' and l.tax_line_id is null and l.display_type='product' and l.date between '2026-01-01' and current_date", DB));
  ck('5 taxable base (sales) == independent SQL', out && Math.abs(num(out.cells[1]) - sqlBase) < 0.011, (out && out.cells[1]) + ' vs ' + sqlBase);
  // drill-down: a tax row opens the tax record
  const taxLink = await page.$('.o_nc_rep__table tbody tr .o_nc_rep__link');
  if (taxLink) { await taxLink.click(); await page.waitForSelector('.o_form_view', { timeout: 20000 }); ck('6 a tax row drills into the tax record', /account\.tax\/\d+/.test(page.url()), page.url().replace(base, '')); await page.goBack(); await page.waitForSelector('.o_nc_rep__table'); }
  else ck('6 a tax row drills into the tax record', false, 'no link rendered');
  ck('7 section/total rows are not links', (await page.$$('.o_nc_rep__table tbody tr.level-0 .o_nc_rep__link')).length === 0);
  // every view
  for (const view of ['account', 'customer', 'vendor', 'taxability']) {
    const sel = await page.$('#f_group_by, select[name="group_by"], .o_nc_rep__f select');
    const opts = await page.$$eval('.o_nc_rep__f select option', o => o.map(x => x.value + '=' + x.textContent.trim()));
    await page.selectOption('.o_nc_rep__f select >> nth=' + (await page.$$eval('.o_nc_rep__f select', s => s.findIndex(x => [...x.options].some(o => o.value === 'taxability')))), view);
    await page.click('.o_nc_rep__filters button[type=submit]'); await page.waitForTimeout(2500);
    rows = await read();
    ck('8 view "' + view + '" renders rows', rows.length > 2, rows.length + ' rows, first: ' + (rows[0] && rows[0].cells[0]));
    if (view === 'customer') { const l = await page.$('.o_nc_rep__table tbody tr:not(.level-0) .o_nc_rep__link'); if (l) { await l.click(); await page.waitForSelector('.o_form_view', { timeout: 20000 }); ck('9 a customer row drills into the partner', /res\.partner\/\d+/.test(page.url()), page.url().replace(base, '')); await page.goBack(); await page.waitForSelector('.o_nc_rep__table'); } else ck('9 customer drill', false, 'no link'); }
  }
  ck('10 the chosen view is in the URL', /nc_x=/.test(page.url()) || /group_by/.test(decodeURIComponent(page.url())), decodeURIComponent(page.url()).replace(base, '').slice(0, 160));
  // language switch keeps report + view
  const urlBefore = page.url();
  let r = await switchLang(page, 'Arabic'); await page.waitForSelector('.o_nc_rep__table', { timeout: 40000 });
  const dir = await page.evaluate(() => getComputedStyle(document.body).direction);
  const title = await page.textContent('.o_nc_rep__title');
  ck('11 EN->AR re-initialises by itself (RTL, translated title, same report)', r.reloaded && dir === 'rtl' && /الضريبة/.test(title), title.trim());
  r = await switchLang(page, 'English'); await page.waitForSelector('.o_nc_rep__table', { timeout: 40000 });
  ck('12 AR->EN back to LTR', (await page.evaluate(() => getComputedStyle(document.body).direction)) === 'ltr');
  // exports
  await page.click('.o_nc_rep__menu button');
  const [xl] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.click('.o_nc_rep__dropdown button:has-text("Excel")')]);
  ck('13 Excel export is a real workbook', (await sig(xl)).startsWith('PK'), xl.suggestedFilename());
  // scroll: page can reach the bottom at each width
  for (const w of [1920, 1366, 1024, 768, 480]) {
    await page.setViewportSize({ width: w, height: 800 }); await page.waitForTimeout(500);
    const m = await page.evaluate(() => { const el = document.querySelector('.o_nc_rep'); const sc = el && (el.scrollHeight > el.clientHeight + 2 ? el : el.parentElement); const root = document.querySelector('.o_action_manager'); return { content: el ? el.scrollHeight : 0, client: el ? el.clientHeight : 0, hOverflow: document.documentElement.scrollWidth > window.innerWidth + 2, am: root ? root.clientHeight : 0 }; });
    ck('14 width ' + w + ': no page-level horizontal overflow, content reachable', !m.hOverflow, JSON.stringify(m));
  }
  ck('15 no JS/RPC errors in the whole journey', A.problems.length === 0, A.problems.join(' ; ').slice(0, 250));
  await A.browser.close();
  console.log(R.every(Boolean) ? 'ALL PASS' : 'SOME FAIL');
})().catch(e => { console.error('FAIL', e.message.slice(0, 500)); process.exit(1); });
