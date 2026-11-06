// AI chat widget (P5-T06 / #63) browser acceptance, on the PRIVATE stack built by fixtures/run_ai_web.sh.
// The provider is the repo's mock gateway: no external AI call is possible. Every UI claim is followed by an ORM read.
const path = require('path');
const { launch, shot, rpc, BASE, login } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

// Local session helper: the shared one navigates to /odoo, but this product serves the web client under /atlas, so
// we stay on the page the login lands on. The user's language comes from the fixture (res.users.lang).
async function session(browser, { user, password, lang = 'en_US', company = 1, viewport = { width: 1280, height: 800 } }) {
  const context = await browser.newContext({ viewport, locale: lang === 'ar_001' ? 'ar' : 'en-US' });
  await context.addCookies([{ name: 'cids', value: String(company), url: BASE }]);
  const page = await context.newPage();
  page.setDefaultTimeout(40000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 240)));
  await login(page, user, password);
  await page.waitForSelector('.o_main_navbar', { timeout: 60000 });
  const uid = await page.evaluate(async () => {
    const r = await fetch('/web/session/get_session_info', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{"jsonrpc":"2.0","method":"call","params":{}}' });
    return (await r.json()).result.uid;
  });
  return { context, page, errors, uid };
}

const results = [];
let CURRENT = null;
async function check(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, ok: true });
    console.log('PASS', name, detail || '');
  } catch (e) {
    results.push({ name, ok: false });
    console.log('FAIL', name, String(e).slice(0, 500));
    if (CURRENT) await shot(CURRENT, 'ai_fail_' + results.length).catch(() => {});
  }
}
const ok = (v, msg) => { if (!v) throw new Error(msg); };
const eq = (a, b, msg) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };

const USERS = {
  acct: { user: 'ai_acct', password: 'Ai-Acct-Pass1!' },
  acct2: { user: 'ai_acct2', password: 'Ai-Acct2-Pass1!' },
  ar: { user: 'ai_acct_ar', password: 'Ai-AcctAr-Pass1!' },
  plain: { user: 'ai_plain', password: 'Ai-Plain-Pass1!' },
  admin: { user: 'admin', password: 'admin' },
};
const open = (p) => ({ company: 1, ...p });

async function rawCall(page, model, method, args = [], kwargs = {}) {
  try { return { result: await rpc(page, model, method, args, kwargs) }; } catch (e) { return { error: String(e.message || e) }; }
}
async function widgetBox(page) {
  return page.evaluate(() => {
    const r = (s) => { const el = document.querySelector(s); if (!el) return null; const b = el.getBoundingClientRect();
      return { left: Math.round(b.left), right: Math.round(b.right), top: Math.round(b.top), bottom: Math.round(b.bottom), w: Math.round(b.width) }; };
    return { vw: innerWidth, vh: innerHeight, bubble: r('.nc-ai-chat__bubble'), panel: r('.nc-ai-chat__panel'),
      // Odoo applies the RTL stylesheet to <body> (the <html> element carries no dir attribute).
      dir: getComputedStyle(document.body).direction, docOverflow: document.documentElement.scrollWidth > innerWidth + 1 };
  });
}
async function openPanel(page, rtl = false) {
  if (rtl) await page.waitForFunction(() => getComputedStyle(document.body).direction === 'rtl', null, { timeout: 30000 });
  await page.waitForSelector('.nc-ai-chat__bubble', { timeout: 40000 });
  if (!(await page.locator('.nc-ai-chat__panel').count())) await page.click('.nc-ai-chat__bubble');
  await page.waitForSelector('.nc-ai-chat__panel', { timeout: 15000 });
  await page.waitForSelector('.nc-ai-chat__chip', { timeout: 15000 });
}
async function dbCount(page) {
  return rpc(page, 'ncollection.ai.chat.message', 'search_count', [[]]);
}
// A streamed answer shows a PENDING bubble at once; the rows exist only when the stream ends.
async function waitRows(page, n, ms = 45000) {
  const t0 = Date.now();
  for (;;) {
    if ((await dbCount(page)) >= n) return;
    if (Date.now() - t0 > ms) throw new Error('timed out waiting for ' + n + ' saved rows');
    await page.waitForTimeout(250);
  }
}

