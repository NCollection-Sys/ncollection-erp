// PDF quality gate: runs 12 reports through the real service, fetches each PDF from Odoo's own /report/pdf route as the
// logged-in user and INSPECTS the file (pages, page size, extracted text, page numbers, Arabic text, company, title).
// usage: node pdf_quality.js <db> <en|ar> <output-prefix>      needs: npm i pdfjs-dist@4.10.38   (manual; not in CI)
// It sets the user language through the ORM (a raw SQL update leaves Odoo's cached user context stale).
// usage: node pdfval.js <db> <lang-tag> <outprefix>
// Runs each report through the real service, fetches the PDF from Odoo's own /report/pdf route as the
// logged-in user, and inspects the PDF itself (pages, size, extracted text).
const http = require('http'); const fs = require('fs');
const [db, tag, outp] = [process.argv[2], process.argv[3], process.argv[4]];
const host = db + '.localhost:8069';
const req = (method, path, headers, body) => new Promise((res, rej) => { const r = http.request({ host: '127.0.0.1', port: 8069, method, path, headers: { Host: host, ...headers } }, (m) => { const c = []; m.on('data', d => c.push(d)); m.on('end', () => res({ status: m.statusCode, headers: m.headers, body: Buffer.concat(c) })); }); r.on('error', rej); if (body) r.write(body); r.end(); });
let cookie = '';
const j = async (p, params) => { const b = JSON.stringify({ jsonrpc: '2.0', params }); const r = await req('POST', p, { 'Content-Type': 'application/json', cookie, 'Content-Length': Buffer.byteLength(b) }, b); const sc = r.headers['set-cookie']; if (sc) cookie = sc.map(c => c.split(';')[0]).join('; '); return JSON.parse(r.body.toString()); };
const call = (model, method, args, kwargs) => j('/web/dataset/call_kw/x/y', { model, method, args, kwargs: kwargs || {} });
const P = 'ncollection.account.report.';
const CASES = [
  ['General Ledger', P + 'general.ledger', { date_from: '2026-01-01', date_to: '2026-10-09' }],
  ['Trial Balance', P + 'trial.balance', { date_from: '2026-01-01', date_to: '2026-10-09' }],
  ['Balance Sheet', P + 'balance.sheet', { date_from: '2026-01-01', date_to: '2026-10-09' }],
  ['Profit & Loss', P + 'profit.loss', { date_from: '2026-01-01', date_to: '2026-10-09' }],
  ['Partner Ledger (customers)', P + 'partner.ledger', { date_from: '2026-01-01', date_to: '2026-10-09', partner_scope: 'receivable' }],
  ['Customer statement', P + 'statement', { date_from: '2026-01-01', date_to: '2026-10-09', partner_scope: 'receivable' }],
  ['Vendor statement', P + 'statement', { date_from: '2026-01-01', date_to: '2026-10-09', partner_scope: 'payable' }],
  ['Aged Receivable', P + 'aged.partner', { date_to: '2026-10-09', partner_scope: 'receivable' }],
  ['Aged Payable', P + 'aged.partner', { date_to: '2026-10-09', partner_scope: 'payable' }],
  ['Tax Analysis by tax', P + 'tax.analysis', { date_from: '2026-01-01', date_to: '2026-10-09', group_by: 'tax' }],
  ['Tax Analysis by customer', P + 'tax.analysis', { date_from: '2026-01-01', date_to: '2026-10-09', group_by: 'customer' }],
  ['Cash Flow', P + 'cash.flow', { date_from: '2026-01-01', date_to: '2026-10-09' }],
];
(async () => {
  const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
  const a = await j('/web/session/authenticate', { db, login: 'admin', password: 'admin' });
  if (!(a.result && a.result.uid)) throw new Error('login failed');
  // set the language the way a user does (ORM write: clears the cached user context); a raw SQL update leaves the session stale
  await call('res.users', 'write', [[2], { lang: tag === 'ar' ? 'ar_001' : 'en_US' }]);
  cookie = '';
  const a2 = await j('/web/session/authenticate', { db, login: 'admin', password: 'admin' });
  if (a2.result.user_context.lang !== (tag === 'ar' ? 'ar_001' : 'en_US')) throw new Error('session language not applied: ' + a2.result.user_context.lang);
  const rep = await call('ir.actions.report', 'search_read', [[['model', 'like', P]]], { fields: ['model', 'report_name'] });
  const nameOf = Object.fromEntries(rep.result.map(r => [r.model, r.report_name]));
  const out = [];
  for (const [label, model, filters] of CASES) {
    const row = { label, lang: tag };
    try {
      const t0 = Date.now();
      const run = await call('ncollection.report.service', 'nc_report_run', [model, filters], { context: { lang: tag === 'ar' ? 'ar_001' : 'en_US' } });
      if (run.error) { row.error = JSON.stringify(run.error).slice(0, 200); out.push(row); continue; }
      if (run.result.error) { row.error = run.result.error; out.push(row); continue; }
      const w = run.result.wizard; row.rows = run.result.rows.length; row.title = run.result.title;
      let g = await req('GET', '/report/pdf/' + nameOf[model] + '/' + w.id, { cookie });
      for (let hop = 0; hop < 3 && g.status >= 300 && g.status < 400 && g.headers.location; hop++) g = await req('GET', g.headers.location, { cookie });
      row.ms = Date.now() - t0; row.status = g.status; row.bytes = g.body.length;
      if (g.status !== 200 || g.body.slice(0, 5).toString() !== '%PDF-') { row.error = 'not a pdf: status ' + g.status + ' location ' + g.headers.location + ' ' + g.body.slice(0, 60).toString().replace(/s+/g, ' '); out.push(row); continue; }
      fs.writeFileSync(outp + '_' + label.replace(/[^A-Za-z]+/g, '_') + '_' + tag + '.pdf', g.body);
      const doc = await pdfjs.getDocument({ data: new Uint8Array(g.body), useSystemFonts: true }).promise;
      row.pages = doc.numPages;
      const texts = [];
      for (let p = 1; p <= doc.numPages; p++) {
        const pg = await doc.getPage(p); const vp = pg.getViewport({ scale: 1 });
        if (p === 1) row.size = Math.round(vp.width) + 'x' + Math.round(vp.height);
        const tc = await pg.getTextContent(); texts.push(tc.items.map(i => i.str).join(' '));
      }
      row.arabic = /[0600-06FF]/.test(texts[0].replace(/عميل|مورد|Customer|Vendor/g, '')); row.first = texts[0].replace(/\s+/g, ' ').slice(0, 140);
      row.title_every_page = texts.every(t => t.includes(run.result.title));
      row.company_p1 = texts[0].includes(run.result.company);
      row.pagenum = /\d+\s*\/\s*\d+/.test(texts[texts.length - 1]);
      row.chars = texts.join('').length;
    } catch (e) { row.error = String(e.message || e).slice(0, 200); }
    out.push(row);
  }
  console.log(JSON.stringify(out, null, 0).split('},{').join('},\n{'));
})();
