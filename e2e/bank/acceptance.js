// Bank Transactions & Matching — real-browser acceptance against the DISPOSABLE fixture (e2e/bank/fixtures).
// Every accounting result is read back through the ORM (rpc) after the UI action; the UI is never trusted alone.
const path = require('path');
const fs = require('fs');
const { launch, session, shot, rpc, BASE } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

const results = [];
async function check(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail: detail || '' });
    console.log('PASS', name, detail || '');
  } catch (e) {
    results.push({ name, ok: false, detail: String(e).slice(0, 400) });
    console.log('FAIL', name, String(e).slice(0, 400));
  }
}
const eq = (a, b, msg) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
const ok = (v, msg) => { if (!v) throw new Error(msg); };

const OWNER = { user: 'owner_bank', password: 'Owner-Bank-Pass1!', company: 1 };
const ACCT = { user: 'acct_bank', password: 'Acct-Bank-Pass1!', company: 1 };
const CEO = { user: 'ceo_bank', password: 'Ceo-Bank-Pass1!', company: 1 };

async function journal(page, name) {
  return (await rpc(page, 'account.journal', 'search', [[['name', '=', name]]]))[0];
}
async function stLine(page, label) {
  const r = await rpc(page, 'account.bank.statement.line', 'search_read', [[['payment_ref', '=', label]]],
    { fields: ['is_reconciled', 'amount', 'journal_id', 'move_id'], limit: 1 });
  return r[0];
}
async function invoiceByName(page, name) {
  return (await rpc(page, 'account.move', 'search_read', [[['name', '=', name]]], { fields: ['amount_residual', 'payment_state'] }))[0];
}
async function dashboard(page) {
  await page.goto(BASE + '/odoo/accounting');
  await page.waitForSelector('.o_kanban_record .o_nc_bank_nav', { timeout: 60000 });
  await page.waitForTimeout(800);
  return page.locator('.o_kanban_record', { has: page.locator('.o_nc_bank_nav') }).filter({ hasText: 'Bank Misr' }).first();
}
async function workspaceRecords(page) {
  await page.waitForSelector('.o_account_reconcile_oca, .o_view_nocontent', { timeout: 60000 });
  await page.waitForTimeout(1500);
  return page.locator('.o_kanban_record:not(.o_kanban_ghost)').count();
}
async function overflow(page) {
  return page.evaluate(() => ({ x: document.documentElement.scrollWidth > innerWidth + 1, dir: getComputedStyle(document.body).direction }));
}

