// XLSX quality gate: downloads each workbook from /ncollection/account_reports/xlsx and unzips it: numeric cells must be
// numeric (0 stringified numbers), header frozen, column widths set, landscape fit-to-width, right-to-left in Arabic.
// usage: node xlsx_quality.js <db> <en|ar>      needs: npm i jszip@3.10.1   (manual; not in CI)
const http = require('http'); const fs = require('fs'); const JSZip = require('jszip');
const [db, tag] = [process.argv[2], process.argv[3]]; const host = db + '.localhost:8069';
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
  ['Partner Ledger', P + 'partner.ledger', { date_from: '2026-01-01', date_to: '2026-10-09', partner_scope: 'receivable' }],
  ['Customer statement', P + 'statement', { date_from: '2026-01-01', date_to: '2026-10-09', partner_scope: 'receivable' }],
  ['Aged Receivable', P + 'aged.partner', { date_to: '2026-10-09', partner_scope: 'receivable' }],
  ['Aged Payable', P + 'aged.partner', { date_to: '2026-10-09', partner_scope: 'payable' }],
  ['Tax Analysis (tax)', P + 'tax.analysis', { date_from: '2026-01-01', date_to: '2026-10-09', group_by: 'tax' }],
  ['Tax Analysis (customer)', P + 'tax.analysis', { date_from: '2026-01-01', date_to: '2026-10-09', group_by: 'customer' }],
];
(async () => {
  cookie = ''; await j('/web/session/authenticate', { db, login: 'admin', password: 'admin' });
  await call('res.users', 'write', [[2], { lang: tag === 'ar' ? 'ar_001' : 'en_US' }]); cookie = '';
  await j('/web/session/authenticate', { db, login: 'admin', password: 'admin' });
  const out = [];
  for (const [label, model, filters] of CASES) {
    const run = await call('ncollection.report.service', 'nc_report_run', [model, filters], { context: { lang: tag === 'ar' ? 'ar_001' : 'en_US' } });
    const w = run.result.wizard; const cols = run.result.columns; const rows = run.result.rows;
    let g = await req('GET', '/ncollection/account_reports/xlsx/' + w.model + '/' + w.id, { cookie });
    for (let h = 0; h < 3 && g.status >= 300 && g.status < 400; h++) g = await req('GET', g.headers.location, { cookie });
    const row = { label, status: g.status, bytes: g.body.length };
    if (g.status !== 200) { row.error = g.body.slice(0, 80).toString(); out.push(row); continue; }
    fs.writeFileSync(require('os').tmpdir() + '/nc_x_' + label.replace(/[^A-Za-z]+/g, '_') + '_' + tag + '.xlsx', g.body);
    const z = await JSZip.loadAsync(g.body); const names = Object.keys(z.files);
    const sheet = await z.file('xl/worksheets/sheet1.xml').async('string');
    const ss = z.file('xl/sharedStrings.xml') ? await z.file('xl/sharedStrings.xml').async('string') : '';
    const styles = await z.file('xl/styles.xml').async('string');
    const cells = [...sheet.matchAll(/<c r="([A-Z]+)(\d+)"([^>]*?)(?:\/>|>(.*?)<\/c>)/g)];
    const numeric = cells.filter(c => !/t="(s|str|inlineStr)"/.test(c[3]) && /<v>/.test(c[4] || '')).length;
    const strings = cells.filter(c => /t="(s|str|inlineStr)"/.test(c[3])).length;
    // a numeric-looking STRING in a numeric column would be a stringified number
    const strNumbers = [...ss.matchAll(/<t[^>]*>([^<]*)<\/t>/g)].filter(m => /^-?[\d,]+\.\d\d$/.test(m[1].trim())).length;
    row.cells = cells.length; row.numeric = numeric; row.strings = strings; row.stringified_numbers = strNumbers;
    row.frozen = /<pane [^>]*state="frozen"/.test(sheet); row.rtl = /rightToLeft="1"/.test(sheet);
    row.widths = (sheet.match(/<col /g) || []).length; row.landscape = /orientation="landscape"/.test(sheet); row.fitwidth = /fitToWidth|fitToPage/.test(sheet);
    row.dateStyle = /numFmt[^>]*(yy|dd|mm)/i.test(styles); row.expectedRows = rows.length; row.cols = cols.length;
    row.title_row = (ss.match(/<t[^>]*>([^<]*)<\/t>/) || [])[1];
    out.push(row);
  }
  for (const r of out) console.log((r.label + ' [' + tag + ']').padEnd(30), r.error ? 'ERROR ' + r.error : ['rows ' + r.expectedRows, 'numeric ' + r.numeric, 'strings ' + r.strings, 'stringified# ' + r.stringified_numbers, 'frozen ' + r.frozen, 'rtl ' + r.rtl, 'colwidths ' + r.widths, 'landscape ' + r.landscape, 'fit ' + r.fitwidth, (r.bytes / 1024).toFixed(0) + 'KB'].join(' | '));
})();
