import { test, expect } from '@playwright/test';
import { TENANTS, loginViaRpc } from '../fixtures/tenants';

/**
 * Financial + department dashboards — render smoke tests (#363).
 *
 * WHY THESE EXIST
 * ---------------
 * The 8 OWL files under ncollection_account_dashboard had no browser coverage of
 * any kind. #333 / #356 / #358 closed the RPC side — a Sales-role user cannot
 * FETCH these payloads — but nothing proved a payload ever RENDERS. A component
 * that throws on mount, or renders an empty shell, is invisible to lint, to the
 * Python suite and to the RPC guards alike. That is not hypothetical: it is
 * precisely the failure dashboard.spec.ts was written for, when the core
 * dashboard silently vanished from the actions registry behind a stale asset
 * bundle and every other gate passed it through.
 *
 * WHY PLAYWRIGHT AND NOT ODOO TOURS
 * ---------------------------------
 * Tours look like the cheaper layer, and here they are not. `odoo:19` ships no
 * browser, so an HttpCase tour SKIPS — and odoo still prints
 * "0 failed, 0 error(s) of N tests", i.e. green having executed nothing. Making
 * tours work means putting Chromium in the test image (~300MB, plus install time
 * on every CI run). verify.yml ALREADY installs chromium for this suite, so
 * these tests ride on infrastructure that is already paid for.
 *
 * WHAT THEY ASSERT
 * ----------------
 * Deliberately shallow, and deliberately not shallower. `.nc-kpi-card__value`
 * must be NON-EMPTY: asserting the container exists would pass against a
 * dashboard that mounted and rendered nothing, which is the exact shape of a
 * silent failure. The VALUES themselves are covered server-side in
 * ncollection_account_dashboard/tests/, where role gating can be asserted on the
 * payload rather than on markup — same division of labour dashboard.spec.ts
 * documents.
 */

/** Probing as `fin` (CEO role), because these dashboards are gated at the RPC. */
const PROBE = 'fin';

const DASHBOARDS = [
  { name: 'CEO', action: 'ncollection_account_dashboard.action_ceo_dashboard' },
  { name: 'Finance', action: 'ncollection_account_dashboard.action_finance_dashboard' },
  { name: 'Accountant', action: 'ncollection_account_dashboard.action_accountant_dashboard' },
  { name: 'Cash', action: 'ncollection_account_dashboard.action_cash_dashboard' },
] as const;

test.describe('financial dashboards render for an admitted role (#363)', () => {
  for (const { name, action } of DASHBOARDS) {
    test(`${name} dashboard mounts and shows a server-provided KPI`, async ({ page }) => {
      // Not the login form: the edge throttles /web/login to 10 r/m and this
      // suite already spends that budget on the auth journeys.
      await loginViaRpc(page, 'e2eclienta', PROBE, 'demo1234');

      await page.goto(`${TENANTS.e2eclienta}/odoo/action-${action}`, {
        waitUntil: 'domcontentloaded',
      });

      // The client action resolved and the component mounted.
      await expect(page.locator('.nc-fin-dashboard')).toBeVisible({ timeout: 30_000 });

      // The load-bearing assertion: a KPI carries real text from the server
      // payload. An empty shell does not satisfy this.
      const card = page.locator('.nc-fin-dashboard__kpis .nc-kpi-card').first();
      await expect(card).toBeVisible({ timeout: 20_000 });
      await expect(card.locator('.nc-kpi-card__value')).not.toBeEmpty();
    });
  }
});

/**
 * #332 — charts must shrink when the window does.
 *
 * WHY THIS TEST DID NOT EXIST. Tablet rendering was always fine: a real tablet
 * LOADS at 768px, and at that width the canvas fits exactly. Only a live
 * RESIZE broke it, and no test had ever changed a viewport — grep confirmed no
 * spec called setViewportSize before this one.
 *
 * WHY IT MEASURES canvas-vs-CONTAINER. The ticket records that its first
 * automated check asserted `documentElement.scrollWidth > clientWidth` — page
 * level — and PASSED, because the canvas overflows its CARD, not the document.
 * An assertion against the wrong container is indistinguishable from a passing
 * one, so this compares the canvas to the element that is supposed to contain
 * it.
 *
 * One dashboard is enough: the chart lifecycle lives in the shared
 * dashboard_base.js, so all four financial and three department dashboards
 * share this code path.
 */
test('#332 the chart canvas shrinks with its container on a live resize', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginViaRpc(page, 'e2eclienta', PROBE, 'demo1234');
  await page.goto(
    `${TENANTS.e2eclienta}/odoo/action-ncollection_account_dashboard.action_finance_dashboard`,
    { waitUntil: 'domcontentloaded' },
  );

  const box = page.locator('.nc-fin-dashboard__canvas').first();
  await expect(box).toBeVisible({ timeout: 30_000 });
  await expect(box.locator('canvas')).toBeVisible({ timeout: 20_000 });

  const widths = async () => box.evaluate((el) => ({
    container: Math.round(el.getBoundingClientRect().width),
    canvas: Math.round((el.querySelector('canvas') as HTMLCanvasElement).getBoundingClientRect().width),
  }));

  const wide = await widths();
  expect(wide.canvas).toBeLessThanOrEqual(wide.container + 2);

  await page.setViewportSize({ width: 768, height: 900 });
  // Chart.js resizes through a ResizeObserver, which is asynchronous. Poll
  // rather than sleeping a fixed amount: a fixed wait either flakes or slows
  // every run, and the failure mode here is permanent, not slow.
  await expect.poll(async () => (await widths()).container, { timeout: 10_000 })
    .toBeLessThan(wide.container);

  // The control: without it, a page that failed to resize at all would satisfy
  // the assertion below trivially.
  const narrow = await widths();
  expect(narrow.container).toBeLessThan(wide.container);

  await expect.poll(async () => {
    const w = await widths();
    return w.canvas - w.container;
  }, { timeout: 10_000 }).toBeLessThanOrEqual(2);
});