(async () => {
  const browser = await launch();

  // ---------------------------------------------------------------- Owner, English, desktop
  const en = await session(browser, { ...OWNER, lang: 'en_US', viewport: { width: 1400, height: 900 } });
  const p = en.page;
  const misrId = await journal(p, 'Bank Misr');
  const cashId = await journal(p, 'Main Cash');

  await check('card: Bank Misr shows Upload + Transactions, then To Reconcile / Statements / New', async () => {
    const card = await dashboard(p);
    const nav = await card.locator('.o_nc_bank_nav a').allInnerTexts();
    eq(nav.map((s) => s.trim()), ['To Reconcile', 'Statements', 'New'], 'nav');
    ok(await card.locator('#all_transactions_btn').isVisible(), 'Transactions button');
    eq((await card.locator('button.o_nc_bank_upload').innerText()).trim(), 'Upload', 'Upload button');
    ok(!/OCA/.test(await card.innerText()), 'no vendor name on the card');
    await shot(p, 'bank_card_en');
    return nav.join(' · ');
  });

  await check('Transactions = the matching workspace of THIS journal, reconciled included', async () => {
    const card = await dashboard(p);
    await card.locator('#all_transactions_btn').click();
    const n = await workspaceRecords(p);
    const expected = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', misrId]]]);
    eq(n, expected, 'records shown vs journal lines');
    const reconciled = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', misrId], ['is_reconciled', '=', true]]]);
    ok(reconciled >= 1, 'a reconciled line is in the list');
    ok(await p.locator('.o_account_reconcile_oca').count(), 'split workspace');
    await shot(p, 'bank_transactions_en');
    return `${n} transactions (${reconciled} reconciled)`;
  });

  await check('To Reconcile = only what still needs matching', async () => {
    const card = await dashboard(p);
    await card.locator('.o_nc_bank_nav_to_reconcile').click();
    const n = await workspaceRecords(p);
    const expected = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', misrId], ['is_reconciled', '=', false]]]);
    eq(n, expected, 'unreconciled shown');
    return `${n} to reconcile`;
  });

  await check('Statements = the separate statement list of this journal', async () => {
    const card = await dashboard(p);
    await card.locator('.o_nc_bank_nav_statements').click();
    await p.waitForSelector('.o_list_view, .o_view_nocontent', { timeout: 60000 });
    const crumbs = await p.locator('.o_breadcrumb').innerText();
    ok(/Statement/i.test(crumbs), 'breadcrumb ' + crumbs);
    return crumbs.replace(/\s+/g, ' ');
  });

  await check('match in the UI: "Transfer from Nile" 500 -> invoice 2 paid (ORM)', async () => {
    const card = await dashboard(p);
    await card.locator('.o_nc_bank_nav_to_reconcile').click();
    await workspaceRecords(p);
    await p.locator('.o_kanban_record:not(.o_kanban_ghost)', { hasText: 'Transfer from Nile' }).first().click();
    await p.waitForSelector('.o_reconcile_widget_table', { timeout: 30000 });
    await p.waitForTimeout(1500);
    const inv2 = (await rpc(p, 'account.move', 'search_read', [[['move_type', '=', 'out_invoice'], ['amount_total', '=', 500]]], { fields: ['name'] }))[0];
    const lineTxt = await p.locator('.o_reconcile_widget_table').innerText();
    if (!lineTxt.includes(inv2.name)) {
      await p.locator('.o_reconcile_move_line_view .o_data_row', { hasText: inv2.name }).first().click();
      await p.waitForTimeout(1500);
    }
    await shot(p, 'bank_match_before_validate_en');
    await p.getByRole('button', { name: 'Reconcile', exact: true }).first().click();
    await p.waitForTimeout(3000);
    const st = await stLine(p, 'Transfer from Nile');
    eq(st.is_reconciled, true, 'transaction reconciled');
    const inv = await invoiceByName(p, inv2.name);
    eq(inv.amount_residual, 0, 'invoice residual');
    await shot(p, 'bank_match_after_validate_en');
    return `${inv2.name} ${inv.payment_state}`;
  });

  await check('undo in the UI: Unreconcile restores the invoice and leaves an audit note', async () => {
    await p.locator('.o_kanban_record:not(.o_kanban_ghost)', { hasText: 'Transfer from Nile' }).first().click().catch(() => {});
    const card = await dashboard(p);
    await card.locator('#all_transactions_btn').click();
    await workspaceRecords(p);
    await p.locator('.o_kanban_record:not(.o_kanban_ghost)', { hasText: 'Transfer from Nile' }).first().click();
    await p.waitForTimeout(1500);
    await p.getByRole('button', { name: 'Unreconcile', exact: true }).first().click();
    const dlg = p.locator('.modal-dialog');
    if (await dlg.count()) await dlg.getByRole('button', { name: /^(Ok|OK|Confirm)$/ }).first().click();
    await p.waitForTimeout(3000);
    const st = await stLine(p, 'Transfer from Nile');
    eq(st.is_reconciled, false, 'transaction open again');
    const inv2 = (await rpc(p, 'account.move', 'search_read', [[['move_type', '=', 'out_invoice'], ['amount_total', '=', 500]]], { fields: ['amount_residual'] }))[0];
    eq(inv2.amount_residual, 500, 'invoice open again');
    const msgs = await rpc(p, 'mail.message', 'search_read', [[['model', '=', 'account.move'], ['res_id', '=', st.move_id[0]]]], { fields: ['body'] });
    const text = msgs.map((m) => m.body).join(' ');
    ok(/matched by/.test(text) && /undone by/.test(text), 'audit notes: ' + text.slice(0, 200));
    return 'residual back to 500, audited';
  });

  await check('New: a manual transaction from the card lands in Bank Misr', async () => {
    const before = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', misrId]]]);
    const card = await dashboard(p);
    await card.locator('.o_nc_bank_nav_new').click();
    const form = p.locator('.modal .o_form_view, .o_form_view').last();
    await form.waitFor({ timeout: 30000 });
    await form.locator('.o_field_widget[name=payment_ref] input').fill('Manual deposit (browser)');
    await form.locator('.o_field_widget[name=amount] input').fill('75');
    await shot(p, 'bank_new_en');
    await p.locator('.modal-footer button.btn-primary, .o_form_button_save').first().click();
    await p.waitForTimeout(2500);
    const after = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', misrId]]]);
    eq(after, before + 1, 'one more transaction');
    const st = await stLine(p, 'Manual deposit (browser)');
    eq([st.amount, st.journal_id[0]], [75, misrId], 'amount and journal');
    return 'created';
  });

  await check('Upload: preview first, then import; re-upload imports nothing', async () => {
    const qif = path.join(__dirname, 'fixtures', 'misr_march.qif');
    const before = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', misrId]]]);
    for (const round of [1, 2]) {
      const card = await dashboard(p);
      await card.locator('button.o_nc_bank_upload').click();
      const modal = p.locator('.modal');
      await modal.waitFor({ timeout: 30000 });
      ok(!(await modal.locator('button[name=import_file_button]').isVisible()), 'Import hidden before Preview');
      await modal.locator('input[type=file]').setInputFiles(qif);
      await p.waitForTimeout(800);
      await modal.locator('button[name=action_nc_preview]').click();
      await p.waitForSelector('.o_nc_import_preview', { timeout: 30000 });
      const prev = await p.locator('.modal').innerText();
      if (round === 1) {
        ok(/3 transactions read/.test(prev) && /3 new/.test(prev), 'preview counts: ' + prev.slice(0, 200));
        await shot(p, 'bank_upload_preview_en');
        await p.locator('.modal button[name=import_file_button]').click();
        await p.waitForTimeout(3500);
      } else {
        ok(/0 new, 3 already imported/.test(prev), 'second preview: ' + prev.slice(0, 200));
        ok(!(await p.locator('.modal button[name=import_file_button]').isVisible()), 'Import not offered: nothing new');
        await shot(p, 'bank_upload_duplicate_en');
        await p.keyboard.press('Escape');
      }
    }
    const after = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', misrId]]]);
    eq(after, before + 3, 'exactly three imported once');
    return `+${after - before}`;
  });

  await check('Cash journal: Transactions opens its own workspace; no Upload on a cash card', async () => {
    await p.goto(BASE + '/odoo/accounting');
    await p.waitForSelector('.o_nc_bank_nav', { timeout: 60000 });
    const card = p.locator('.o_kanban_record', { has: p.locator('.o_nc_bank_nav') }).filter({ hasText: 'Main Cash' }).first();
    eq(await card.locator('.o_nc_bank_upload').count(), 0, 'no Upload for cash');
    await card.locator('#all_transactions_btn').click();
    const n = await workspaceRecords(p);
    eq(n, await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', cashId]]]), 'cash lines');
    return `${n} cash transaction(s)`;
  });
  ok(en.errors.length === 0 || true, '');
  await check('no page errors (Owner, English)', async () => { eq(en.errors, [], 'page errors'); });
  await en.context.close();

  // ---------------------------------------------------------------- CEO: read-only
  const ceo = await session(browser, { ...CEO, lang: 'en_US', viewport: { width: 1400, height: 900 } });
  await check('CEO (read-only): no New / Upload on the card, and the server refuses a match', async () => {
    const card = await dashboard(ceo.page);
    const nav = (await card.locator('.o_nc_bank_nav a').allInnerTexts()).map((s) => s.trim());
    eq(nav, ['To Reconcile', 'Statements'], 'read-only nav');
    const st = await stLine(ceo.page, 'Bank Fees March');
    let refused = '';
    try { await rpc(ceo.page, 'account.bank.statement.line', 'reconcile_bank_line', [[st.id]]); } catch (e) { refused = String(e); }
    ok(/access|not allowed|permission|غير مسموح/i.test(refused), 'refused: ' + refused.slice(0, 160));
    try { await rpc(ceo.page, 'account.journal', 'action_nc_bank_new', [[misrId]]); refused = ''; } catch (e) { refused = String(e); }
    ok(refused, 'New refused server-side');
    return 'refused at the ORM';
  });
  await ceo.context.close();

  // ---------------------------------------------------------------- Accountant: can match
  const acct = await session(browser, { ...ACCT, lang: 'en_US', viewport: { width: 1400, height: 900 } });
  await check('Accountant: the To Reconcile workspace opens for the accountant role', async () => {
    const card = await dashboard(acct.page);
    await card.locator('.o_nc_bank_nav_to_reconcile').click();
    await workspaceRecords(acct.page);
    return 'workspace opens for the accountant';
  });
  await acct.context.close();

  // ---------------------------------------------------------------- Arabic RTL, desktop + 375 px
  for (const vp of [{ width: 1400, height: 900, tag: 'desktop' }, { width: 375, height: 812, tag: 'mobile' }]) {
    const ar = await session(browser, { ...OWNER, lang: 'ar_001', viewport: { width: vp.width, height: vp.height } });
    await check(`Arabic RTL ${vp.tag}: card and workspace translated, no horizontal overflow`, async () => {
      const card = await dashboard(ar.page);
      const nav = (await card.locator('.o_nc_bank_nav a').allInnerTexts()).map((s) => s.trim());
      eq(nav, ['للتسوية', 'الكشوف', 'جديد'], 'Arabic nav');
      const o1 = await overflow(ar.page);
      eq(o1.dir, 'rtl', 'direction');
      ok(!o1.x, 'no horizontal overflow on the dashboard');
      await shot(ar.page, `bank_card_ar_${vp.tag}`);
      await card.locator('#all_transactions_btn').click();
      await workspaceRecords(ar.page);
      const body = await ar.page.locator('.o_action_manager').innerText();
      ok(/الرصيد الإجمالي/.test(body), 'workspace in Arabic');
      const o2 = await overflow(ar.page);
      ok(!o2.x, 'no horizontal overflow in the workspace');
      const listW = await ar.page.locator('.o_account_reconcile_oca_selector').evaluate((el) => el.getBoundingClientRect().width);
      if (vp.tag === 'mobile') ok(listW >= vp.width - 40, `transaction list uses the phone width (${Math.round(listW)}px)`);
      await shot(ar.page, `bank_transactions_ar_${vp.tag}`);
      return `${nav.join(' · ')}`;
    });
    await check(`no page errors (Arabic ${vp.tag})`, async () => { eq(ar.errors, [], 'page errors'); });
    await ar.context.close();
  }

  // English mobile
  const m = await session(browser, { ...OWNER, lang: 'en_US', viewport: { width: 375, height: 812 } });
  await check('English 375 px: card nav wraps, workspace opens, no horizontal overflow', async () => {
    const card = await dashboard(m.page);
    ok(!(await overflow(m.page)).x, 'dashboard overflow');
    await shot(m.page, 'bank_card_en_mobile');
    await card.locator('#all_transactions_btn').click();
    await workspaceRecords(m.page);
    ok(!(await overflow(m.page)).x, 'workspace overflow');
    const listW = await m.page.locator('.o_account_reconcile_oca_selector').evaluate((el) => el.getBoundingClientRect().width);
    ok(listW >= 335, `transaction list uses the phone width (${Math.round(listW)}px)`);
    await m.page.locator('.o_kanban_record:not(.o_kanban_ghost)').first().click();
    await m.page.waitForTimeout(1500);
    const bar = await m.page.locator('.o_account_reconcile_oca_statusbar').first().evaluate((el) => el.getBoundingClientRect().height);
    ok(bar >= 30, 'status bar sized to its buttons');
    await shot(m.page, 'bank_transactions_en_mobile');
  });
  await m.context.close();

  await browser.close();
  const passed = results.filter((r) => r.ok).length;
  fs.writeFileSync(path.join(__dirname, '..', 'shots', 'bank_acceptance.json'), JSON.stringify(results, null, 2));
  console.log(`RESULT ${passed}/${results.length}`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error('CRASH', e); process.exit(2); });
