const { BASE, launch, shot, login } = require('./lib');
const R = []; const ck = (l, ok, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + l + (d ? ' -- ' + d : '')); R.push(ok); };
(async () => {
  const { browser, page, problems } = await launch({ width: 1366, height: 768 }); await login(page, 'admin', 'admin');
  await page.goto(BASE + '/odoo/action-ncollection_account_reports.action_nc_report_hub'); await page.waitForSelector('.o_nc_hub__grid');
  const links = await page.$$eval('.o_nc_hub__link', l => l.map(x => x.dataset.xmlid + '|' + x.innerText.trim()));
  console.log('reports in hub:', links.length);
  for (const item of links) {
    const [xmlid, name] = item.split('|');
    await page.goto(BASE + '/odoo/action-ncollection_account_reports.action_nc_report_hub'); await page.waitForSelector('.o_nc_hub__grid');
    const before = problems.length;
    await page.click(`.o_nc_hub__link[data-xmlid="${xmlid}"]`);
    await page.waitForTimeout(2200);
    const dlg = await page.$('.o_error_dialog, .modal .o_dialog_error, .modal-dialog:has-text("Error")');
    const body = await page.$('.o_nc_rep, .o_pivot, .o_graph_view, .o_list_view, .o_form_view, .o_kanban_view, .o_view_controller, .o_action');
    const newP = problems.slice(before);
    ck(name + '  (' + xmlid.split('.')[1] + ')', !dlg && !!body && newP.length === 0, newP.join(' ; ').slice(0, 160));
  }
  console.log(R.every(Boolean) ? 'ALL PASS' : 'SOME FAIL'); await browser.close();
})().catch(e => { console.error('FAIL', e.message.slice(0, 400)); process.exit(1); });
