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
 * MEASURED CAVEAT: this one did NOT catch the mutation the two id-based tests
 * caught. Re-parenting Beta under Alpha leaks Beta's documents to Alpha, and
 * both id-based tests failed — but this list assertion stayed green, because
 * the portal list renders a leaked document under the COMMERCIAL partner's
 * name (Alpha), so the string "Portal Beta Ltd" never appears.
 *
 * Kept because it pins the ordinary case cheaply, and stated plainly so nobody
 * reads it as isolation evidence: the id-based tests above are what prove it.
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
