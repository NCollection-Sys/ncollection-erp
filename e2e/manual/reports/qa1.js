const { BASE, launch, shot, login, switchLang } = require('./lib');
const R = []; const ck = (l, ok, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + l + (d ? ' -- ' + d : '')); R.push(ok); };
const sizes = [[1366, 768], [1920, 1080], [1024, 768], [768, 1024], [480, 900]];
(async () => {
  const { browser, page, problems, context } = await launch({ width: 1366, height: 768 }); await login(page, 'admin', 'admin');
  for (const rtl of [false, true]) {
    if (rtl) { await page.setViewportSize({ width: 1366, height: 768 }); await switchLang(page, 'Arabic'); }
    for (const [w, h] of sizes) {
      await page.setViewportSize({ width: w, height: h });
      for (const [name, url, sel] of [['hub', '/odoo/action-ncollection_account_reports.action_nc_report_hub', '.o_nc_hub__grid'], ['gl', '/odoo/action-ncollection_account_reports.action_nc_view_general_ledger', '.o_nc_rep__table']]) {
        await page.goto(BASE + url); await page.waitForSelector(sel, { timeout: 30000 }); await page.waitForTimeout(500);
        const m = await page.evaluate((sel) => {
          const root = document.querySelector('.o_nc_hub, .o_nc_rep'); const sc = document.querySelector('.o_action_manager, .o_content') || document.body;
          const r = root.getBoundingClientRect(); const over = document.documentElement.scrollWidth > window.innerWidth + 2;
          // clipped controls: any filter/action button outside the viewport horizontally
          const out = [...document.querySelectorAll('.o_nc_rep__filters button, .o_nc_rep__actions button, .o_nc_hub__search')].filter(b => { const x = b.getBoundingClientRect(); return x.width && (x.right > window.innerWidth + 1 || x.left < -1); }).length;
          return { over, out, dir: getComputedStyle(document.body).direction, w: Math.round(r.width) };
        }, sel);
        ck(`${rtl ? 'AR' : 'EN'} ${name} ${w}x${h}: no page overflow, nothing clipped`, !m.over && m.out === 0, JSON.stringify(m));
        if ((w === 1366 || w === 480) ) await shot(page, `qa_${rtl ? 'ar' : 'en'}_${name}_${w}`);
      }
    }
  }
  console.log(problems); console.log(R.every(Boolean) ? 'ALL PASS' : 'SOME FAIL'); await browser.close();
})().catch(e => { console.error('FAIL', e.message.slice(0, 400)); process.exit(1); });
