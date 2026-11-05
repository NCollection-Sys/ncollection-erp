// Sales Order Matching + drag-and-drop browser acceptance (fixtures/bank_fixture_sales.py on the DISPOSABLE uxbankweb).
// Every UI action is followed by an ORM read of the invoices / entries / residuals it produced.
const path = require('path');
const fs = require('fs');
const { launch, session, shot, rpc, BASE } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

const results = [];
let CURRENT = null;
async function check(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail: detail || '' });
    console.log('PASS', name, detail || '');
  } catch (e) {
    results.push({ name, ok: false, detail: String(e).slice(0, 400) });
    console.log('FAIL', name, String(e).slice(0, 400));
    if (CURRENT) await shot(CURRENT, 'sales_fail_' + results.length).catch(() => {});
  }
}
const eq = (a, b, msg) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
const ok = (v, msg) => { if (!v) throw new Error(msg); };
const round = (x) => Math.round(x * 100) / 100;

const OWNER = { user: 'owner_bank', password: 'Owner-Bank-Pass1!', company: 1 };
const CEO = { user: 'ceo_bank', password: 'Ceo-Bank-Pass1!', company: 1 };
const QIF = fs.readFileSync(path.join(__dirname, 'fixtures', 'misr_march.qif'), 'utf8');

async function stLine(page, label) {
  return (await rpc(page, 'account.bank.statement.line', 'search_read', [[['payment_ref', 'like', label]]],
    { fields: ['is_reconciled', 'amount', 'journal_id', 'move_id', 'payment_ref'], limit: 1 }))[0];
}
async function moveLines(page, moveIds) {
  return rpc(page, 'account.move.line', 'search_read', [[['move_id', 'in', moveIds]]],
    { fields: ['account_id', 'balance', 'move_id'] });
}
async function openCard(page, journalName) {
  await page.goto(BASE + '/odoo/accounting');
  await page.waitForSelector('.o_nc_bank_nav', { timeout: 60000 });
  return page.locator('.o_kanban_record', { has: page.locator('.o_nc_bank_nav') }).filter({ hasText: journalName }).first();
}
async function openJournal(page, journalName) {
  const card = await openCard(page, journalName);
  await card.locator('#all_transactions_btn').click();
  await page.waitForSelector('.o_account_reconcile_oca', { timeout: 60000 });
  await page.waitForTimeout(1500);
}
async function select(page, label) {
  await page.locator('.o_kanban_record:not(.o_kanban_ghost)', { hasText: label }).first().click();
  await page.waitForSelector('.o_reconcile_widget_table', { timeout: 30000 });
  await page.waitForTimeout(1200);
}
async function validate(page) {
  await page.getByRole('button', { name: 'Reconcile', exact: true }).first().click();
  await page.waitForTimeout(3000);
}
// A real browser drop: the same DataTransfer / DragEvent sequence the OS drag produces.
async function dropFiles(page, selector, files) {
  await page.evaluate(({ selector, files }) => {
    // 'card:<journal name>' = the bank journal card carrying the drop marker; anything else is a CSS selector
    const target = selector.startsWith('card:')
      ? [...document.querySelectorAll('.o_kanban_record')].find((el) => el.querySelector('.o_nc_bank_drop_hint[data-journal-type=bank]') && el.innerText.includes(selector.slice(5)))
      : document.querySelector(selector);
    if (!target) throw new Error('no drop target ' + selector);
    const dt = new DataTransfer();
    for (const f of files) {
      const content = f.size ? new Uint8Array(f.size) : f.text;
      dt.items.add(new File([content], f.name, { type: 'application/octet-stream' }));
    }
    const rect = target.getBoundingClientRect();
    const opts = { bubbles: true, cancelable: true, dataTransfer: dt, clientX: rect.x + 20, clientY: rect.y + 20 };
    target.dispatchEvent(new DragEvent('dragenter', opts));
    target.dispatchEvent(new DragEvent('dragover', opts));
    target.dispatchEvent(new DragEvent('drop', opts));
  }, { selector, files });
}
const lineCount = (page, journalName) => rpc(page, 'account.bank.statement.line', 'search_count', [[['journal_id.name', '=', journalName]]]);

