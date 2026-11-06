// AI chat widget (P5-T06 / #63): STREAMING, forgery, size, language and failure behaviour in a real browser.
//   (same private stack as acceptance_contexts.js; the mock gateway is slowed to 150 ms per chunk by run_ai_web.sh)
//   node acceptance_stream.js     (from e2e/)
const path = require('path');
const { execSync } = require('child_process');
const { launch, shot, rpc, BASE, login } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

const USERS = {
  acct: { user: 'ai_acct', password: 'Ai-Acct-Pass1!' },
  ar: { user: 'ai_acct_ar', password: 'Ai-AcctAr-Pass1!' },
  plain: { user: 'ai_plain', password: 'Ai-Plain-Pass1!' },
  admin: { user: 'admin', password: 'admin' },
};
const results = [];
let CURRENT = null;
const ok = (v, m) => { if (!v) throw new Error(m); };
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
async function check(name, fn) {
  try { const d = await fn(); results.push({ name, ok: true }); console.log('PASS', name, d || ''); }
  catch (e) { results.push({ name, ok: false }); console.log('FAIL', name, String(e).slice(0, 600)); if (CURRENT) await shot(CURRENT, 'stream_fail_' + results.length).catch(() => {}); }
}
async function session(browser, { user, password, lang = 'en_US', viewport = { width: 1280, height: 800 } }) {
  const context = await browser.newContext({ viewport, locale: lang === 'ar_001' ? 'ar' : 'en-US' });
  await context.addCookies([{ name: 'cids', value: '1', url: BASE }]);
  const page = await context.newPage();
  page.setDefaultTimeout(40000);
  const s = { context, page, errors: [], dialogs: [], streams: [] };
  page.on('pageerror', (e) => s.errors.push(String(e).slice(0, 240)));
  page.on('dialog', (d) => { s.dialogs.push(d.message()); d.dismiss().catch(() => {}); });
  page.on('response', (r) => { if (r.url().includes('/ncollection/ai/chat/stream')) s.streams.push({ status: r.status(), type: r.headers()['content-type'], buffering: r.headers()['x-accel-buffering'] }); });
  await login(page, user, password);
  await page.waitForSelector('.o_main_navbar', { timeout: 60000 });
  return s;
}
async function openPanel(page, rtl = false) {
  if (rtl) await page.waitForFunction(() => getComputedStyle(document.body).direction === 'rtl', null, { timeout: 30000 });
  await page.waitForSelector('.nc-ai-chat__bubble', { timeout: 40000 });
  if (!(await page.locator('.nc-ai-chat__panel').count())) await page.click('.nc-ai-chat__bubble');
  await page.waitForSelector('.nc-ai-chat__chip', { timeout: 15000 });
}
const count = (page) => rpc(page, 'ncollection.ai.chat.message', 'search_count', [[]]);
async function waitRows(page, n, ms = 45000) {
  const t0 = Date.now();
  while ((await count(page)) < n) { if (Date.now() - t0 > ms) throw new Error('timed out waiting for ' + n + ' rows'); await page.waitForTimeout(200); }
}
const lastRows = (page, n) => rpc(page, 'ncollection.ai.chat.message', 'search_read', [[]], { fields: ['role', 'body'], order: 'id desc', limit: n });