(async () => {
  const browser = await launch();
  const dialogs = [];
  const consoleErrors = [];
  const wire = (page) => {
    page.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss().catch(() => {}); });
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  };

  // ---- admin: free text OFF is the fresh default; prove it, then keep the handle for later flips
  const adm = await session(browser, { ...USERS.admin, ...open() }); CURRENT = adm.page; wire(adm.page);
  const setFlag = async (v) => rpc(adm.page, 'ir.config_parameter', 'set_param', ['ncollection_ai.enable_free_text_questions', v]);
  await setFlag('');

  await check('plain internal user: the widget does not render and the server says disabled', async () => {
    const s = await session(browser, { ...USERS.plain, ...open() }); wire(s.page);
    await s.page.waitForTimeout(2500);
    eq(await s.page.locator('.nc-ai-chat').count(), 0, 'widget elements');
    const st = await rpc(s.page, 'ncollection.ai.chat', 'get_state', [], {});
    eq(st, { enabled: false }, 'get_state');
    const refused = await rawCall(s.page, 'ncollection.ai.chat', 'send_suggested', ['workspace_summary']);
    ok(refused.error, 'send_suggested must be refused for a non-finance user');
    await s.context.close();
  });

  const A = await session(browser, { ...USERS.acct, ...open() }); wire(A.page); CURRENT = A.page;

  await check('accountant EN desktop: bubble, panel, chips, free-text hint, no input', async () => {
    await openPanel(A.page);
    ok((await A.page.locator('.nc-ai-chat__chip').count()) >= 3, 'chips');
    ok(await A.page.locator('.nc-ai-chat__hint').isVisible(), 'hint visible while free text is off');
    eq(await A.page.locator('.nc-ai-chat__input').count(), 0, 'no typed input while free text is off');
    const b = await widgetBox(A.page);
    ok(b.bubble.left > b.vw / 2, 'LTR: the bubble sits at the inline-end (right) side');
    ok(b.panel.left >= 0 && b.panel.right <= b.vw && b.panel.top >= 0, 'panel inside the viewport');
    await shot(A.page, 'ai_en_desktop_open');
  });

  await check('free text OFF: a raw send_typed is refused by the server and stores nothing', async () => {
    const before = await dbCount(A.page);
    const r = await rawCall(A.page, 'ncollection.ai.chat', 'send_typed', ['What is our revenue?']);
    ok(r.error, 'send_typed must be refused');
    eq(await dbCount(A.page), before, 'no transcript rows');
  });

  await check('click a suggestion: typing indicator, assistant answer, persisted in order', async () => {
    const before = await dbCount(A.page);
    await A.page.locator('.nc-ai-chat__chip').first().click();
    await waitRows(A.page, before + 2);
    await A.page.waitForFunction(() => !document.querySelector('.nc-ai-chat__typing'), null, { timeout: 15000 });
    eq(await A.page.locator('.nc-ai-chat__msg--user').count(), 1, 'one user message');
    ok((await A.page.locator('.nc-ai-chat__msg--assistant').first().innerText()).trim().length > 0, 'non-empty answer');
    const rows = await rpc(A.page, 'ncollection.ai.chat.message', 'search_read', [[]], { fields: ['role', 'body'], order: 'id' });
    eq(rows.map((r) => r.role), ['user', 'assistant'], 'roles in order');
    eq(await dbCount(A.page), before + 2, 'two rows written');
    ok(A.errors.length === 0, 'page errors: ' + A.errors.join(' | '));
  });

  await check('reload: the panel state and the transcript are restored from the server', async () => {
    await A.page.reload();
    await A.page.waitForSelector('.nc-ai-chat__panel', { timeout: 40000 });
    await A.page.waitForSelector('.nc-ai-chat__msg--assistant', { timeout: 20000 });
    eq(await A.page.locator('.nc-ai-chat__msg').count(), 2, 'messages after reload');
  });

  await check('renderer XSS: hostile model output is escaped, only the tiny markdown subset becomes markup', async () => {
    const out = await A.page.evaluate(() => {
      const mod = odoo.loader.modules.get('@ncollection_ai/chat/chat_widget');
      if (!mod) return { missing: true };
      const hostile = [
        '<script>window.__x=1</script>', '<img src=x onerror="window.__x=2">', '[a](javascript:alert(1))',
        '**<b onmouseover=alert(1)>bold</b>**', '- <iframe src=//evil></iframe>', '`</code><svg onload=alert(1)>`',
        '"><img src=x onerror=alert(1)>', '&lt;script&gt;', 'plain **bold** and `code`\n- item one\n- item two'];
      // Parse every rendering into an inert <template> and report what it would CREATE: the words "onerror=" may
      // appear as escaped text, but no element other than the five allowed tags, and no attribute at all, may exist.
      return hostile.map((h) => {
        const html = String(mod.renderMarkdownSubset(h));
        const tpl = document.createElement('template');
        tpl.innerHTML = html;
        const els = [...tpl.content.querySelectorAll('*')];
        return { html, tags: els.map((e) => e.tagName), attrs: els.reduce((n, e) => n + e.attributes.length, 0) };
      });
    });
    ok(!out.missing, 'widget module not found in the loader');
    const allowed = new Set(['P', 'UL', 'LI', 'STRONG', 'CODE']);
    out.forEach((o, i) => {
      ok(o.tags.every((t) => allowed.has(t)), `case ${i}: unexpected element(s) ${o.tags.join(',')} in ${o.html}`);
      eq(o.attrs, 0, `case ${i}: attributes created`);
    });
    const last = out[out.length - 1].html;
    ok(last.includes('<strong>bold</strong>') && last.includes('<code>code</code>') && last.includes('<ul><li>'),
      'the intended markdown subset still works');
    eq(await A.page.evaluate(() => window.__x), undefined, 'no injected script ran');
  });

  await check('EN mobile 390px: panel fits, no horizontal page overflow, chips usable', async () => {
    const M = await session(browser, { ...USERS.acct, ...open(), viewport: { width: 390, height: 800 } }); wire(M.page); CURRENT = M.page;
    await openPanel(M.page);
    const b = await widgetBox(M.page);
    ok(b.panel.left >= 0 && b.panel.right <= b.vw, `panel within viewport (${b.panel.left}..${b.panel.right} of ${b.vw})`);
    ok(!b.docOverflow, 'no horizontal page overflow');
    ok(b.panel.top >= 0 && b.panel.bottom <= b.vh, 'panel within the viewport height');
    await shot(M.page, 'ai_en_mobile_open');
    await M.context.close();
  });

  // ---- Arabic
  const R = await session(browser, { ...USERS.ar, ...open(), lang: 'ar_001' }); wire(R.page); CURRENT = R.page;
  await check('Arabic desktop: RTL, chips and texts are Arabic, bubble at the inline-end (left)', async () => {
    await openPanel(R.page, true);
    const b = await widgetBox(R.page);
    eq(b.dir, 'rtl', 'document direction');
    ok(b.bubble.right < b.vw / 2 + 40, 'RTL: the bubble sits at the inline-end (left) side');
    ok(b.panel.left >= 0 && b.panel.right <= b.vw, 'panel inside the viewport');
    const arabic = /[؀-ۿ]/;
    const chips = await R.page.locator('.nc-ai-chat__chip').allInnerTexts();
    ok(chips.length >= 3 && chips.every((t) => arabic.test(t)), 'every chip Arabic: ' + JSON.stringify(chips));
    ok(arabic.test(await R.page.locator('.nc-ai-chat__hint').innerText()), 'hint Arabic');
    ok(arabic.test(await R.page.locator('.nc-ai-chat__title').innerText()), 'title Arabic');
    ok(arabic.test((await R.page.getAttribute('.nc-ai-chat__bubble', 'title')) || ''), 'bubble title Arabic');
    await shot(R.page, 'ai_ar_desktop_open');
  });
  await check('Arabic mobile 390px: RTL panel fits the screen', async () => {
    const M = await session(browser, { ...USERS.ar, ...open(), lang: 'ar_001', viewport: { width: 390, height: 800 } }); wire(M.page); CURRENT = M.page;
    await openPanel(M.page, true);
    const b = await widgetBox(M.page);
    ok(b.dir === 'rtl' &&b.panel.left >= 0 && b.panel.right <= b.vw && !b.docOverflow, JSON.stringify(b));
    await shot(M.page, 'ai_ar_mobile_open');
    await M.context.close();
  });

  // ---- isolation between users
  await check('isolation: a second accountant sees none of the first one\'s transcript and cannot forge into it', async () => {
    const B = await session(browser, { ...USERS.acct2, ...open() }); wire(B.page);
    eq(await dbCount(B.page), 0, 'second accountant sees zero rows');
    const mine = await rpc(A.page, 'ncollection.ai.chat.message', 'search_read', [[]], { fields: ['user_id'] });
    ok(mine.length === 2, 'first accountant still has 2 rows');
    const forged = await rawCall(B.page, 'ncollection.ai.chat.message', 'create', [{ user_id: A.uid, role: 'assistant', body: 'forged' }]);
    ok(forged.error, 'creating a row for another user must be refused');
    const own = await rawCall(B.page, 'ncollection.ai.chat.message', 'search_read', [[['user_id', '=', A.uid]]], { fields: ['body'] });
    eq(own.result, [], 'explicit domain on another user returns nothing');
    eq(await dbCount(A.page), 2, 'first accountant transcript untouched');
    await B.context.close();
  });

  // ---- free text ON
  await check('free text ON: the input appears; typed hostile text is shown as text, never executed', async () => {
    await setFlag('1');
    await A.page.reload();
    await A.page.waitForSelector('.nc-ai-chat__input', { timeout: 40000 });
    eq(await A.page.locator('.nc-ai-chat__hint').count(), 0, 'hint gone');
    const hostile = '<img src=x onerror="window.__y=1"> **hello**';
    await A.page.fill('.nc-ai-chat__input', hostile);
    await A.page.click('.nc-ai-chat__sendbtn');
    await waitRows(A.page, 4);
    await A.page.waitForFunction(() => !document.querySelector('.nc-ai-chat__typing'), null, { timeout: 15000 });
    const lastUser = (await A.page.locator('.nc-ai-chat__msg--user').last().innerText()).trim();
    eq(lastUser, hostile, 'user message shown verbatim as text');
    eq(await A.page.locator('.nc-ai-chat__messages img').count(), 0, 'no <img> element created');
    eq(await A.page.evaluate(() => window.__y), undefined, 'no injected handler ran');
    eq(dialogs.length, 0, 'no alert/confirm/prompt dialog');
    eq(await dbCount(A.page), 4, 'four rows now');
    await shot(A.page, 'ai_en_free_text');
  });
  await check('free text ON: a question that looks like a secret is refused, the text is kept, nothing stored', async () => {
    const before = await dbCount(A.page);
    const text = 'my password is Hunter2Hunter2Hunter2';
    await A.page.fill('.nc-ai-chat__input', text);
    await A.page.click('.nc-ai-chat__sendbtn');
    await A.page.waitForSelector('.nc-ai-chat__error', { timeout: 20000 });
    eq(await A.page.inputValue('.nc-ai-chat__input'), text, 'typed text restored for editing');
    eq(await dbCount(A.page), before, 'nothing stored on a refusal');
  });
  await setFlag('');

  await check('no JavaScript errors, no console errors, no dialogs across the whole run', async () => {
    const pageErrors = [].concat(A.errors, R.errors, adm.errors);
    eq(pageErrors, [], 'page errors');
    const benign = consoleErrors.filter((t) => !/favicon|Failed to load resource.*(40[0-4])|net::ERR_ABORTED/i.test(t));
    eq(benign, [], 'console errors');
    eq(dialogs, [], 'dialogs');
  });

  const passed = results.filter((r) => r.ok).length;
  console.log(`\nAI CHAT BROWSER ACCEPTANCE: ${passed}/${results.length} passed`);
  await browser.close();
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error('SUITE CRASH', e); process.exit(2); });