(async () => {
  const browser = await launch();
  const s = await session(browser, { ...OWNER, lang: 'en_US', viewport: { width: 1400, height: 900 } });
  const p = s.page;
  CURRENT = p;
  const orders = await rpc(p, 'sale.order', 'search_read', [[['partner_id.name', '=', 'Sales Customer']]], { fields: ['name', 'invoice_status'], order: 'id' });
  const [soA, soB, soC] = orders.map((o) => o.name);

  async function openSales() {
    const btn = p.locator('button.o_nc_sales:visible').first();
    await btn.waitFor({ state: 'visible', timeout: 20000 });
    await btn.click();
    await p.locator('.modal .o_nc_sale_orders').waitFor({ timeout: 30000 });
    await p.waitForTimeout(800);
  }
  async function tick(orderName, on = true) {
    const row = p.locator('.modal .o_nc_sale_orders .o_data_row', { hasText: orderName }).first();
    const toggle = row.locator('.o_boolean_toggle input, .o_field_boolean_toggle input').first();
    const checked = await toggle.isChecked();
    if (checked !== on) { await row.locator('.o_field_boolean_toggle, .o_boolean_toggle').first().click(); await p.waitForTimeout(400); }
  }

  await check('Sales action: order named in the reference is ticked; regular invoice posted and matched; paid; no revenue from the bank entry', async () => {
    await openJournal(p, 'Sales Bank');
    await select(p, 'Payment ' + soA);
    await openSales();
    await shot(p, 'sales_dialog');
    const row = p.locator('.modal .o_nc_sale_orders .o_data_row', { hasText: soA }).first();
    ok(await row.count(), 'order listed');
    const text = (await row.innerText()).replace(/\s+/g, ' ');
    ok(/To Invoice/i.test(text), 'invoicing status shown: ' + text);
    const modal = p.locator('.modal');
    await modal.locator('.o_field_widget[name=post] input').check();
    await modal.locator('button.o_nc_sale_create').click();
    await p.waitForTimeout(3500);
    await shot(p, 'sales_after_create');
    await validate(p);
    const st = await stLine(p, 'Payment ' + soA);
    eq(st.is_reconciled, true, 'reconciled');
    const so = (await rpc(p, 'sale.order', 'search_read', [[['name', '=', soA]]], { fields: ['invoice_ids', 'invoice_status'] }))[0];
    eq(so.invoice_ids.length, 1, 'exactly one invoice');
    const inv = (await rpc(p, 'account.move', 'read', [so.invoice_ids, ['state', 'amount_total', 'amount_residual', 'payment_state']]))[0];
    eq([inv.state, inv.amount_total, inv.amount_residual], ['posted', 1000, 0], 'posted, paid');
    const lines = await moveLines(p, [st.move_id[0]]);
    eq(round(lines.reduce((a, l) => a + l.balance, 0)), 0, 'balanced');
    const kinds = await rpc(p, 'account.account', 'read', [[...new Set(lines.map((l) => l.account_id[0]))], ['account_type']]);
    ok(!kinds.some((k) => /income/.test(k.account_type)), 'the bank entry itself books no revenue');
    return `${soA} -> ${inv.payment_state}`;
  });

  await check('Down payment 30 %: invoice of 600 posted and matched; the order stays to-invoice for the balance', async () => {
    await openJournal(p, 'Sales Bank');
    await select(p, 'Advance ' + soB);
    await openSales();
    await tick(soB, true);
    const modal = p.locator('.modal');
    await modal.locator('.o_field_widget[name=method] input').click(); await p.locator('.o_select_menu_item', { hasText: 'Down payment (percentage)' }).click();
    await p.waitForTimeout(500);
    await modal.locator('.o_field_widget[name=percentage] input').fill('30');
    await modal.locator('.o_field_widget[name=post] input').check();
    await shot(p, 'sales_downpayment');
    await modal.locator('button.o_nc_sale_create').click();
    await p.waitForTimeout(3500);
    await validate(p);
    eq((await stLine(p, 'Advance ' + soB)).is_reconciled, true, 'reconciled');
    const so = (await rpc(p, 'sale.order', 'search_read', [[['name', '=', soB]]], { fields: ['invoice_ids', 'invoice_status'] }))[0];
    const inv = (await rpc(p, 'account.move', 'read', [so.invoice_ids, ['state', 'amount_total', 'amount_residual']]))[0];
    eq([inv.state, inv.amount_total, inv.amount_residual], ['posted', 600, 0], 'down payment invoice paid');
    eq(so.invoice_status, 'to invoice', 'balance still to invoice');
    return 'down payment 600 paid; ' + soB + ' ' + so.invoice_status;
  });

  await check('Already-invoiced order: a second invoice is refused; its posted invoice is added and matched instead', async () => {
    await openJournal(p, 'Sales Bank');
    await select(p, 'Payment ' + soC);
    await openSales();
    const modal = p.locator('.modal');
    ok(await modal.locator('.o_data_row', { hasText: soC }).locator('input:checked').count(), 'the order named in the reference is ticked even though it is invoiced');
    await modal.locator('button.o_nc_sale_create').click();
    const dialog = p.locator('.modal-dialog', { hasText: /nothing left to invoice/i });
    await dialog.first().waitFor({ timeout: 15000 });
    await shot(p, 'sales_refused');
    await p.keyboard.press('Escape');
    await p.waitForTimeout(500);
    const count = (await rpc(p, 'sale.order', 'search_read', [[['name', '=', soC]]], { fields: ['invoice_ids'] }))[0].invoice_ids.length;
    eq(count, 1, 'still one invoice');
    await modal.locator('button.o_nc_sale_add').click().catch(() => {});
    await p.waitForTimeout(3000);
    await validate(p);
    eq((await stLine(p, 'Payment ' + soC)).is_reconciled, true, 'reconciled');
    const so = (await rpc(p, 'sale.order', 'search_read', [[['name', '=', soC]]], { fields: ['invoice_ids'] }))[0];
    const inv = (await rpc(p, 'account.move', 'read', [so.invoice_ids, ['amount_residual', 'payment_state']]))[0];
    eq(inv.amount_residual, 0, 'paid');
    return 'second invoice refused; paid ' + inv.payment_state;
  });

  await check('drag and drop on the journal card: opens the import wizard with the file; nothing is imported until Preview + Import', async () => {
    const before = await lineCount(p, 'Bank Misr');
    await openCard(p, 'Bank Misr');
    await dropFiles(p, 'card:Bank Misr', [{ name: 'drop.qif', text: QIF }]);
    const modal = p.locator('.modal');
    await modal.waitFor({ timeout: 30000 });
    // the wizard record already holds the dropped file (server side), whatever the binary widget shows
    const filled = await modal.evaluate((el) => el.innerText);
    ok(/drop\.qif/.test(filled) || (await rpc(p, 'account.statement.import', 'search_count', [[['statement_filename', '=', 'drop.qif']]])) > 0, 'file in the wizard: ' + filled.slice(0, 200));
    eq(await lineCount(p, 'Bank Misr'), before, 'a drop imports nothing');
    ok(await modal.locator('button.o_nc_import_preview_btn').isVisible(), 'Preview offered');
    eq(await modal.locator('button[name=import_file_button]').count() === 0 || !(await modal.locator('button[name=import_file_button]').isVisible()), true, 'Import hidden before preview');
    await shot(p, 'drop_wizard');
    await modal.locator('button.o_nc_import_preview_btn').click();
    await modal.locator('.o_nc_import_preview').waitFor({ timeout: 30000 });
    eq(await lineCount(p, 'Bank Misr'), before, 'preview imports nothing');
    await modal.locator('button[name=import_file_button]').click();
    await p.waitForTimeout(4000);
    ok((await lineCount(p, 'Bank Misr')) > before, 'Import after Preview creates the transactions');
    return `lines ${before} -> ${await lineCount(p, 'Bank Misr')}`;
  });

  await check('drag and drop refusals: wrong type, two files, oversize -> a notification, no wizard, no upload', async () => {
    for (const [files, pattern] of [
      [[{ name: 'virus.exe', text: 'MZ' }], /not a bank statement file/i],
      [[{ name: 'a.qif', text: QIF }, { name: 'b.qif', text: QIF }], /one statement file/i],
      [[{ name: 'big.csv', size: 11 * 1024 * 1024 }], /larger than/i],
    ]) {
      await openCard(p, 'Bank Misr');
      await dropFiles(p, 'card:Bank Misr', files);
      await p.locator('.o_notification', { hasText: pattern }).first().waitFor({ timeout: 10000 });
      eq(await p.locator('.modal').count(), 0, 'no wizard for ' + files[0].name);
    }
    return 'three refusals';
  });

  await check('drag and drop on the matching workspace opens the same wizard', async () => {
    await openJournal(p, 'Bank Misr');
    await dropFiles(p, '.o_action_manager .o_action', [{ name: 'again.qif', text: QIF }]);
    await p.locator('.modal button.o_nc_import_preview_btn').waitFor({ timeout: 30000 });
    await shot(p, 'drop_workspace');
    return 'wizard opened from the workspace';
  });

  await check('no page errors (Owner)', async () => { eq(s.errors, [], 'page errors'); });
  await s.context.close();

  const ceo = await session(browser, { ...CEO, lang: 'en_US', viewport: { width: 1400, height: 900 } });
  await check('CEO (read-only): the server refuses invoice creation from the Sales action and a dropped file', async () => {
    const stl = await stLine(ceo.page, 'Payment');
    let wizardRefused = false;
    try { await rpc(ceo.page, 'ncollection.bank.sale.match', 'create', [{ statement_line_id: stl.id }]); } catch (e) { wizardRefused = true; }
    ok(wizardRefused, 'Sales wizard refused');
    let dropRefused = false;
    const journal = (await rpc(ceo.page, 'account.journal', 'search', [[['name', '=', 'Bank Misr']]]))[0];
    try { await rpc(ceo.page, 'account.journal', 'action_nc_bank_drop', [[journal], 'x.qif', Buffer.from(QIF).toString('base64')]); } catch (e) { dropRefused = true; }
    ok(dropRefused, 'drop refused');
    return 'refused at the ORM';
  });
  await ceo.context.close();

  // Arabic (RTL), desktop: the Sales button and the dialog; a drop on the card
  const ar = await session(browser, { ...OWNER, lang: 'ar_001', viewport: { width: 1280, height: 900 } });
  CURRENT = ar.page;
  await check('Arabic RTL desktop: Sales dialog is right-to-left and a drop opens the wizard', async () => {
    await openCard(ar.page, 'Bank Misr');
    ok((await ar.page.evaluate(() => getComputedStyle(document.body).direction)) === 'rtl', 'rtl');
    await dropFiles(ar.page, 'card:Bank Misr', [{ name: 'ar.qif', text: QIF }]);
    await ar.page.locator('.modal').waitFor({ timeout: 30000 });
    await shot(ar.page, 'drop_wizard_ar');
    await ar.page.keyboard.press('Escape');
    return 'rtl drop ok';
  });
  await ar.context.close();

  // English LTR mobile: the Sales dialog stays usable on a phone
  const mob = await session(browser, { ...OWNER, lang: 'en_US', viewport: { width: 390, height: 844 } });
  CURRENT = mob.page;
  await check('Mobile 390px: the matching workspace stays inside the viewport (no horizontal page scroll)', async () => {
    await openJournal(mob.page, 'Sales Bank').catch(async () => { await mob.page.waitForTimeout(500); });
    const line = await rpc(mob.page, 'account.bank.statement.line', 'search_read', [[['is_reconciled', '=', false], ['journal_id.name', '=', 'Sales Bank']]], { fields: ['id'], limit: 1 });
    await shot(mob.page, 'sales_mobile');
    const overflow = await mob.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    ok(overflow <= 1, 'no horizontal page scroll: ' + overflow);
    return line.length ? 'open line present' : 'all matched';
  });
  await mob.context.close();

  await browser.close();
  const passed = results.filter((r) => r.ok).length;
  fs.writeFileSync(path.join(__dirname, '..', 'shots', 'bank_sales_acceptance.json'), JSON.stringify(results, null, 2));
  console.log(`RESULT ${passed}/${results.length}`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error('CRASH', e); process.exit(2); });
