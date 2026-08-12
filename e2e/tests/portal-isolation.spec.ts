import { test, expect } from '@playwright/test';
import { TENANTS, loginViaRpc } from '../fixtures/tenants';

/**
 * Portal isolation over HTTP — the IDOR vector (P6-T02 / #66).
 *
 * WHY THIS EXISTS SEPARATELY FROM THE PYTHON SUITE
 * ------------------------------------------------
 * `ncollection_core/tests/test_portal_isolation.py` proves isolation at the ORM
 * control point, which `ir.rule` enforces identically for browser HTTP, JSON-RPC
 * and XML-RPC — so it already covers "URL manipulation" in the sense that
 * matters. What it does NOT describe is the controller layer: `/my/...` routes
 * go through `portal._document_check_access`, which on `AccessError` falls back
 * to comparing an `access_token`. That fallback is a second, independent path to
 * a record, and only an HTTP test exercises it.
 *
 * WHY isolation.spec.ts IS NOT THE PLACE
 * --------------------------------------
 * That spec is P1-T06: cross-TENANT, cross-DATABASE session isolation. This is a
 * different threat model — two partners inside ONE tenant database. Conflating
 * them would let a passing cross-database test be mistaken for evidence about
 * cross-partner access, which it is not.
 *
 * THE CONTROL IS ASSERTED FIRST, DELIBERATELY
 * -------------------------------------------
 * Every "cannot reach" assertion here is preceded by proof that the SAME url
 * shape IS reachable for its rightful owner. Before this ticket the tenant had
 * zero invoices and no real portal user, so a suite like this would have passed
 * against an empty database — the vacuity this repo has shipped four times
 * (#330, #348, #363, #381).
 */

const TENANT = 'e2eclienta' as const;
const PW = 'demo1234';

/** Record ids the portal list page exposes to the logged-in user. */
async function ownDocumentIds(page: any, path: string): Promise<number[]> {
  await page.goto(`${TENANTS[TENANT]}${path}`, { waitUntil: 'domcontentloaded' });
  const hrefs = await page.locator(`a[href*="${path}/"]`).evaluateAll(
    (els: HTMLAnchorElement[]) => els.map((e) => e.getAttribute('href') || ''),
  );
  const ids = hrefs
    .map((h: string) => {
      const m = h.match(new RegExp(`${path}/(\\d+)`));
      return m ? Number(m[1]) : NaN;
    })
    .filter((n: number) => Number.isFinite(n));
  return [...new Set<number>(ids)];
}

for (const { label, path } of [
  { label: 'invoices', path: '/my/invoices' },
  { label: 'orders', path: '/my/orders' },
]) {
  test(`#66 a portal user cannot open another partner's ${label} by id`, async ({ browser }) => {
    // Two independent browser contexts: one session per portal user, so neither
    // inherits the other's cookies.
    const ctxA = await browser.newContext();
    const ctxB = await browser.newContext();
    const a = await ctxA.newPage();
    const b = await ctxB.newPage();
    await loginViaRpc(a, TENANT, 'portala', PW);
    await loginViaRpc(b, TENANT, 'portalb', PW);

    const mine = await ownDocumentIds(a, path);
    const theirs = await ownDocumentIds(b, path);

    // CONTROL 1: each user can actually see something of their own. Without
    // this, the assertion below is satisfied by an empty portal.
    expect(mine.length, `portal A sees no ${label} of its own — the isolation
      assertion below would pass vacuously`).toBeGreaterThan(0);
    expect(theirs.length, `portal B sees no ${label} of its own`).toBeGreaterThan(0);

    // CONTROL 2: the two users' documents are genuinely different records.
    const overlap = mine.filter((id) => theirs.includes(id));
    expect(overlap, 'the two portal users share a document, so there is nothing '
      + 'to isolate').toHaveLength(0);

    // THE ASSERTION: A requests B's document id directly — the IDOR shape.
    for (const id of theirs) {
      const res = await a.request.get(`${TENANTS[TENANT]}${path}/${id}`);
      // Odoo answers an unauthorised portal document with 403, or redirects to
      // /my. What must never happen is a 200 rendering the record.
      if (res.status() === 200) {
        const body = await res.text();
        expect(body).not.toContain('Portal Beta Ltd');
      } else {
        expect([301, 302, 303, 307, 308, 403, 404]).toContain(res.status());
      }
    }

    await ctxA.close();
    await ctxB.close();
  });
}

/**
 * MEASURED CAVEAT: this one cannot catch a leak, and the reason recorded here
 * originally was WRONG — corrected after #66's security review checked the
 * templates.
 *
 * It said a leaked document renders under the commercial partner's name. It
 * does not. `portal_my_invoices` (account/views/account_portal_templates.xml)
 * has NO partner column at all — the row is Invoice # / Date / Due Date /
 * Amount / Status. The "Portal Alpha Ltd" string this test matches comes from
 * the portal HEADER (portal/views/portal_templates.xml, `t-esc="user_id.name"`)
 * — the logged-in user's own name, rendered on every page regardless of which
 * documents are listed. So the control would read identically whether or not a
 * leak occurred, and a leaked Beta row would carry no partner label to detect.
 *
 * Confirmed empirically: re-parenting Beta under Alpha fails the two id-based
 * tests and the #403 PDF test, and leaves this one green.
 *
 * Kept because it pins the ordinary case cheaply, and stated plainly so nobody
 * reads it as isolation evidence: the id-based tests are what prove it.
 */
