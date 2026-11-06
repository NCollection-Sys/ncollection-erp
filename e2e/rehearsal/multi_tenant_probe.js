// Evidence for the streaming safeguard: ordinary-request latency while SEVERAL TENANTS stream at once.
// Needs ONE server holding all the databases (db-filter), started with a small --workers=N and a slow mock gateway
// (e2e/rehearsal/README.md). Asserts the safeguard, measures the latency.
//   node multi_tenant_probe.js <workers> db1,db2,db3
// Expectations (global cap = min(workers // 2, workers - 1), at least 1):
//   * at most `cap` streams are admitted at the same time; the rest are refused promptly (HTTP 400, "busy"),
//   * an ordinary request on EVERY tenant stays fast while the admitted streams run,
//   * once the streams finish, a new stream is admitted again (no leaked slot).
const path = require('path');
const { launch, BASE } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

const WORKERS = Number(process.argv[2] || 3);
const DBS = (process.argv[3] || 'ncollection,awtar,ncollection_demo').split(',');
const CAP = Math.min(Math.max(1, Math.floor(WORKERS / 2)), Math.max(1, WORKERS - 1));
const results = [];
const check = (name, ok, detail = '') => { results.push(ok); console.log(ok ? 'PASS' : 'FAIL', name, detail); };

async function open(browser, db, user, password) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  await page.goto(`${BASE}/web/login?db=${db}`);
  await page.fill('input[name=login]', user);
  await page.fill('input[name=password]', password);
  await Promise.all([page.waitForURL(/\/(odoo|atlas|web)(\/|$|\?)/, { timeout: 60000 }), page.click('button[type=submit]')]);
  await page.goto(BASE + '/odoo');
  await page.waitForSelector('.o_main_navbar', { timeout: 60000 });
  return page;
}
const stream = (page) => page.evaluate(async () => {
  const t0 = performance.now();
  const r = await fetch('/ncollection/ai/chat/stream', { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ kind: 'suggested', value: 'workspace_summary', csrf_token: odoo.csrf_token }) });
  const text = await r.text();
  return { status: r.status, ms: Math.round(performance.now() - t0), busy: /busy/i.test(text) };
});
const quick = (page) => page.evaluate(async () => {
  const t0 = performance.now();
  const r = await fetch('/web/webclient/version_info', { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: '{"jsonrpc":"2.0","method":"call","params":{}}' });
  await r.text();
  return { status: r.status, ms: Math.round(performance.now() - t0) };
});

(async () => {
  const browser = await launch();
  const askers = {}; const others = {};
  for (const db of DBS) {
    askers[db] = await open(browser, db, 'acct_bank', 'Acct-Bank-Pass1!');
    others[db] = await open(browser, db, 'owner_bank', 'Owner-Bank-Pass1!');
  }
  console.log(`workers=${WORKERS} -> server-wide cap ${CAP}; tenants: ${DBS.join(', ')}`);
  const idle = {};
  for (const db of DBS) idle[db] = (await quick(others[db])).ms;
  console.log('idle ordinary request ms:', JSON.stringify(idle));

  // every tenant starts a stream at the same moment
  const running = DBS.map((db) => stream(askers[db]));
  await others[DBS[0]].waitForTimeout(1500);
  const during = {};
  for (const db of DBS) during[db] = await quick(others[db]);
  console.log('ordinary request while streams run:', JSON.stringify(during));
  const outcome = await Promise.all(running);
  console.log('stream outcomes:', JSON.stringify(Object.fromEntries(DBS.map((d, i) => [d, outcome[i]]))));

  const admitted = outcome.filter((o) => o.status === 200).length;
  const refused = outcome.filter((o) => o.status === 400 && o.busy);
  check(`at most ${CAP} stream(s) admitted across ${DBS.length} tenants`, admitted >= 1 && admitted <= CAP, `admitted=${admitted}`);
  check('every other stream is refused with the friendly "busy" message', refused.length === DBS.length - admitted, `refused=${refused.length}`);
  check('a refused stream is answered promptly (< 1500 ms, bounded wait)', refused.every((o) => o.ms < 1500), refused.map((o) => o.ms).join(','));
  const worst = Math.max(...DBS.map((d) => during[d].ms));
  check('ordinary requests on every tenant stay fast while streams run (< 1000 ms)', DBS.every((d) => during[d].status === 200) && worst < 1000, `worst=${worst} ms`);

  // nothing leaked: after everything finished a fresh stream is admitted again
  const again = await stream(askers[DBS[DBS.length - 1]]);
  check('after the streams finish a new stream is admitted again (no leaked slot)', again.status === 200, JSON.stringify(again));
  await browser.close();
  const ok = results.filter(Boolean).length;
  console.log(`\nMULTI-TENANT PROBE: ${ok}/${results.length} passed`);
  process.exit(ok === results.length ? 0 : 1);
})().catch((e) => { console.error('CRASH', e); process.exit(2); });
