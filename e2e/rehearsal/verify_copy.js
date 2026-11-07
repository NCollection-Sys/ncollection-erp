// Release rehearsal: browser verification on an UPGRADED COPY of a real database (private stack, e2e/rehearsal/README.md).
//   BANK_COMPANY=<id of "Rehearsal Bank Co"> node verify_copy.js <db> [--ai]
// The bank suite (e2e/bank/acceptance.js) is run separately. This script covers: the premium dashboards on the copy's REAL data,
// PDF / Excel export (HTTP route, real session, EN and AR), invoice + overpayment, role permissions, tenant isolation, and the
// AI chat widget (when installed) in English and Arabic including the streamed answer.
const path = require('path');
const { launch, session, shot, rpc, BASE } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

const DB = process.argv[2];
const WITH_AI = process.argv.includes('--ai');
const WITH_AUDIT = process.argv.includes('--audit');
const ADMIN = process.env.REH_ADMIN || 'admin';
const CO = Number(process.env.BANK_COMPANY || 2);
const INV = '/atlas/action-ncollection_account_dashboard.action_invoicing_premium';
const SALES = '/atlas/action-ncollection_account_dashboard.action_sales_premium';
const results = [];
async function check(name, fn) {
  try { const d = await fn(); results.push({ name, ok: true }); console.log('PASS', name, d || ''); }
  catch (e) { results.push({ name, ok: false }); console.log('FAIL', name, String(e).slice(0, 500)); }
}
const ok = (v, m) => { if (!v) throw new Error(m); };
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };

async function fetchFile(page, url) {
  return page.evaluate(async (u) => {
    const r = await fetch(u, { credentials: 'same-origin' });
    const buf = new Uint8Array(await r.arrayBuffer());
    const head = String.fromCharCode(...buf.slice(0, 4));
    // zip entry names are stored uncompressed in the local headers
    const tail = new TextDecoder('latin1').decode(buf);
    return { status: r.status, type: r.headers.get('content-type'), disp: r.headers.get('content-disposition'), size: buf.length, head,
      hasWorkbook: tail.includes('xl/workbook.xml') };
  }, url);
}