test('#66 the portal list page itself shows only the owner\'s documents', async ({ page }) => {
  await loginViaRpc(page, TENANT, 'portala', PW);
  await page.goto(`${TENANTS[TENANT]}/my/invoices`, { waitUntil: 'domcontentloaded' });
  const body = await page.locator('body').innerText();
  // The control and the assertion in one place: A's own company must appear,
  // B's must not. Asserting only the absence would pass on a blank page.
  expect(body).toContain('Portal Alpha Ltd');
  expect(body).not.toContain('Portal Beta Ltd');
});

/**
 * #403 — the vectors #66 did not reach: the CONTROLLER path.
 *
 * Portal users have NO ORM access to ir.attachment at all — group_portal has no
 * ir.model.access row for it, measured on a live tenant — so attachments reach
 * them only through controllers that use sudo plus `_document_check_access`.
 * An ORM test therefore proves the wrong thing; this is the layer that matters.
 *
 * Both routes are probed with a REAL other-partner id, discovered at runtime
 * rather than hardcoded, so the test cannot silently drift onto a nonexistent
 * record and pass by 404.
 */
test('#403 a portal user cannot fetch another partner\'s invoice PDF', async ({ browser }) => {
  const ctxA = await browser.newContext();
  const ctxB = await browser.newContext();
  const a = await ctxA.newPage();
  const b = await ctxB.newPage();
  await loginViaRpc(a, TENANT, 'portala', PW);
  await loginViaRpc(b, TENANT, 'portalb', PW);

  const mine = await ownDocumentIds(a, '/my/invoices');
  const theirs = await ownDocumentIds(b, '/my/invoices');
  expect(mine.length, 'A has no invoice — control failed').toBeGreaterThan(0);
  expect(theirs.length, 'B has no invoice — nothing to probe').toBeGreaterThan(0);

  const pdf = (page: any, id: number) => page.request.get(
    `${TENANTS[TENANT]}/my/invoices/${id}?report_type=pdf&download=true`,
    // maxRedirects: 0 is LOAD-BEARING. Odoo answers an unauthorised document
    // with 303 -> /my, and /my returns 200. Following redirects turns a denial
    // into a "200" and the assertion below then reads a refusal as a successful
    // download. The first version of this test did exactly that and reported a
    // leak that did not exist. Measured:
    //   own   -> 200 application/pdf   24084 bytes
    //   other -> 303 text/html            193 bytes  location=/my
    { maxRedirects: 0 },
  );

  // CONTROL: A's own PDF really is served, so a denial below is about ownership
  // rather than the route being broken for everyone.
  const own = await pdf(a, mine[0]);
  expect(own.status(), 'A cannot fetch its OWN invoice PDF').toBe(200);
  expect(own.headers()['content-type']).toContain('application/pdf');

  for (const id of theirs) {
    const res = await pdf(a, id);
    // Assert on WHAT CAME BACK, not just the status: a PDF body is the leak.
    expect(res.headers()['content-type'] || '',
      `A received a PDF for partner B's invoice (id ${id})`)
      .not.toContain('application/pdf');
    expect([303, 302, 403, 404]).toContain(res.status());
  }

  await ctxA.close();
  await ctxB.close();
});

test('#403 /web/content serves portal users no DOCUMENT attachment', async ({ browser }) => {
  // STATED AS MEASURED, not as isolation. Portal users have NO ir.attachment
  // ACL (group_portal has no row for it), so this route denies them in BOTH
  // directions — own and other alike. A "A cannot fetch B's" assertion here
  // would therefore pass just as well against a route that serves nobody.
  //
  // TWO EARLIER VERSIONS WERE WRONG, both in ways that still looked like a pass:
  //   1. Hardcoded the fixture's attachment ids (560/561). Those exist only on
  //      the database they were seeded in — on CI's fresh tenant they do not
  //      exist, so the route answered 404 "not found" rather than "not allowed"
  //      and the test passed having probed nothing.
  //   2. Scanned ids 1..60 instead. That FAILED, because id 12 is
  //      `placeholder.png` — a public web asset, `public=True` by design and
  //      correctly served to everyone. Broad scanning cannot tell a public
  //      asset from a leaked document.
  //
  // So the ids are resolved at RUNTIME, as admin, restricted to attachments
  // that hang on the three documents this suite is about. That is
  // fixture-independent and cannot be satisfied by an id that does not exist.
  const admin = await browser.newContext();
  const adminPage = await admin.newPage();
  await loginViaRpc(adminPage, TENANT, 'admin', 'admin');
  const res = await adminPage.request.post(
    `${TENANTS[TENANT]}/web/dataset/call_kw`, {
      data: {
        jsonrpc: '2.0', method: 'call',
        params: {
          model: 'ir.attachment', method: 'search_read',
          args: [[['res_model', 'in',
                   ['account.move', 'sale.order', 'stock.picking']]],
                 ['id', 'name', 'public']],
          kwargs: {},
        },
      },
    });
  const docAttachments = (await res.json())?.result ?? [];
  await admin.close();

  // CONTROL: there ARE document attachments to probe. The fixture seeds one per
  // party; if that ever stops, this test must fail rather than pass on none.
  expect(docAttachments.length,
    'no attachments hang on any invoice/order/picking, so this test probed '
    + 'nothing — the fixture seed has regressed').toBeGreaterThan(0);
  // And none of them is public, which would be a legitimate 200 below.
  expect(docAttachments.filter((a: any) => a.public)).toHaveLength(0);

  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await loginViaRpc(page, TENANT, 'portala', PW);
  for (const att of docAttachments) {
    const r = await page.request.get(`${TENANTS[TENANT]}/web/content/${att.id}`,
      { maxRedirects: 0 });
    expect(r.status(), `/web/content served a portal user the document `
      + `attachment "${att.name}" (id ${att.id}) — that route is now a path to `
      + 'documents and needs a real cross-partner isolation test')
      .not.toBe(200);
  }
  await ctx.close();
});
