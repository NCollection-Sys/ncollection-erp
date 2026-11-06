// Evidence for the streaming review: how long does an ordinary request wait while streams hold the web workers?
// Run against a stack started with --workers=2 and a slow mock gateway (see e2e/rehearsal/README.md). Measures, never asserts.
const path = require('path');
const { launch, session, BASE } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));
(async () => {
  const browser = await launch();
  const mk = async (u, p) => session(browser, { user: u, password: p, lang: 'en_US', company: Number(process.env.BANK_COMPANY || 2) });
  const a = await mk('acct_bank', 'Acct-Bank-Pass1!'); const c = await mk('ceo_bank', 'Ceo-Bank-Pass1!'); const o = await mk('owner_bank', 'Owner-Bank-Pass1!');
  const stream = (page) => page.evaluate(async () => {
    const t0 = performance.now();
    const r = await fetch('/ncollection/ai/chat/stream', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ kind: 'suggested', value: 'workspace_summary', csrf_token: odoo.csrf_token }) });
    await r.text(); return { status: r.status, ms: Math.round(performance.now() - t0) };
  });
  const quick = (page) => page.evaluate(async () => { const t0 = performance.now(); const r = await fetch('/web/webclient/version_info', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{"jsonrpc":"2.0","method":"call","params":{}}' }); await r.text(); return { status: r.status, ms: Math.round(performance.now() - t0) }; });
  console.log('idle quick request      :', JSON.stringify(await quick(o.page)));
  const one = stream(a.page); await o.page.waitForTimeout(1200);
  console.log('quick, 1 stream running  :', JSON.stringify(await quick(o.page)));
  const [r1] = await Promise.all([one]);
  const s1 = stream(a.page), s2 = stream(c.page); await o.page.waitForTimeout(1500);
  console.log('quick, 2 streams running :', JSON.stringify(await quick(o.page)), '(workers=2)');
  console.log('stream durations         :', JSON.stringify(await Promise.all([s1, s2])), 'single:', JSON.stringify(r1));
  await browser.close();
})().catch((e) => { console.error('CRASH', e); process.exit(2); });