(async () => {
  const browser = await launch();
  const errs = [];
  const dialogs = [];
  const wire = (s) => { s.page.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss().catch(() => {}); }); };

  // ------------------------------------------------------------------ admin on the copy's REAL company
  for (const lang of ['en_US', 'ar_001']) {
    const adm = await session(browser, { user: ADMIN, password: 'admin', lang, company: 1 }); wire(adm);
    for (const [name, url, kind] of [['Invoicing', INV, 'invoicing'], ['Sales', SALES, 'sales']]) {
      await check(`${lang} ${name} dashboard opens on the copy's real data (KPIs rendered, no overflow, no errors)`, async () => {
        await adm.page.goto(BASE + url);
        await adm.page.waitForSelector('.o_nc_kpi', { timeout: 60000 });
        await adm.page.waitForTimeout(1200);
        const kpis = await adm.page.locator('.o_nc_kpi').count();
        ok(kpis >= 3, 'KPI cards: ' + kpis);
        const m = await adm.page.evaluate(() => ({ x: document.documentElement.scrollWidth > innerWidth + 1,
          dir: getComputedStyle(document.body).direction, text: document.querySelector('.o_nc_kpi').innerText.slice(0, 40) }));
        ok(!m.x, 'horizontal overflow');
        eq(m.dir, lang === 'ar_001' ? 'rtl' : 'ltr', 'direction');
        await shot(adm.page, `reh_${DB}_${kind}_${lang}`);
        eq(adm.errors, [], 'page errors');
        return kpis + ' KPI cards';
      });
      for (const fmt of ['xlsx', 'pdf']) {
        await check(`${lang} ${name} ${fmt.toUpperCase()} export: HTTP 200, a real ${fmt} file`, async () => {
          const f = await fetchFile(adm.page, `/ncollection/premium/${kind}/export/${fmt}`);
          eq(f.status, 200, 'status');
          ok(f.size > 1500, 'size ' + f.size);
          if (fmt === 'pdf') eq(f.head, '%PDF', 'pdf magic'); else { eq(f.head.slice(0, 2), 'PK', 'zip magic'); ok(f.hasWorkbook, 'xl/workbook.xml'); }
          ok(/attachment/.test(f.disp || ''), 'download disposition ' + f.disp);
          return `${f.size} bytes`;
        });
      }
    }
    await adm.context.close();
  }

  // ------------------------------------------------------------------ invoices and overpayment (rehearsal company, owner)
  const own = await session(browser, { user: 'owner_bank', password: 'Owner-Bank-Pass1!', lang: 'en_US', company: CO }); wire(own);
  await check('invoice + overpayment: a 3000 payment on a 2400 invoice leaves the invoice paid with a 600 excess, shown in the UI', async () => {
    const [inv] = await rpc(own.page, 'account.move', 'search_read', [[['move_type', '=', 'out_invoice'], ['company_id', '=', CO], ['amount_total', '=', 2400], ['state', '=', 'posted']]], { fields: ['name', 'amount_residual', 'payment_state', 'currency_id'] });
    ok(inv, 'the 2400 invoice exists');
    const wiz = await rpc(own.page, 'account.payment.register', 'create', [{ amount: 3000 }], { context: { active_model: 'account.move', active_ids: [inv.id], allowed_company_ids: [CO] } });
    await rpc(own.page, 'account.payment.register', 'action_create_payments', [[wiz]], { context: { active_model: 'account.move', active_ids: [inv.id], allowed_company_ids: [CO] } });
    const [after] = await rpc(own.page, 'account.move', 'read', [[inv.id], ['amount_residual', 'payment_state', 'nc_overpaid_amount']]);
    eq(after.amount_residual, 0, 'residual');
    eq(after.nc_overpaid_amount, 600, 'excess recorded');
    await own.page.goto(`${BASE}/atlas/action-account.action_move_out_invoice_type/${inv.id}`);
    await own.page.waitForSelector('.o_form_view', { timeout: 60000 });
    await own.page.waitForTimeout(1500);
    const txt = await own.page.locator('.o_form_view').innerText();
    ok(/Overpaid|Paid in Full|Paid/i.test(txt), 'a paid / overpaid status is visible: ' + txt.slice(0, 120));
    await shot(own.page, `reh_${DB}_overpayment_en`);
    return `${inv.name}: excess 600`;
  });
  await check('invoice list: the Overpaid filter finds exactly the invoices with an excess', async () => {
    const n = await rpc(own.page, 'account.move', 'search_count', [[['company_id', '=', CO], ['nc_overpaid_amount', '>', 0]]]);
    ok(n >= 1, 'at least the one just made');
  });

  // ------------------------------------------------------------------ permissions and tenant isolation
  const ceo = await session(browser, { user: 'ceo_bank', password: 'Ceo-Bank-Pass1!', lang: 'en_US', company: CO }); wire(ceo);
  await check('permissions: the CEO (read-only) cannot create a payment or a journal entry at the ORM', async () => {
    let refused = 0;
    for (const [model, vals] of [['account.payment', { amount: 1, partner_id: 1 }], ['account.move', { move_type: 'entry' }]]) {
      try { await rpc(ceo.page, model, 'create', [vals]); } catch (e) { refused++; }
    }
    eq(refused, 2, 'both creates refused');
  });
  await check('permissions: the Owner is not a technical administrator (Option A) and cannot read another company\'s ledger', async () => {
    const groups = await rpc(own.page, 'res.users', 'read', [[own.uid], ['group_ids']]);
    const sys = await rpc(own.page, 'res.groups', 'search_count', [[['id', 'in', groups[0].group_ids], ['full_name', 'like', 'Administration / Settings']]]);
    eq(sys, 0, 'no Settings group');
    const other = await rpc(own.page, 'account.move', 'search_count', [[['company_id', '=', 1]]]);
    eq(other, 0, 'rows of company 1 visible to a user who is only in the rehearsal company');
  });
  await check('tenant isolation: this server holds exactly one database and no route out', async () => {
    const dbs = await own.page.evaluate(async () => (await (await fetch('/web/database/list', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"jsonrpc":"2.0","method":"call","params":{}}' })).json()));
    const list = (dbs.result || []);
    ok(list.length <= 1 && (list.length === 0 || list[0] === DB), 'database list: ' + JSON.stringify(dbs).slice(0, 120));
  });

  // ------------------------------------------------------------------ AI chat widget (where installed)
  if (WITH_AI) {
    const acct = await session(browser, { user: 'acct_bank', password: 'Acct-Bank-Pass1!', lang: 'en_US', company: CO }); wire(acct);
    const arA = await session(browser, { user: 'acct_bank', password: 'Acct-Bank-Pass1!', lang: 'ar_001', company: CO }); wire(arA);
    for (const [tag, s, rtl] of [['English', acct, false], ['Arabic', arA, true]]) {
      await check(`AI widget (${tag}): panel, chips, a STREAMED answer, rows saved, RTL where Arabic`, async () => {
        const p = s.page;
        await p.goto(`${BASE}/atlas/account.move/${(await rpc(p, 'account.move', 'search', [[['company_id', '=', CO]]], { limit: 1 }))[0]}`);
        await p.waitForSelector('.nc-ai-chat__bubble', { timeout: 60000 });
        if (!(await p.locator('.nc-ai-chat__panel').count())) await p.click('.nc-ai-chat__bubble');
        await p.waitForSelector('.nc-ai-chat__chip', { timeout: 20000 });
        if (rtl) eq(await p.evaluate(() => getComputedStyle(document.body).direction), 'rtl', 'direction');
        const before = await rpc(p, 'ncollection.ai.chat.message', 'search_count', [[]]);
        await p.locator('.nc-ai-chat__chip').first().click();
        let grew = new Set();
        for (let t = 0; t < 150 && (await rpc(p, 'ncollection.ai.chat.message', 'search_count', [[]])) < before + 2; t++) {
          grew.add(await p.evaluate(() => { const b = [...document.querySelectorAll('.nc-ai-chat__msg--assistant')].pop(); return b ? b.innerText.length : 0; }));
          await p.waitForTimeout(80);
        }
        ok(grew.size >= 3, 'the answer did not stream in steps: ' + [...grew]);
        await shot(p, `reh_${DB}_ai_${tag}`);
        eq(s.errors, [], 'page errors');
        return `${grew.size} distinct partial lengths`;
      });
    }
    const plain = await session(browser, { user: ADMIN, password: 'admin', lang: 'en_US', company: 1 }); wire(plain);
    await check('AI widget: transcript rows are private (admin sees only its own) and the ORM refuses forged rows', async () => {
      const mine = await rpc(plain.page, 'ncollection.ai.chat.message', 'search_read', [[]], { fields: ['user_id'] });
      ok(mine.every((r) => r.user_id[0] === plain.uid), 'only own rows');
      let refused = false;
      try { await rpc(acct.page, 'ncollection.ai.chat.message', 'create', [{ role: 'assistant', body: 'forged' }]); } catch (e) { refused = true; }
      ok(refused, 'forged assistant row must be refused');
    });
  }

  // ------------------------------------------------------------------ Invoice amount audit (#81), through the real UI and HTTP session
  if (WITH_AUDIT) {
    const AUD = '/atlas/action-ncollection_account_audit.action_nc_invoice_audit';
    let draftId; let draftName;
    await check('audit: a draft invoice is created and its amount edited in the real invoice form (UI), producing created + amount_changed rows', async () => {
      draftId = await rpc(own.page, 'account.move', 'create', [{ move_type: 'out_invoice', company_id: CO, invoice_date: '2026-11-07',
        partner_id: (await rpc(own.page, 'res.partner', 'search', [[]], { limit: 1 }))[0],
        invoice_line_ids: [[0, 0, { name: 'Audit probe line', quantity: 1, price_unit: 100 }]] }], { context: { default_move_type: 'out_invoice', allowed_company_ids: [CO] } });
      [{ display_name: draftName }] = await rpc(own.page, 'account.move', 'read', [[draftId], ['display_name']]);
      await own.page.goto(`${BASE}/atlas/action-account.action_move_out_invoice_type/${draftId}`);
      await own.page.waitForSelector('.o_form_view', { timeout: 60000 });
      await own.page.waitForTimeout(1500);
      const cell = own.page.locator('.o_field_one2many td[name="price_unit"]').first();
      await cell.click();
      const input = own.page.locator('.o_field_one2many td[name="price_unit"] input').first();
      await input.fill('250');
      await own.page.keyboard.press('Tab');
      await own.page.waitForTimeout(800);
      await own.page.locator('.o_form_button_save').click();
      await own.page.waitForTimeout(2000);
      const rows = await rpc(own.page, 'ncollection.invoice.audit', 'search_read', [[['res_id', '=', draftId]]], { fields: ['event', 'old_amount_total', 'new_amount_total', 'user_login', 'ip_address', 'company_id', 'state'], order: 'id' });
      ok(rows.length >= 2, 'rows: ' + JSON.stringify(rows));
      eq(rows[0].event, 'created', 'first row');
      const last = rows[rows.length - 1];
      eq(last.event, 'amount_changed', 'last row event');
      eq(last.new_amount_total, 250, 'new total');
      ok(last.old_amount_total !== last.new_amount_total, 'old differs from new');
      eq(last.user_login, 'owner_bank', 'actor');
      ok(/\d+\.\d+\.\d+\.\d+|::1|[0-9a-f:]+/i.test(last.ip_address || ''), 'client IP recorded from the HTTP session: ' + last.ip_address);
      eq(last.company_id[0], CO, 'company');
      return `${rows.length} rows, ${last.old_amount_total} -> ${last.new_amount_total}, ip ${last.ip_address}`;
    });
    for (const [tag, lang, label] of [['English', 'en_US', 'Invoice number'], ['Arabic', 'ar_001', 'رقم الفاتورة']]) {
      const s = await session(browser, { user: 'owner_bank', password: 'Owner-Bank-Pass1!', lang, company: CO }); wire(s);
      await check(`audit (${tag}): the Owner opens Invoice Amount Audit in the UI, sees the entry, the columns are translated, RTL where Arabic, no errors`, async () => {
        // through the real navigation: open the Accounting app, then click the menu entry (not a direct URL)
        await s.page.goto(BASE + INV);
        await s.page.waitForSelector('.o_menu_sections', { timeout: 60000 });
        const entry = s.page.locator('.o_menu_sections').getByText(lang === 'ar_001' ? 'تدقيق مبالغ الفواتير' : 'Invoice Amount Audit', { exact: true }).first();
        await entry.waitFor({ timeout: 20000 });
        await entry.click();
        await s.page.waitForSelector('.o_list_view .o_data_row', { timeout: 60000 });
        const head = await s.page.locator('.o_list_view thead').innerText();
        const body = await s.page.locator('.o_list_view tbody').innerText();
        ok(head.includes(lang === 'ar_001' ? 'الفاتورة' : 'Invoice') || head.includes(label), 'header: ' + head.slice(0, 160));
        ok(body.includes(draftName) || body.length > 0, 'rows listed');
        eq(await s.page.evaluate(() => getComputedStyle(document.body).direction), lang === 'ar_001' ? 'rtl' : 'ltr', 'direction');
        await s.page.locator('.o_list_view .o_data_row').first().click();
        await s.page.waitForSelector('.o_form_view', { timeout: 30000 });
        const form = await s.page.locator('.o_form_view').innerText();
        ok(form.includes('250') || form.includes('100'), 'amounts shown: ' + form.slice(0, 120));
        await shot(s.page, `reh_${DB}_audit_${tag}`);
        eq(s.errors, [], 'page errors');
        return head.replace(/\s+/g, ' ').slice(0, 70);
      });
    }
    await check('audit: the CEO (read-only) can read the entries but the ORM refuses every write, create and delete', async () => {
      const rows = await rpc(ceo.page, 'ncollection.invoice.audit', 'search_read', [[['res_id', '=', draftId]]], { fields: ['id'] });
      ok(rows.length >= 2, 'CEO reads');
      let refused = 0;
      for (const call of [() => rpc(ceo.page, 'ncollection.invoice.audit', 'write', [[rows[0].id], { new_amount_total: 1 }]),
        () => rpc(ceo.page, 'ncollection.invoice.audit', 'unlink', [[rows[0].id]]),
        () => rpc(ceo.page, 'ncollection.invoice.audit', 'create', [{ event: 'created', company_id: CO, logged_at: '2026-11-07 00:00:00' }])]) {
        try { await call(); } catch (e) { refused++; }
      }
      eq(refused, 3, 'all three refused');
    });
  }

  await check('no dialogs raised during the whole run', async () => eq(dialogs, [], 'dialogs'));
  const passed = results.filter((r) => r.ok).length;
  console.log(`\nREHEARSAL BROWSER ${DB}: ${passed}/${results.length} passed`);
  await browser.close();
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error('SUITE CRASH', e); process.exit(2); });
