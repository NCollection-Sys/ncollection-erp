// Gap-closure browser acceptance (fixtures/bank_fixture_gaps.py on the DISPOSABLE uxbankweb). Every UI action is
// followed by an ORM read of the journal entries / residuals it produced.
const path = require('path');
const fs = require('fs');
const { launch, session, shot, rpc, BASE } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

const results = [];
let CURRENT = null;          // the page a failing check is shot from
async function check(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail: detail || '' });
    console.log('PASS', name, detail || '');
  } catch (e) {
    results.push({ name, ok: false, detail: String(e).slice(0, 400) });
    console.log('FAIL', name, String(e).slice(0, 400));
    if (CURRENT) await shot(CURRENT, 'gap_fail_' + results.length).catch(() => {});
  }
}
const eq = (a, b, msg) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
const ok = (v, msg) => { if (!v) throw new Error(msg); };
const round = (x) => Math.round(x * 100) / 100;

const OWNER = { user: 'owner_bank', password: 'Owner-Bank-Pass1!', company: 1 };
const CEO = { user: 'ceo_bank', password: 'Ceo-Bank-Pass1!', company: 1 };

async function stLine(page, label) {
  return (await rpc(page, 'account.bank.statement.line', 'search_read', [[['payment_ref', '=', label]]],
    { fields: ['is_reconciled', 'amount', 'journal_id', 'move_id'], limit: 1 }))[0];
}
async function moveLines(page, moveIds) {
  return rpc(page, 'account.move.line', 'search_read', [[['move_id', 'in', moveIds]]],
    { fields: ['account_id', 'balance', 'amount_currency', 'currency_id', 'tax_line_id', 'reconciled', 'move_id'] });
}
async function openJournal(page, journalName) {
  await page.goto(BASE + '/odoo/accounting');
  await page.waitForSelector('.o_nc_bank_nav', { timeout: 60000 });
  const card = page.locator('.o_kanban_record', { has: page.locator('.o_nc_bank_nav') }).filter({ hasText: journalName }).first();
  await card.locator('#all_transactions_btn').click();
  await page.waitForSelector('.o_account_reconcile_oca', { timeout: 60000 });
  await page.waitForTimeout(1500);
  return card;
}
async function select(page, label) {
  await page.locator('.o_kanban_record:not(.o_kanban_ghost)', { hasText: label }).first().click();
  await page.waitForSelector('.o_reconcile_widget_table', { timeout: 30000 });
  await page.waitForTimeout(1200);
}
async function modelButton(page, transfer) {
  // the model's name in the session language (the fixture's chart may have been loaded in Arabic). Found by its
  // counterpart: the internal-transfer model posts to the company's transfer account, the bank-fee model elsewhere.
  // (The Owner cannot read ir.model.data: Option A.)
  const company = (await rpc(page, 'res.company', 'read', [[1], ['transfer_account_id']]))[0];
  const models = await rpc(page, 'account.reconcile.model', 'search_read', [[['trigger', '=', 'manual']]], { fields: ['name', 'line_ids'] });
  for (const m of models) {
    const lines = await rpc(page, 'account.reconcile.model.line', 'read', [m.line_ids, ['account_id']]);
    const isTransfer = lines.some((l) => l.account_id && l.account_id[0] === company.transfer_account_id[0]);
    if (isTransfer === transfer) return page.locator('.o_form_view button:visible', { hasText: m.name }).first();
  }
  throw new Error('no ' + (transfer ? 'internal-transfer' : 'bank-fee') + ' model');
}
async function addItem(page, docName) {
  if (!(await page.locator('.o_reconcile_widget_table').innerText()).includes(docName)) {
    await page.locator('.o_reconcile_move_line_view .o_data_row', { hasText: docName }).first().click();
    await page.waitForTimeout(1500);
  }
}
async function validate(page) {
  await page.getByRole('button', { name: 'Reconcile', exact: true }).first().click();
  await page.waitForTimeout(3000);
}

