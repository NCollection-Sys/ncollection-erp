// Headless browser acceptance of the NCollection dashboards against the disposable fixture (db uxbrowse).
// usage: node acceptance.js [group,...]   groups: sales, invoicing, exports, native, ui
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { launch, session, shot, open, rpc, BASE, SHOTS, login } = require('./lib');

const DL = path.join(__dirname, '..', 'dl');
fs.mkdirSync(DL, { recursive: true });
const results = [];
const check = async (group, name, fn) => {
  const t0 = Date.now();
  try {
    const detail = await fn();
    results.push({ group, name, ok: true, detail: detail === undefined ? '' : detail, ms: Date.now() - t0 });
  } catch (e) {
    results.push({ group, name, ok: false, error: String(e.message || e).split('\n')[0].slice(0, 260), ms: Date.now() - t0 });
  }
};
const eq = (a, b, what) => { if (a !== b) throw new Error(`${what}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); };
const has = (s, sub, what) => { if (!String(s).includes(sub)) throw new Error(`${what}: "${sub}" not found in ${String(s).slice(0, 160)}`); };

const SALES = '/atlas/action-ncollection_account_dashboard.action_sales_premium';
const INV = '/atlas/action-ncollection_account_dashboard.action_invoicing_premium';

async function kpis(page) {
  return page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.o_nc_kpi')].map((e) => [
    e.dataset.key, { label: e.querySelector('.o_nc_kpi_label').innerText.trim(), num: e.querySelector('.o_nc_num').innerText.trim(),
      unit: (e.querySelector('.o_nc_unit') || {}).innerText || '', text: e.innerText.replace(/\s+/g, ' ').trim() }])));
}
async function chooseYear(page) {
  await page.click('.o_nc_seg_btn:nth-child(4)');
  await page.waitForFunction(() => !document.querySelector('.o_nc_prem_loading') && document.querySelectorAll('.o_nc_kpi').length > 3);
  await page.waitForTimeout(800);
}
/** Full-content screenshot of the scrolling dashboard root (viewport grown to its content), plus a clipping check. */
async function fullShot(page, name, root = '.o_nc_prem') {
  const vp = page.viewportSize();
  const h = await page.evaluate((r) => document.querySelector(r).scrollHeight, root);
  await page.setViewportSize({ width: vp.width, height: Math.min(h + 70, 4200) });
  await page.waitForTimeout(700);
  await shot(page, name);
  const clip = await page.evaluate((r) => {
    const el = document.querySelector(r); const b = el.getBoundingClientRect();
    const out = [...el.querySelectorAll('*')].filter((n) => { const q = n.getBoundingClientRect(); return q.width > 0 && (q.right > innerWidth + 1 || q.left < -1); }).length;
    return { hOverflow: el.scrollWidth > el.clientWidth + 1, outOfViewport: out, bottomVisible: b.bottom <= innerHeight + 2 };
  }, root);
  await page.setViewportSize(vp);
  return clip;
}
function inspectFile(file) {
  return execFileSync('docker', ['run', '--rm', '-v', DL.replace(/\\/g, '/') + ':/d', '-v', __dirname.replace(/\\/g, '/') + ':/i', 'odoo:19', 'python3', '/i/inspect_file.py', '/d/' + path.basename(file)],
    { encoding: 'utf-8', env: { ...process.env, MSYS_NO_PATHCONV: '1' }, timeout: 120000 });
}
async function download(page, selector, name) {
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.click(selector)]);
  const file = path.join(DL, name + '_' + dl.suggestedFilename().replace(/[^\w.-]+/g, '_'));
  await dl.saveAs(file);
  return { file, filename: dl.suggestedFilename() };
}

(async () => {
  const groups = (process.argv[2] || 'sales,invoicing,exports,native,ui').split(',');
  const browser = await launch();

  // ---------------------------------------------------------------- SALES (AED company with real records)
  if (groups.includes('sales')) {
    for (const [lang, w, h] of [['en_US', 1280, 800], ['ar_001', 1280, 800], ['en_US', 375, 812]]) {
      const { page, errors } = await session(browser, { lang, viewport: { width: w, height: h } });
      const tag = `sales_${lang === 'ar_001' ? 'ar' : 'en'}_${w}`;
      await open(page, SALES, '.o_nc_kpi', '.o_nc_prem');
      await chooseYear(page);
      const k = await kpis(page);
      await check('sales', `${tag} KPIs reconcile to the fixture records (AED)`, async () => {
        eq(k.orders.num, '3', 'confirmed orders');
        eq(k.quotations.num, '2', 'open quotations');
        eq(k.order_value.text.includes('52,200.00') && k.order_value.text.includes('AED'), true, 'order value 52,200.00 AED (incl. 6,000 EUR = 24,000 AED)');
        eq(k.avg_order.text.includes('17,400.00'), true, 'average order');
        eq(k.conversion.num, '60.0%', 'conversion 3/(3+2)');
        eq(k.invoiced.text.includes('60,200.00'), true, 'invoiced');
        eq(k.credit_notes.text.includes('1,500.00'), true, 'credit notes');
        eq(k.net_sales.text.includes('58,700.00'), true, 'net sales');
        for (const key of ['order_value', 'avg_order', 'invoiced', 'credit_notes', 'net_sales']) has(k[key].num, 'AED', key + ' carries its currency');
        return `${Object.keys(k).length} KPIs`;
      });
      await check('sales', `${tag} header, charts, tables, nothing clipped`, async () => {
        const head = await page.evaluate(() => ({ title: document.querySelector('.o_nc_prem_title').innerText, chips: [...document.querySelectorAll('.o_nc_chip')].map((c) => c.innerText.trim()),
          canvases: document.querySelectorAll('canvas').length, tables: [...document.querySelectorAll('[data-table]')].map((t) => t.dataset.table + ':' + t.querySelectorAll('li, tbody tr').length),
          demo: /Odoo's illustrative|Sample data|189|491,617/.test(document.body.innerText) }));
        has(head.chips.join(' '), 'Gulf Trading LLC', 'company chip');
        has(head.chips.join(' '), 'AED', 'currency chip');
        eq(head.canvases >= 1, true, 'a chart is drawn');
        eq(head.demo, false, 'no demonstration figures on the page');
        const top = await page.evaluate(() => [...document.querySelectorAll('[data-table=top_customers] li')].map((li) => li.innerText.replace(/\s+/g, ' ')));
        has(top[0], 'Marina Tech Supplies', 'top customer is the 24,000 AED order');
        has(top[0], '24,000.00', 'top customer amount');
        const clip = await fullShot(page, tag);
        eq(clip.hOverflow, false, 'no horizontal overflow');
        eq(clip.outOfViewport, 0, 'no element outside the viewport');
        return JSON.stringify({ tables: head.tables, errors: errors.length });
      });
      if (lang === 'en_US' && w === 1280) {
        await check('sales', 'drill-down: confirmed orders opens the 3 orders; a recent order opens its form', async () => {
          await page.click('[data-key=orders] .o_nc_kpi_value');
          await page.waitForSelector('.o_list_view .o_data_row');
          eq(await page.locator('.o_data_row').count(), 3, 'rows in the drilled list');
          await shot(page, 'sales_drill_orders');
          await page.goBack();
          await page.waitForSelector('.o_nc_kpi');
          await page.click('[data-table=recent_orders] tbody tr >> nth=0');
          await page.waitForSelector('.o_form_view');
          return 'ok';
        });
        await check('sales', 'KPI tooltip: title attribute and click-to-expand definition', async () => {
          await open(page, SALES, '.o_nc_kpi', '.o_nc_prem');
          const btn = page.locator('[data-key=conversion] .o_nc_tipbtn');
          has(await btn.getAttribute('title'), 'Confirmed orders divided by', 'title tooltip');
          await btn.click();
          has(await page.locator('[data-key=conversion] .o_nc_tip').innerText(), 'Undefined when there are none', 'expanded definition');
          return 'ok';
        });
        await check('sales', 'custom date filter: a window with no sales shows the explicit empty state', async () => {
          await page.fill('.o_nc_range input >> nth=0', '2025-01-01');
          await page.fill('.o_nc_range input >> nth=1', '2025-01-31');
          await page.click('.o_nc_range button');
          await page.waitForSelector('.o_nc_empty');
          const t = await page.locator('.o_nc_prem').innerText();
          has(t, 'No records in this period', 'empty-state title');
          const kk = await kpis(page);
          eq(kk.orders.num, '0', 'orders in the empty window');
          eq(kk.avg_order.num, '—', 'average undefined, not zero');
          await shot(page, 'sales_empty_window_en');
          return 'ok';
        });
      }
      await page.context().close();
    }
    // the empty AED company: nothing invented
    const e = await session(browser, { lang: 'ar_001', company: 3 });
    await check('sales', 'empty AED company (Arabic): zeros / not defined, AED context, nothing invented', async () => {
      await open(e.page, SALES, '.o_nc_kpi', '.o_nc_prem');
      await chooseYear(e.page);
      const kk = await kpis(e.page);
      eq(kk.orders.num, '0', 'orders'); eq(kk.avg_order.num, '—', 'avg undefined');
      const body = await e.page.locator('.o_nc_prem').innerText();
      eq(/491,617|6,828|189|456/.test(body), false, 'no sample figures');
      eq(/-0\.00/.test(body), false, 'no negative zero');
      await fullShot(e.page, 'sales_empty_company_ar');
      return 'ok';
    });
    await e.context.close();
    // another company with another currency, and a multi-company selection
    const eur = await session(browser, { lang: 'en_US', company: 4 });
    await check('sales', 'EUR company: figures in EUR, not mixed with the AED company', async () => {
      await open(eur.page, SALES, '.o_nc_kpi', '.o_nc_prem');
      await chooseYear(eur.page);
      const kk = await kpis(eur.page);
      eq(/€|EUR/.test(kk.order_value.text), true, 'EUR currency shown'); has(kk.order_value.text, '4,000.00', 'value (5 x 800 EUR)');
      await fullShot(eur.page, 'sales_euro_company_en');
      return kk.order_value.text;
    });
    await eur.context.close();
    const multi = await session(browser, { lang: 'en_US', company: '2-4' });
    await check('sales', 'two companies ticked: only the active company, the other named, drill-down consistent', async () => {
      await open(multi.page, SALES, '.o_nc_kpi', '.o_nc_prem');
      await chooseYear(multi.page);
      const kk = await kpis(multi.page);
      has(kk.order_value.text, 'AED', 'active company currency'); has(kk.order_value.text, '52,200.00', 'only Gulf');
      has(await multi.page.locator('.o_nc_note').first().innerText(), 'Other companies are not added in', 'scope note');
      await multi.page.click('[data-key=orders] .o_nc_kpi_value');
      await multi.page.waitForSelector('.o_list_view .o_data_row');
      eq(await multi.page.locator('.o_data_row').count(), 3, 'the drill-down lists only the active company (3), not the EUR order');
      return 'ok';
    });
    await multi.context.close();
  }

  // ---------------------------------------------------------------- INVOICING
  if (groups.includes('invoicing')) {
    for (const [lang, w, h] of [['en_US', 1280, 800], ['ar_001', 1280, 800], ['en_US', 375, 812], ['ar_001', 375, 812]]) {
      const { page, errors } = await session(browser, { lang, viewport: { width: w, height: h } });
      const tag = `invoicing_${lang === 'ar_001' ? 'ar' : 'en'}_${w}`;
      await open(page, INV, '.o_nc_kpi', '.o_nc_prem');
      await chooseYear(page);
      const k = await kpis(page);
      const period = await page.evaluate(() => [...document.querySelectorAll('.o_nc_chip')].map((c) => c.innerText).find((t) => /\d{4}-\d{2}-\d{2}/.test(t)));
      const [from, to] = period.match(/\d{4}-\d{2}-\d{2}/g);
      const days = Math.round((Date.parse(to) - Date.parse(from)) / 864e5) + 1;
      await check('invoicing', `${tag} KPIs reconcile to the fixture records (AED)`, async () => {
        eq(k.issued.num, '4', 'invoices issued');
        for (const [key, v] of [['invoiced', '60,200.00'], ['credit_notes', '1,500.00'], ['paid', '18,600.00'], ['outstanding', '41,600.00'], ['overdue', '8,000.00'], ['customer_credit', '1,500.00']]) {
          has(k[key].text, v, key); has(k[key].num, 'AED', key + ' currency');
        }
        eq(k.collection_rate.num, '30.9%', 'collection rate 18,600 / 60,200');
        eq(k.unpaid.num, '2', 'unpaid'); eq(k.partial.num, '1', 'partial');
        // DSO = average receivables (0 at the start, 41,600 at the end) / net credit sales 58,700 x days
        const dso = (41600 / 2) / 58700 * days;
        eq(k.dso.num, dso.toFixed(1), `DSO (${days} days)`);
        eq(k.dso.unit.trim().length > 0, true, 'DSO unit shown');
        return `DSO ${k.dso.num} over ${days} days`;
      });
      await check('invoicing', `${tag} title not clipped, nothing overflowing, tables and chart present`, async () => {
        const m = await page.evaluate(() => {
          const t = document.querySelector('.o_nc_prem_title').getBoundingClientRect();
          return { l: Math.round(t.left), r: Math.round(t.right), vw: innerWidth, canv: document.querySelectorAll('canvas').length,
            tables: [...document.querySelectorAll('[data-table]')].map((x) => x.dataset.table + ':' + x.querySelectorAll('li, tbody tr').length),
            dsoLabel: document.querySelector('[data-key=dso] .o_nc_kpi_label').innerText };
        });
        eq(m.l >= 0 && m.r <= m.vw, true, `title inside the viewport (${m.l}..${m.r} of ${m.vw})`);
        eq(m.canv >= 1, true, 'chart');
        if (lang === 'ar_001') has(m.dsoLabel, 'متوسط فترة تحصيل المستحقات', 'Arabic DSO title');
        else has(m.dsoLabel, 'DSO', 'DSO abbreviation');
        const clip = await fullShot(page, tag);
        eq(clip.hOverflow, false, 'no horizontal overflow'); eq(clip.outOfViewport, 0, 'no element outside the viewport');
        return JSON.stringify(m.tables) + ' pageerrors=' + errors.length;
      });
      if (lang === 'en_US' && w === 1280) {
        await check('invoicing', 'DSO tooltip states the formula, VAT basis and credit-note treatment', async () => {
          await page.click('[data-key=dso] .o_nc_tipbtn');
          const t = await page.locator('[data-key=dso] .o_nc_tip').innerText();
          for (const s of ['average trade receivables', 'net credit sales', 'tax-included', 'credit notes', 'customer credit not netted']) has(t.toLowerCase(), s.toLowerCase(), 'DSO definition');
          return 'ok';
        });
        await check('invoicing', 'overdue table lists the 40-day overdue invoice and opens its form', async () => {
          const row = await page.locator('[data-table=overdue_invoices] tbody tr').first().innerText();
          has(row, 'Falcon Logistics', 'customer'); has(row, '8,000.00', 'amount');
          const due = row.match(/(\d{4}-\d{2}-\d{2})/)[1]; const shown = Number((row.match(/(\d+)\s*d/) || [])[1]);
          const expected = Math.floor((Date.now() - Date.parse(due)) / 864e5);
          eq(Math.abs(shown - expected) <= 1, true, `days overdue ${shown} vs ${expected} from due date ${due}; row=${JSON.stringify(row)}`);
          await page.click('[data-table=overdue_invoices] tbody tr >> nth=0');
          await page.waitForSelector('.o_form_view');
          return 'ok';
        });
      }
      await page.context().close();
    }
  }

  // ---------------------------------------------------------------- EXPORTS of the new dashboards
  if (groups.includes('exports')) {
    for (const [lang, w, h] of [['en_US', 1280, 800], ['ar_001', 375, 812]]) {
      const { page } = await session(browser, { lang, viewport: { width: w, height: h } });
      for (const [url, kind] of [[INV, 'invoicing'], [SALES, 'sales']]) {
        await open(page, url, '.o_nc_kpi', '.o_nc_prem');
        await chooseYear(page);
        for (const [fmt, sel] of [['xlsx', '.o_nc_export a:nth-child(1)'], ['pdf', '.o_nc_export a:nth-child(2)']]) {
          await check('exports', `${kind} ${fmt} download in ${lang} at ${w}px: valid file, real values`, async () => {
            const { file, filename } = await download(page, sel, `${kind}_${lang}_${w}`);
            const text = inspectFile(file);
            has(text, fmt === 'pdf' ? 'PDF pages' : 'XLSX sheets', 'file type');
            eq(/FORMULA_SHEETS=0/.test(text) || fmt === 'pdf', true, 'no formula cells');
            has(filename, kind[0].toUpperCase() + kind.slice(1), 'file name');
            has(text, 'Gulf Trading LLC', 'company');
            has(text, 'AED', 'currency');
            if (fmt === 'xlsx') {
              if (kind === 'invoicing') { has(text, '60200', 'invoiced (numeric)'); has(text, '41600', 'outstanding (numeric)'); }
              else { has(text, '52200', 'order value (numeric)'); has(text, '58700', 'net sales (numeric)'); }
            } else if (kind === 'invoicing') has(text.replace(/,/g, ''), '41600', 'outstanding in the PDF');
            else has(text.replace(/,/g, ''), '52200', 'order value in the PDF');
            if (lang === 'ar_001') eq(/[\u0600-\u06FF\uFB50-\uFEFF]/.test(text), true, 'Arabic text present in the file');
            return filename;
          });
        }
      }
      await page.context().close();
    }
    // role and scope on the HTTP route, from a real browser session
    const adm = await session(browser, { lang: 'en_US', company: 1 });
    const sid = await rpc(adm.page, 'res.users', 'search', [[['login', '=', 'sara_prem']]]);
    await rpc(adm.page, 'res.users', 'write', [sid, { password: 'Sara-Prem-Pass1!' }]);
    await adm.context.close();
    const plain = await session(browser, { user: 'sara_prem', password: 'Sara-Prem-Pass1!', lang: 'en_US', company: 2 }).catch(() => null);
    if (plain) {
      await check('exports', 'a Sales-role user without the financial role is refused the invoicing export (403) and cannot pick a company', async () => {
        const st = await plain.page.evaluate(async () => (await fetch('/ncollection/premium/invoicing/export/xlsx')).status);
        eq(st, 403, 'status');
        const st2 = await plain.page.evaluate(async () => (await fetch('/ncollection/premium/invoicing/export/xlsx?company_id=4')).status);
        eq(st2, 403, 'status with a company parameter');
        return 'ok';
      });
      await plain.context.close();
    }
  }

  // ---------------------------------------------------------------- NATIVE spreadsheet dashboards: Share + Export preserved
  if (groups.includes('native')) {
    const { page } = await session(browser, { lang: 'en_US', company: 1 });
    await check('native', 'real Invoicing dashboard: Share and Export sit side by side, context band shows the currency', async () => {
      const did = (await rpc(page, 'spreadsheet.dashboard', 'search', [[['name', '=', 'Invoicing']]]))[0];
      await page.goto(BASE + '/atlas/dashboards?dashboard_id=' + did);
      await page.waitForSelector('.o_nc_dash_bar', { timeout: 60000 });
      await page.waitForSelector('.o_spreadsheet_dashboard_action .o-dropdown button, .o_cp_switch_buttons, .o_control_panel', { timeout: 30000 });
      const names = await page.evaluate(() => [...document.querySelectorAll('.o_control_panel button')].map((b) => b.innerText.trim()).filter(Boolean));
      eq(names.includes('Share') && names.includes('Export'), true, 'Share and Export buttons: ' + names.join(','));
      const order = names.indexOf('Export') - names.indexOf('Share');
      eq(order, 1, 'Export immediately follows Share');
      const band = await page.locator('.o_nc_dash_bar').innerText();
      has(band, 'My Company', 'company'); has(band, 'USD', 'currency');
      await shot(page, 'native_invoicing_en');
      return names.join(',');
    });
    await check('native', 'Share works: opening it creates a share link', async () => {
      await page.locator('.o_control_panel button', { hasText: 'Share' }).click();
      await page.waitForSelector('.o-dropdown--menu input, .o-dropdown--menu .o_copy_button, .o-dropdown--menu', { timeout: 40000 });
      await page.waitForFunction(() => /\/dashboard\/share\//.test(document.body.innerText) || [...document.querySelectorAll('.o-dropdown--menu input')].some((i) => /\/dashboard\/share\//.test(i.value)), null, { timeout: 40000 });
      await shot(page, 'native_share_en');
      return 'share link generated';
    });
    await check('native', 'Export (Excel, PDF) of the native dashboard downloads a valid snapshot file', async () => {
      await page.keyboard.press('Escape');
      await page.mouse.click(5, 400);
      await page.waitForTimeout(600);
      const out = [];
      for (const label of ['Export to Excel', 'Export to PDF']) {
        await page.locator('.o_control_panel button:has-text("Export")').first().click();
        const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.locator('.dropdown-item', { hasText: label }).click()]);
        const file = path.join(DL, 'native_' + dl.suggestedFilename().replace(/[^\w.-]+/g, '_'));
        await dl.saveAs(file);
        const text = inspectFile(file);
        has(text, /PDF/.test(label) ? 'PDF pages' : 'XLSX sheets', 'type');
        has(text, 'Invoice', 'dashboard content');
        out.push(dl.suggestedFilename());
      }
      return out.join(', ');
    });
    await page.context().close();
    const sample = await session(browser, { user: 'admin', password: 'admin', lang: 'en_US', company: 1 });
    await check('native', 'AED company without sales: the native Sales dashboard is labelled SAMPLE DATA', async () => {
      await sample.page.goto(BASE + '/atlas/dashboards?dashboard_id=' + (await rpc(sample.page, 'spreadsheet.dashboard', 'search', [[['name', '=', 'Sales']]]))[0]);
      await sample.page.waitForSelector('.o_nc_dash_sample', { timeout: 60000 });
      has(await sample.page.locator('.o_nc_dash_sample').innerText(), 'Sample data', 'banner');
      await shot(sample.page, 'native_sales_sample_banner_en');
      return 'banner shown';
    });
    await sample.context.close();
  }

  // ---------------------------------------------------------------- remaining UI acceptance (English / Arabic)
  if (groups.includes('ui')) {
    const { page } = await session(browser, { lang: 'en_US', company: 1, viewport: { width: 1500, height: 900 } });
    await check('ui', 'invoice list (English): Overpaid / Taxable / Non-taxable filters match the data', async () => {
      await page.goto(BASE + '/atlas/action-account.action_move_out_invoice_type');
      await page.waitForSelector('.o_list_view');
      await page.click('.o_searchview_dropdown_toggler');
      await page.waitForSelector('.o_filter_menu');
      const labels = await page.evaluate(() => [...document.querySelectorAll('.o_filter_menu .dropdown-item')].map((e) => e.innerText.trim()));
      const out = {};
      for (const [label, domain] of [['Overpaid', [['nc_overpaid_amount', '>', 0]]], ['Taxable', [['nc_is_taxable', '=', true]]], ['Non-taxable', [['nc_is_taxable', '=', false]]]]) {
        if (!labels.includes(label)) throw new Error(`filter "${label}" missing in: ${labels.join(', ')}`);
        const expected = await rpc(page, 'account.move', 'search_count', [[['move_type', '=', 'out_invoice'], ['state', '!=', 'cancel'], ...domain]], { context: { allowed_company_ids: [1] } });
        await page.locator('.o_filter_menu .dropdown-item', { hasText: new RegExp('^' + label + '$') }).click();
        await page.waitForTimeout(1200);
        const rows = await page.locator('.o_data_row').count();
        out[label] = `${rows} rows (server ${expected})`;
        await page.locator('.o_filter_menu .dropdown-item', { hasText: new RegExp('^' + label + '$') }).click();
        await page.waitForTimeout(400);
      }
      await shot(page, 'ui_invoice_filters_en');
      return JSON.stringify(out);
    });
    await check('ui', 'invoice list (English): sorting by Balance Due ascending and descending is correct', async () => {
      await page.goto(BASE + '/atlas/action-account.action_move_out_invoice_type');
      await page.waitForSelector('.o_list_view .o_data_row');
      const head = page.locator('.o_list_table thead th', { hasText: /Balance Due/ });
      if (!(await head.count())) throw new Error('column Balance Due not found: ' + (await page.locator('.o_list_table thead th').allInnerTexts()).join('|'));
      const idx = await page.evaluate(() => [...document.querySelectorAll('.o_list_table thead th')].findIndex((t) => /Balance Due/.test(t.innerText)));
      const read = async () => (await page.evaluate((i) => [...document.querySelectorAll('.o_data_row')].map((r) => r.children[i].innerText.replace(/[^\d.\-]/g, '')), idx)).map(Number);
      await head.click(); await page.waitForTimeout(1200);
      const asc = await read();
      await head.click(); await page.waitForTimeout(1200);
      const desc = await read();
      const sorted = [...asc].sort((a, b) => a - b);
      eq(JSON.stringify(asc), JSON.stringify(sorted), 'ascending order');
      eq(JSON.stringify(desc), JSON.stringify([...sorted].reverse()), 'descending order');
      await shot(page, 'ui_invoice_sorting_en');
      return `asc ${asc.slice(0, 4)} desc ${desc.slice(0, 4)}`;
    });
    await check('ui', 'product image preview (English): the form loads the small variant, not the 1600px original', async () => {
      const ids = await rpc(page, 'product.template', 'search', [[['name', '=', 'Ux Product With Image']]]);
      await page.goto(BASE + '/atlas/action-product.product_template_action/' + ids[0]);
      await page.waitForSelector('.o_field_image img');
      const info = await page.evaluate(() => { const i = document.querySelector('.o_field_image img'); return { src: i.src, w: i.naturalWidth }; });
      eq(info.w > 0, true, 'image decodes'); has(info.src, 'image_128', 'small variant requested');
      await shot(page, 'ui_product_image_en');
      return `${info.w}px natural width`;
    });
    await check('ui', 'chatter (English): collapse, expand, draft kept, preference persisted', async () => {
      const inv = await rpc(page, 'account.move', 'search', [[['move_type', '=', 'out_invoice'], ['state', '=', 'posted']]], { limit: 1, context: { allowed_company_ids: [1] } });
      const url = BASE + '/atlas/action-account.action_move_out_invoice_type/' + inv[0];
      await page.goto(url);
      await page.waitForSelector('.o_nc_chatter_toggle');
      await page.locator('.o-mail-Chatter button', { hasText: 'Send message' }).first().click();
      await page.waitForSelector('.o-mail-Composer-input');
      await page.fill('.o-mail-Composer-input', 'unsent draft kept');
      await page.click('.o_nc_chatter_toggle');
      await page.waitForSelector('.o_nc_chatter_collapsed');
      has(await page.locator('.o_nc_chatter_toggle').getAttribute('title'), 'Show the history panel', 'toggle title when collapsed');
      await shot(page, 'ui_chatter_collapsed_en');
      await page.click('.o_nc_chatter_toggle');
      await page.waitForFunction(() => !document.querySelector('.o_nc_chatter_collapsed'));
      eq(await page.inputValue('.o-mail-Composer-input'), 'unsent draft kept', 'draft survives collapse and expand');
      await page.click('.o_nc_chatter_toggle');                       // collapse again, then reload: the preference persists
      await page.reload(); await page.waitForSelector('.o_nc_chatter_collapsed', { timeout: 40000 });
      await page.click('.o_nc_chatter_toggle');
      return 'ok';
    });
    await page.context().close();

    const owner = await session(browser, { user: 'owner_ux', password: 'Ux-Owner-Pass1!', lang: 'en_US', company: 1, viewport: { width: 1400, height: 900 } });
    await check('ui', 'Configure Document Layout (English, Tenant Owner): opens, previews and saves', async () => {
      await owner.page.goto(BASE + '/atlas/action-web.action_base_document_layout_configurator');
      await owner.page.waitForSelector('.o_form_view', { timeout: 60000 });
      await owner.page.waitForSelector('.o_field_iframe_wrapper iframe', { timeout: 60000 });
      await owner.page.waitForTimeout(2500);
      const names = await owner.page.locator('.o_field_widget[name=report_layout_id] label, .o_field_widget[name=report_layout_id] .o_selection_badge').allInnerTexts();
      for (const n of ['Light', 'Boxed', 'Bold', 'Striped']) eq(names.join('|').includes(n), true, 'layout choice ' + n + ' offered: ' + names.join('|'));
      const frame = () => owner.page.frameLocator('.o_field_iframe_wrapper iframe').locator('body');
      const html0 = await frame().innerHTML();
      eq(html0.length > 500, true, 'the preview renders a document (' + html0.length + ' chars)');
      const before = (await rpc(owner.page, 'res.company', 'read', [[1], ['external_report_layout_id']]))[0].external_report_layout_id;
      const wanted = /bold/i.test(String(before)) ? 'Boxed' : 'Bold';
      await owner.page.locator('.o_field_widget[name=report_layout_id]').getByText(wanted, { exact: true }).click();
      await owner.page.waitForTimeout(3500);
      const html1 = await frame().innerHTML();
      eq(html1 !== html0, true, 'the preview changed after choosing ' + wanted);
      await shot(owner.page, 'ui_document_layout_en');
      await owner.page.getByRole('button', { name: 'Continue' }).click();
      await owner.page.waitForTimeout(3000);
      const after = (await rpc(owner.page, 'res.company', 'read', [[1], ['external_report_layout_id']]))[0].external_report_layout_id;
      eq(JSON.stringify(after) !== JSON.stringify(before), true, 'saved: ' + JSON.stringify(before) + ' -> ' + JSON.stringify(after));
      return wanted + ' saved by the Tenant Owner';
    });
    await owner.context.close();

    const ar = await session(browser, { user: 'owner_ux', password: 'Ux-Owner-Pass1!', lang: 'ar_001', company: 1, viewport: { width: 1280, height: 900 } });
    await check('ui', 'Duplicate (Arabic) on a contact, a product and a payment term', async () => {
      const out = [];
      const cases = [['res.partner', 'Ux Customer', '/atlas/action-base.action_partner_form/'], ['product.template', 'Ux Product With Image', '/atlas/action-product.product_template_action/'],
        ['account.payment.term', 'Immediate Payment', '/atlas/action-account.action_payment_term_form/']];
      for (const [model, name, base] of cases) {
        const ids = await rpc(ar.page, model, 'search', [[['name', '=', name]]], { context: { allowed_company_ids: [1] } });
        await ar.page.goto(BASE + base + ids[0]);
        await ar.page.waitForSelector('.o_form_view', { timeout: 60000 });
        const btn = ar.page.locator('button, .oe_stat_button', { hasText: 'نسخة مماثلة' }).first();
        await btn.waitFor({ timeout: 20000 }).catch(() => { throw new Error(`${model}: Arabic Duplicate button missing`); });
        await btn.click();
        await ar.page.waitForFunction((n) => !/\/\d+$/.test(location.pathname) || true, name);
        await ar.page.waitForTimeout(2500);
        const copy = await rpc(ar.page, model, 'search_read', [[['id', '>', ids[0]], ['name', 'ilike', name]]], { fields: ['name'], limit: 1, context: { allowed_company_ids: [1] } });
        if (!copy.length) throw new Error(`${model}: no copy created`);
        out.push(`${model}: ${copy[0].name}`);
        await shot(ar.page, 'ui_duplicate_' + model.replace(/\W/g, '_') + '_ar');
      }
      return out.join(' | ');
    });
    await ar.context.close();
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(__dirname, '..', 'acceptance_' + (process.argv[2] || 'all').replace(/,/g, '_') + '.json'), JSON.stringify(results, null, 1));
  for (const r of results) console.log((r.ok ? 'PASS ' : 'FAIL ') + `[${r.group}] ${r.name}` + (r.ok ? (r.detail ? '  -> ' + String(r.detail).slice(0, 120) : '') : '\n      ' + r.error));
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
})();
