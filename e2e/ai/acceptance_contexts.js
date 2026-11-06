// AI chat widget (P5-T06 / #63): five-module-context browser acceptance on the PRIVATE stack.
//   AI_INSTALL=ncollection_ai,sale,crm,account,stock AI_SEED=ai_context_fixture.py bash fixtures/run_ai_web.sh up
//   node acceptance_contexts.js          (from e2e/)       then:  bash fixtures/run_ai_web.sh clean
// Real navigation to a real record of each model; the module context the browser SENDS is read off the wire, the chips
// are compared with what the server's registry says for that context, and every answer is followed by an ORM read.
const path = require('path');
const { execSync } = require('child_process');
const { launch, shot, rpc, BASE, login } = require(path.join(__dirname, '..', 'dashboards', 'lib.js'));

const CONTEXTS = ['default', 'sale.order', 'crm.lead', 'account.move', 'stock.picking'];
const USERS = {
  acct: { user: 'ai_acct', password: 'Ai-Acct-Pass1!' },
  owner: { user: 'ai_owner', password: 'Ai-Owner-Pass1!' },
  ar: { user: 'ai_acct_ar', password: 'Ai-AcctAr-Pass1!' },
  plain: { user: 'ai_plain', password: 'Ai-Plain-Pass1!' },
};
const results = [];
let CURRENT = null;
const ok = (v, m) => { if (!v) throw new Error(m); };
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
async function check(name, fn) {
  try { const d = await fn(); results.push({ name, ok: true }); console.log('PASS', name, d || ''); }
  catch (e) { results.push({ name, ok: false }); console.log('FAIL', name, String(e).slice(0, 600)); if (CURRENT) await shot(CURRENT, 'ctx_fail_' + results.length).catch(() => {}); }
}

async function session(browser, { user, password, lang = 'en_US', viewport = { width: 1280, height: 800 } }) {
  const context = await browser.newContext({ viewport, locale: lang === 'ar_001' ? 'ar' : 'en-US' });
  await context.addCookies([{ name: 'cids', value: '1', url: BASE }]);
  const page = await context.newPage();
  page.setDefaultTimeout(40000);
  const s = { context, page, errors: [], ctxSent: [], dialogs: [] };
  page.on('pageerror', (e) => s.errors.push(String(e).slice(0, 240)));
  page.on('dialog', (d) => { s.dialogs.push(d.message()); d.dismiss().catch(() => {}); });
  // The module context the BROWSER sends with each get_state call.
  page.on('request', (r) => {
    if (r.method() !== 'POST' || !r.url().includes('/ncollection.ai.chat/get_state')) return;
    try { s.ctxSent.push(JSON.parse(r.postData()).params.kwargs.module_ctx); } catch (e) { /* not ours */ }
  });
  await login(page, user, password);
  await page.waitForSelector('.o_main_navbar', { timeout: 60000 });
  return s;
}

// Ids of the seeded records are read through the ORM, never assumed.
async function recordId(page, model) {
  const r = await rpc(page, model, 'search', [[]], { order: 'id', limit: 1 });
  return r[0];
}
async function openRecord(page, model, id) {
  await page.goto(`${BASE}/atlas/${model}/${id}`);
  await page.waitForSelector('.o_form_view, .o_error_dialog, .o_notification', { timeout: 45000 });
}
async function openPanel(page, rtl = false) {
  if (rtl) await page.waitForFunction(() => getComputedStyle(document.body).direction === 'rtl', null, { timeout: 30000 });
  await page.waitForSelector('.nc-ai-chat__bubble', { timeout: 40000 });
  if (!(await page.locator('.nc-ai-chat__panel').count())) await page.click('.nc-ai-chat__bubble');
  await page.waitForSelector('.nc-ai-chat__chip', { timeout: 15000 });
}
async function serverChips(page, ctx) {
  const st = await rpc(page, 'ncollection.ai.chat', 'get_state', [], { module_ctx: ctx, include_history: false });
  return st.suggestions.map((s) => s.label);
}
const chipTexts = (page) => page.locator('.nc-ai-chat__chip').allInnerTexts().then((a) => a.map((t) => t.trim()));
const count = (page) => rpc(page, 'ncollection.ai.chat.message', 'search_count', [[]]);