(async () => {
  const browser = await launch();
  const s = await session(browser, { ...OWNER, lang: 'en_US', viewport: { width: 1400, height: 900 } });
  const p = s.page;
  CURRENT = p;
  const company = (await rpc(p, 'res.company', 'read', [[1], ['transfer_account_id', 'income_currency_exchange_account_id', 'account_journal_suspense_account_id']]))[0];

  await check('bank fee: the Bank Fees model button books the fee; journal entry balanced', async () => {
    await openJournal(p, 'Ahly Bank');
    await select(p, 'Bank Fees October');
    await (await modelButton(p, false)).click();
    await p.waitForTimeout(1500);
    await shot(p, 'gap_fee_before');
    await validate(p);
    const st = await stLine(p, 'Bank Fees October');
    eq(st.is_reconciled, true, 'reconciled');
    const lines = await moveLines(p, [st.move_id[0]]);
    eq(round(lines.reduce((a, l) => a + l.balance, 0)), 0, 'balanced');
    ok(!lines.some((l) => l.account_id[0] === company.account_journal_suspense_account_id[0]), 'no suspense left');
    return lines.map((l) => `${l.account_id[1]} ${l.balance}`).join(' | ');
  });

  await check('internal transfer: both legs through the Internal Transfers model; transfer account nets to 0', async () => {
    for (const [journal, label] of [['Bank Misr', 'Transfer to Ahly'], ['Ahly Bank', 'Transfer from Misr']]) {
      await openJournal(p, journal);
      await select(p, label);
      await (await modelButton(p, true)).click();
      await p.waitForTimeout(1500);
      await validate(p);
      eq((await stLine(p, label)).is_reconciled, true, label);
    }
    const out = await stLine(p, 'Transfer to Ahly');
    const into = await stLine(p, 'Transfer from Misr');
    const lines = await moveLines(p, [out.move_id[0], into.move_id[0]]);
    const legs = lines.filter((l) => l.account_id[0] === company.transfer_account_id[0]);
    eq(legs.length, 2, 'two transfer legs');
    eq(round(legs.reduce((a, l) => a + l.balance, 0)), 0, 'transfer account nets to zero');
    const kinds = await rpc(p, 'account.account', 'read', [[...new Set(lines.map((l) => l.account_id[0]))], ['account_type']]);
    ok(!kinds.some((k) => /income|expense/.test(k.account_type)), 'no income / expense touched');
    return 'legs ' + legs.map((l) => l.balance).join(' / ');
  });

  await check('multi-currency: EUR receipt for a EUR invoice booked at 4.00 -> paid, realised gain 40.00 once', async () => {
    const before = await rpc(p, 'account.move.line', 'search_read', [[['account_id', '=', company.income_currency_exchange_account_id[0]], ['parent_state', '=', 'posted']]], { fields: ['balance'] });
    await openJournal(p, 'EUR Bank');
    await select(p, 'EUR receipt Nile');
    const inv = (await rpc(p, 'account.move', 'search_read', [[['currency_id.name', '=', 'EUR'], ['move_type', '=', 'out_invoice']]], { fields: ['name'] }))[0];
    if (!(await p.locator('.o_reconcile_widget_table').innerText()).includes(inv.name)) {
      await p.locator('.o_reconcile_move_line_view .o_data_row', { hasText: inv.name }).first().click();
      await p.waitForTimeout(1500);
    }
    await shot(p, 'gap_fx_before');
    await validate(p);
    const st = await stLine(p, 'EUR receipt Nile');
    eq(st.is_reconciled, true, 'reconciled');
    const invNow = (await rpc(p, 'account.move', 'read', [[(await rpc(p, 'account.move', 'search', [[['name', '=', inv.name]]]))[0]], ['amount_residual', 'payment_state']]))[0];
    eq(invNow.amount_residual, 0, 'invoice residual');
    const after = await rpc(p, 'account.move.line', 'search_read', [[['account_id', '=', company.income_currency_exchange_account_id[0]], ['parent_state', '=', 'posted']]], { fields: ['balance'] });
    const gain = round(-(after.reduce((a, l) => a + l.balance, 0) - before.reduce((a, l) => a + l.balance, 0)));
    eq(gain, 40, 'realised gain in AED');
    const bank = (await moveLines(p, [st.move_id[0]])).find((l) => l.currency_id[1] === 'EUR' && l.balance > 0);
    eq([bank.balance, bank.amount_currency], [440, 100], 'bank line: 100 EUR = 440.00 AED');
    await shot(p, 'gap_fx_after');
    return `${inv.name} ${invNow.payment_state}, gain ${gain}`;
  });

  await check('early payment discount: button shown, applied, invoice paid in full, tax corrected', async () => {
    await openJournal(p, 'Ahly Bank');
    await select(p, 'Early payment Nile');
    const disc = (await rpc(p, 'account.move', 'search_read', [[['invoice_payment_term_id.name', '=', '2/10 net 30']]], { fields: ['name'] }))[0];
    await addItem(p, disc.name);
    const btn = p.locator('button.o_nc_apply_discount:visible').first();
    await btn.waitFor({ state: 'visible', timeout: 20000 });     // offered once the form has loaded the match
    await btn.click();
    await p.waitForTimeout(2000);
    await shot(p, 'gap_discount_applied');
    await validate(p);
    const st = await stLine(p, 'Early payment Nile');
    eq(st.is_reconciled, true, 'reconciled');
    const inv = (await rpc(p, 'account.move', 'search_read', [[['invoice_payment_term_id.name', '=', '2/10 net 30']]], { fields: ['amount_residual', 'payment_state', 'amount_total'] }))[0];
    eq([inv.amount_total, inv.amount_residual], [1050, 0], 'paid in full');
    const lines = await moveLines(p, [st.move_id[0]]);
    const tax = lines.filter((l) => l.tax_line_id).reduce((a, l) => a + l.balance, 0);
    eq(round(tax), 1, 'VAT reversed on the discount: 2% of 50');
    eq(round(lines.reduce((a, l) => a + l.balance, 0)), 0, 'balanced');
    return `${inv.payment_state}`;
  });

  await check('batch: "Match a batch" adds both payments; one bank line settles them', async () => {
    await openJournal(p, 'Ahly Bank');
    await select(p, 'Deposit slip 42');
    await p.locator('button.o_nc_match_batch').click();
    const modal = p.locator('.modal');
    await modal.waitFor({ timeout: 30000 });
    const input = modal.locator('.o_field_widget[name=batch_id] input');
    await input.click();
    await input.fill('BATCH');
    await p.locator('.o-autocomplete--dropdown-item').first().click();
    await p.waitForTimeout(800);
    await shot(p, 'gap_batch_dialog');
    await modal.locator('button.o_nc_batch_add').click();
    await p.waitForTimeout(2500);
    await validate(p);
    const st = await stLine(p, 'Deposit slip 42');
    eq(st.is_reconciled, true, 'reconciled');
    const batch = (await rpc(p, 'ncollection.bank.payment.batch', 'search_read', [[]], { fields: ['state', 'amount'] }))[0];
    eq([batch.state, batch.amount], ['matched', 750], 'batch matched');
    return 'batch matched';
  });

  await check('duplicate review: the hand-booked pair is proposed with a reason; Not a duplicate records it; nothing deleted', async () => {
    const ahly = (await rpc(p, 'account.journal', 'search', [[['code', '=', 'BAHL']]]))[0];
    const count = await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', ahly]]]);
    await p.goto(BASE + '/odoo/accounting');
    await p.waitForSelector('.o_nc_bank_nav', { timeout: 60000 });
    const card = p.locator('.o_kanban_record', { has: p.locator('.o_nc_bank_nav') }).filter({ hasText: 'Ahly Bank' }).first();
    await card.locator('.o_nc_bank_nav_duplicates').click();
    await p.waitForSelector('.o_nc_duplicate_candidates', { timeout: 30000 });
    const rows = p.locator('.o_nc_duplicate_candidates .o_data_row');
    eq(await rows.count(), 1, 'one candidate');
    const text = await rows.first().innerText();
    ok(/Cash deposit counter 3/.test(text) && /same date, same amount, same label/.test(text), 'reason shown: ' + text.replace(/\s+/g, ' ').slice(0, 200));
    await shot(p, 'gap_duplicates');
    await rows.first().locator('button.o_nc_not_duplicate').click();
    await p.waitForTimeout(2000);
    eq(await rpc(p, 'account.bank.statement.line', 'search_count', [[['journal_id', '=', ahly]]]), count, 'nothing deleted');
    eq(await rpc(p, 'account.bank.statement.line', 'search_count', [[['nc_duplicate_reviewed', '=', true]]]), 2, 'both marked');
    return 'reviewed';
  });

  await check('undo and retry in the UI after a multi-currency match: gain reversed, then booked once again', async () => {
    await openJournal(p, 'EUR Bank');
    await select(p, 'EUR receipt Nile');
    await shot(p, 'gap_undo_before');
    const undo = p.locator('.o_form_view button:visible', { hasText: 'Unreconcile' }).first();
    await undo.waitFor({ state: 'visible', timeout: 20000 });
    await undo.click();
    const dlg = p.locator('.modal-dialog');
    if (await dlg.count()) await dlg.getByRole('button', { name: /^(Ok|OK|Confirm)$/ }).first().click();
    await p.waitForTimeout(3000);
    const fx = async () => round(-(await rpc(p, 'account.move.line', 'search_read', [[['account_id', '=', company.income_currency_exchange_account_id[0]], ['parent_state', '=', 'posted']]], { fields: ['balance'] })).reduce((a, l) => a + l.balance, 0));
    eq(await fx(), 0, 'gain reversed by the undo');
    eq((await stLine(p, 'EUR receipt Nile')).is_reconciled, false, 'open again');
    await select(p, 'EUR receipt Nile');
    const inv = (await rpc(p, 'account.move', 'search_read', [[['currency_id.name', '=', 'EUR'], ['move_type', '=', 'out_invoice']]], { fields: ['name'] }))[0];
    if (!(await p.locator('.o_reconcile_widget_table').innerText()).includes(inv.name)) {
      await p.locator('.o_reconcile_move_line_view .o_data_row', { hasText: inv.name }).first().click();
      await p.waitForTimeout(1500);
    }
    await validate(p);
    eq(await fx(), 40, 'booked once again, not twice');
    return 'net gain 40.00';
  });
  await check('no page errors (Owner)', async () => { eq(s.errors, [], 'page errors'); });
  await s.context.close();

  const ceo = await session(browser, { ...CEO, lang: 'en_US', viewport: { width: 1400, height: 900 } });
  await check('CEO (read-only): no discount / batch buttons; server refuses discount, batch match and Not a duplicate', async () => {
    const st = await stLine(ceo.page, 'Cash deposit counter 3');
    const batch = (await rpc(ceo.page, 'ncollection.bank.payment.batch', 'search', [[]]))[0];
    for (const [method, args] of [['action_nc_apply_early_discount', [[st.id]]], ['action_nc_match_batch', [[st.id], batch]]]) {
      let refused = '';
      try { await rpc(ceo.page, 'account.bank.statement.line', method, args); } catch (e) { refused = String(e); }
      ok(refused, method + ' refused');
    }
    let created = true;
    try { await rpc(ceo.page, 'ncollection.bank.payment.batch', 'create', [{ journal_id: 1, payment_type: 'inbound' }]); } catch (e) { created = false; }
    ok(!created, 'CEO cannot create a batch');
    return 'refused at the ORM';
  });
  await ceo.context.close();

  const ar = await session(browser, { ...OWNER, lang: 'ar_001', viewport: { width: 1280, height: 900 } });
  await check('Arabic: new card links and the duplicate review in Arabic', async () => {
    await ar.page.goto(BASE + '/odoo/accounting');
    await ar.page.waitForSelector('.o_nc_bank_nav', { timeout: 60000 });
    const card = ar.page.locator('.o_kanban_record', { has: ar.page.locator('.o_nc_bank_nav') }).filter({ hasText: 'Ahly Bank' }).first();
    const nav = (await card.locator('.o_nc_bank_nav a').allInnerTexts()).map((t) => t.trim());
    ok(nav.includes('الدفعات المجمعة') && nav.includes('المكررات'), 'Arabic links: ' + nav.join(' · '));
    await shot(ar.page, 'gap_card_ar');
    return nav.join(' · ');
  });
  await ar.context.close();

  await browser.close();
  const passed = results.filter((r) => r.ok).length;
  fs.writeFileSync(path.join(__dirname, '..', 'shots', 'bank_gaps_acceptance.json'), JSON.stringify(results, null, 2));
  console.log(`RESULT ${passed}/${results.length}`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error('CRASH', e); process.exit(2); });