(async () => {
  const browser = await launch();
  const all = [];
  const S = async (u, extra = {}) => { const s = await session(browser, { ...u, ...extra }); all.push(s); CURRENT = s.page; return s; };
  const adm = await S(USERS.admin);
  const setFlag = (v) => rpc(adm.page, 'ir.config_parameter', 'set_param', ['ncollection_ai.enable_free_text_questions', v]);
  await setFlag('');
  const A = await S(USERS.acct);
  await openPanel(A.page);

  await check('streaming: the answer GROWS in the browser before it is saved, then the saved rows replace it', async () => {
    const before = await count(A.page);
    const bubblesBefore = await A.page.locator('.nc-ai-chat__msg').count();
    const botsBefore = await A.page.locator('.nc-ai-chat__msg--assistant').count();
    await A.page.locator('.nc-ai-chat__chip').first().click();
    const samples = [];
    let sawPendingBeforeRows = false;
    for (let t = 0; t < 120; t++) {
      const info = await A.page.evaluate((n0) => {
        const b = [...document.querySelectorAll('.nc-ai-chat__msg--assistant')][n0];
        return { len: b ? b.innerText.trim().length : -1, typing: !!document.querySelector('.nc-ai-chat__typing') };
      }, botsBefore);
      samples.push(info.len);
      if ((await count(A.page)) === before) { if (info.len > 0) sawPendingBeforeRows = true; } else break;
      await A.page.waitForTimeout(60);
    }
    const distinct = [...new Set(samples.filter((n) => n > 0))];
    ok(sawPendingBeforeRows, 'no visible answer text before the rows were saved');
    ok(distinct.length >= 3, 'the visible answer did not grow in steps: ' + JSON.stringify(distinct));
    ok(distinct.every((v, i) => i === 0 || v >= distinct[i - 1]), 'answer shrank while streaming: ' + JSON.stringify(samples));
    await waitRows(A.page, before + 2);
    await A.page.waitForFunction(() => !document.querySelector('.nc-ai-chat__typing'), null, { timeout: 15000 });
    eq(await A.page.locator('.nc-ai-chat__msg').count(), bubblesBefore + 2, 'pending bubbles replaced, not duplicated');
    const [assistant, user] = await lastRows(A.page, 2);
    eq([assistant.role, user.role], ['assistant', 'user'], 'saved roles');
    const shown = (await A.page.locator('.nc-ai-chat__msg--assistant').last().innerText()).replace(/\s+/g, ' ').trim();
    eq(shown, assistant.body.replace(/\s+/g, ' ').trim(), 'what was shown equals what was saved');
    const s = A.streams[A.streams.length - 1];
    eq([s.status, s.type.split(';')[0], s.buffering], [200, 'application/x-ndjson', 'no'], 'stream response');
    await shot(A.page, 'stream_en_done');
  });

  await check('forgery: a gated user cannot create ANY transcript row through the ORM, own user included', async () => {
    const before = await count(A.page);
    for (const role of ['assistant', 'user']) {
      let refused = false;
      try { await rpc(A.page, 'ncollection.ai.chat.message', 'create', [{ role, body: 'forged ' + role }]); } catch (e) { refused = true; }
      ok(refused, `create(${role}) must be refused`);
    }
    eq(await count(A.page), before, 'no rows planted');
  });

  await check('size: a 6000-character typed question is cut to the 2000-character cap before it is sent or stored', async () => {
    await setFlag('1');
    await A.page.reload(); await openPanel(A.page);
    await A.page.waitForSelector('.nc-ai-chat__input');
    const before = await count(A.page);
    await A.page.fill('.nc-ai-chat__input', 'long question '.repeat(430));
    await A.page.click('.nc-ai-chat__sendbtn');
    await waitRows(A.page, before + 2);
    const [, user] = await lastRows(A.page, 2);
    ok(user.body.length <= 2000 && user.body.length > 1500, 'stored question length ' + user.body.length);
    await setFlag('');
  });

  await check('permissions: the stream endpoint refuses a plain user (403) and saves nothing', async () => {
    const P = await S(USERS.plain);
    const r = await P.page.evaluate(async () => {
      const res = await fetch('/ncollection/ai/chat/stream', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ kind: 'suggested', value: 'workspace_summary', csrf_token: odoo.csrf_token }) });
      return { status: res.status, body: await res.text() };
    });
    eq(r.status, 403, 'plain user status');
    const noToken = await P.page.evaluate(async () => (await fetch('/ncollection/ai/chat/stream', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ kind: 'suggested', value: 'workspace_summary' }) })).status);
    ok(noToken >= 400, 'a POST without the CSRF token must be refused, got ' + noToken);
    await P.context.close();
  });

  await check('permissions: an accountant POST without the CSRF token is refused too', async () => {
    const st = await A.page.evaluate(async () => (await fetch('/ncollection/ai/chat/stream', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ kind: 'suggested', value: 'workspace_summary' }) })).status);
    ok(st >= 400, 'got ' + st);
  });

  await check('Arabic: the transcript shows the Arabic label (not the English registry question) and the stream renders RTL', async () => {
    const R = await S(USERS.ar, { lang: 'ar_001' });
    await openPanel(R.page, true);
    const before = await count(R.page);
    const label = (await R.page.locator('.nc-ai-chat__chip').first().innerText()).trim();
    await R.page.locator('.nc-ai-chat__chip').first().click();
    await waitRows(R.page, before + 2);
    await R.page.waitForFunction(() => !document.querySelector('.nc-ai-chat__typing'), null, { timeout: 15000 });
    const [, userRow] = await lastRows(R.page, 2);
    eq(userRow.body, label, 'stored user text is the chip label');
    ok(/[؀-ۿ]/.test(userRow.body), 'Arabic');
    ok(!/[A-Za-z]{4,}/.test(await R.page.locator('.nc-ai-chat__msg--user').last().innerText()), 'no English question in the Arabic bubble');
    eq(await R.page.evaluate(() => getComputedStyle(document.body).direction), 'rtl', 'direction');
    await shot(R.page, 'stream_ar_done');
  });

  await check('fallback: if the stream route is unavailable (404) the widget answers through the batch RPC', async () => {
    const F = await S(USERS.acct);
    await openPanel(F.page);
    const before = await count(F.page);
    await F.page.route('**/ncollection/ai/chat/stream', (route) => route.fulfill({ status: 404, body: 'nope' }));
    await F.page.locator('.nc-ai-chat__chip').first().click();
    await waitRows(F.page, before + 2);
    const [assistant] = await lastRows(F.page, 2);
    ok(assistant.body.includes('[mock completion]'), 'batch answer: ' + assistant.body);
    eq(await F.page.locator('.nc-ai-chat__error').count(), 0, 'no error shown');
  });

  await check('failure: gateway stopped -> a clear error, no half answer, nothing saved, widget usable again afterwards', async () => {
    await openPanel(A.page);
    const before = await count(A.page);
    const msgsBefore = await A.page.locator('.nc-ai-chat__msg').count();
    execSync('docker stop ncai_gateway', { stdio: 'ignore' });
    try {
      await A.page.locator('.nc-ai-chat__chip').first().click();
      await A.page.waitForSelector('.nc-ai-chat__error', { timeout: 30000 });
      eq(await count(A.page), before, 'nothing saved');
      eq(await A.page.locator('.nc-ai-chat__msg').count(), msgsBefore, 'no pending bubbles left behind');
      ok((await A.page.locator('.nc-ai-chat__error').innerText()).trim().length > 5, 'error text');
      await shot(A.page, 'stream_gateway_down');
    } finally {
      execSync('docker start ncai_gateway', { stdio: 'ignore' });
    }
    for (let t = 0; t < 30; t++) { try { execSync('docker exec ncai_gateway python3 -c "import urllib.request;urllib.request.urlopen(\'http://127.0.0.1:8080/healthz\')"', { stdio: 'ignore' }); break; } catch (e) { await A.page.waitForTimeout(1000); } }
    await A.page.locator('.nc-ai-chat__chip').first().click();
    await waitRows(A.page, before + 2);
  });

  await check('no page errors and no dialogs across the run', async () => {
    for (const s of all) { eq(s.errors, [], 'page errors'); eq(s.dialogs, [], 'dialogs'); }
  });

  const passed = results.filter((r) => r.ok).length;
  console.log(`\nAI CHAT STREAMING ACCEPTANCE: ${passed}/${results.length} passed`);
  await browser.close();
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error('SUITE CRASH', e); process.exit(2); });