(async () => {
  const browser = await launch();
  const sessions = [];
  const S = async (u, extra = {}) => { const s = await session(browser, { ...u, ...extra }); sessions.push(s); CURRENT = s.page; return s; };

  // Which user can open which record is part of the permission story, so it is measured, not assumed.
  const owner = await S(USERS.owner);
  const ids = {};
  for (const m of CONTEXTS.slice(1)) ids[m] = await recordId(owner.page, m);
  console.log('records', JSON.stringify(ids));

  await check('registry: five contexts, four of them distinct from the default chip set', async () => {
    const base = (await serverChips(owner.page, 'default')).join('|');
    for (const m of CONTEXTS.slice(1)) {
      const chips = await serverChips(owner.page, m);
      ok(chips.length >= 3, `${m}: ${chips.length} chips`);
      ok(chips.join('|') !== base, `${m}: chip set identical to default`);
    }
    eq(await serverChips(owner.page, 'no.such.model'), await serverChips(owner.page, 'default'), 'unknown context falls back to default');
  });

  // One signed-in session per role, reused for every context (the realistic path: navigate, not re-login).
  const roles = { owner, acct: await S(USERS.acct) };
  const plain = await S(USERS.plain);

  for (const model of CONTEXTS) {
    const who = model === 'account.move' ? 'acct' : 'owner';
    await check(`${model} [${who}]: real navigation, browser sends the right context, chips match the registry`, async () => {
      const s = roles[who]; CURRENT = s.page;
      s.ctxSent.length = 0;
      if (model === 'default') { await s.page.goto(`${BASE}/atlas`); await s.page.waitForSelector('.o_main_navbar'); }
      else await openRecord(s.page, model, ids[model]);
      await openPanel(s.page);
      ok(await s.page.locator('.nc-ai-chat__panel').isVisible(), 'panel visible');
      ok(s.ctxSent.includes(model), `context '${model}' not sent; sent ${JSON.stringify(s.ctxSent)}`);
      eq(await chipTexts(s.page), await serverChips(s.page, model), 'chips vs server registry for this context');
      await shot(s.page, `ctx_${model.replace('.', '_')}_en`);
    });
    await check(`${model} [${who}]: a chip is answered by the mock gateway and persisted in order`, async () => {
      const s = roles[who]; CURRENT = s.page;
      const before = await count(s.page);
      await s.page.locator('.nc-ai-chat__chip').first().click();
      await s.page.waitForFunction((n) => document.querySelectorAll('.nc-ai-chat__msg--assistant').length > n, await s.page.locator('.nc-ai-chat__msg--assistant').count(), { timeout: 45000 });
      eq(await count(s.page), before + 2, 'two rows written');
      const rows = await rpc(s.page, 'ncollection.ai.chat.message', 'search_read', [[]], { fields: ['role'], order: 'id desc', limit: 2 });
      eq(rows.map((r) => r.role), ['assistant', 'user'], 'last two roles');
      eq(s.errors, [], 'page errors');
    });
  }

  await check('permissions: a plain internal user gets no widget on any of the five contexts and the server refuses them', async () => {
    for (const model of CONTEXTS) {
      if (model === 'default') await plain.page.goto(`${BASE}/atlas`);
      else await plain.page.goto(`${BASE}/atlas/${model}/${ids[model]}`);
      await plain.page.waitForSelector('.o_main_navbar', { timeout: 40000 });
      await plain.page.waitForTimeout(1500);
      eq(await plain.page.locator('.nc-ai-chat').count(), 0, `${model}: widget elements`);
    }
    eq(await rpc(plain.page, 'ncollection.ai.chat', 'get_state', [], { module_ctx: 'sale.order' }), { enabled: false }, 'get_state');
    let refused = false;
    try { await rpc(plain.page, 'ncollection.ai.chat', 'send_suggested', ['workspace_summary']); } catch (e) { refused = true; }
    ok(refused, 'send_suggested must be refused');
  });

  await check('permissions: the transcript is the same per-user log in every context (accountant never sees the owner\'s rows)', async () => {
    const o = await count(roles.owner.page), a = await count(roles.acct.page);
    ok(o >= 4 && a >= 1, `owner ${o}, accountant ${a}`);
    const mine = await rpc(roles.acct.page, 'ncollection.ai.chat.message', 'search_read', [[]], { fields: ['user_id'] });
    const users = new Set(mine.map((r) => r.user_id[0]));
    eq(users.size, 1, 'one author only');
  });

  await check('navigation: panel open, SPA moves (record -> list -> other record) refresh the chips for the new context', async () => {
    const s = roles.owner; CURRENT = s.page;
    // ?debug=1 exposes the web client env, so the move goes through Odoo's own action service (a real router change,
    // no document reload) instead of a synthetic history event the router would ignore.
    await s.page.goto(BASE + '/atlas/sale.order/' + ids['sale.order'] + '?debug=1');
    await s.page.waitForSelector('.o_form_view', { timeout: 45000 });
    await openPanel(s.page);
    const go = (action) => s.page.evaluate(async (a) => { await odoo.__WOWL_DEBUG__.root.env.services.action.doAction(a); }, action);
    const form = (model) => ({ type: 'ir.actions.act_window', res_model: model, res_id: ids[model], views: [[false, 'form']], target: 'current' });
    const list = (model) => ({ type: 'ir.actions.act_window', res_model: model, views: [[false, 'list']], target: 'current' });
    const step = async (action, selector, expectCtx) => {
      s.ctxSent.length = 0;
      await go(action);
      await s.page.waitForSelector(selector, { timeout: 30000 });
      await s.page.waitForTimeout(1500);
      return s.ctxSent.slice();
    };
    const toCrm = await step(form('crm.lead'), '.o_form_view');
    ok(toCrm.includes('crm.lead'), `record -> crm.lead record: sent ${JSON.stringify(toCrm)}`);
    eq(await chipTexts(s.page), await serverChips(s.page, 'crm.lead'), 'chips follow crm.lead');
    const toList = await step(list('sale.order'), '.o_list_view');
    console.log('   context sent on a LIST route:', JSON.stringify(toList));
    const toMove = await step(form('account.move'), '.o_form_view');
    ok(toMove.includes('account.move'), `list -> account.move record: sent ${JSON.stringify(toMove)}`);
    eq(await chipTexts(s.page), await serverChips(s.page, 'account.move'), 'chips follow account.move');
    await shot(s.page, 'ctx_navigation_spa');
  });

  // ---- Arabic / RTL on every context the Arabic accountant is allowed to open
  const ar = await S(USERS.ar, { lang: 'ar_001' });
  const arabic = /[؀-ۿ]/;
  for (const model of ['default', 'account.move']) {
    await check(`${model} [Arabic accountant]: RTL, Arabic chips matching the context, bubble at the inline-end`, async () => {
      CURRENT = ar.page; ar.ctxSent.length = 0;
      if (model === 'default') await ar.page.goto(`${BASE}/atlas`); else await openRecord(ar.page, model, ids[model]);
      await openPanel(ar.page, true);
      ok(ar.ctxSent.includes(model), `context ${model} not sent: ${JSON.stringify(ar.ctxSent)}`);
      const chips = await chipTexts(ar.page);
      ok(chips.length >= 3 && chips.every((t) => arabic.test(t)), 'chips Arabic: ' + JSON.stringify(chips));
      const box = await ar.page.evaluate(() => {
        const b = document.querySelector('.nc-ai-chat__bubble').getBoundingClientRect();
        const p = document.querySelector('.nc-ai-chat__panel').getBoundingClientRect();
        return { dir: getComputedStyle(document.body).direction, bl: b.left, br: b.right, pl: p.left, pr: p.right, vw: innerWidth,
          over: document.documentElement.scrollWidth > innerWidth + 1 };
      });
      eq(box.dir, 'rtl', 'direction');
      ok(box.br < box.vw / 2 + 40 && box.pl >= 0 && box.pr <= box.vw && !box.over, 'layout ' + JSON.stringify(box));
      await shot(ar.page, `ctx_${model.replace('.', '_')}_ar`);
    });
  }
  await check('Arabic mobile 390px on an account.move record: RTL panel fits', async () => {
    const m = await S(USERS.ar, { lang: 'ar_001', viewport: { width: 390, height: 800 } });
    await openRecord(m.page, 'account.move', ids['account.move']);
    await openPanel(m.page, true);
    const b = await m.page.evaluate(() => { const p = document.querySelector('.nc-ai-chat__panel').getBoundingClientRect();
      return { l: p.left, r: p.right, t: p.top, bt: p.bottom, vw: innerWidth, vh: innerHeight, over: document.documentElement.scrollWidth > innerWidth + 1 }; });
    ok(b.l >= 0 && b.r <= b.vw && b.t >= 0 && b.bt <= b.vh && !b.over, JSON.stringify(b));
    await shot(m.page, 'ctx_account_move_ar_mobile');
  });

  // ---- tenant isolation: the request leaves signed as THIS database and the gateway holds no database
  await check('tenant isolation: the gateway saw only this tenant\'s name, and no prompt text from another database exists', async () => {
    const log = execSync('docker logs ncai_gateway 2>&1 | tail -n 400', { encoding: 'utf8', maxBuffer: 1 << 24 });
    const tenants = new Set([...log.matchAll(/tenant[\"'=: ]+([A-Za-z0-9_]+)/gi)].map((m) => m[1].toLowerCase()));
    console.log('   gateway tenant values seen:', JSON.stringify([...tenants]));
    ok([...tenants].every((t) => t === 'uxaiweb' || t === 'tenant'), 'unexpected tenant in gateway log: ' + [...tenants].join(','));
    const dbs = execSync('docker exec ncai_pg psql -U odoo -d postgres -Atc "select datname from pg_database where datname like \'ux%\'"', { encoding: 'utf8' }).trim().split(/\s+/);
    eq(dbs, ['uxaiweb'], 'only the disposable database exists on the private server');
  });

  await check('no page errors, no dialogs across the whole run', async () => {
    for (const s of sessions) { eq(s.errors, [], 'page errors'); eq(s.dialogs, [], 'dialogs'); }
  });

  const passed = results.filter((r) => r.ok).length;
  console.log(`\nAI CHAT FIVE-CONTEXT ACCEPTANCE: ${passed}/${results.length} passed`);
  await browser.close();
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error('SUITE CRASH', e); process.exit(2); });
