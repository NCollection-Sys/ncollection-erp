import { test, expect } from '@playwright/test';
import { TENANTS, loginViaRpc } from '../fixtures/tenants';

/**
 * P1-T17 customer dashboard — render smoke test.
 *
 * This exists because the failure it guards is invisible to every other gate:
 * during development the OWL client action was silently missing from the
 * actions registry (stale asset bundle), which lint, unit tests and the Python
 * suite all passed straight through. Only a real browser surfaced it. The
 * assertion is deliberately shallow — that the client action resolves and the
 * server-provided widget reaches the DOM — because the widget *contents* are
 * covered server-side in ncollection_core/tests/test_dashboard.py, where role
 * gating can be asserted on the payload rather than on markup.
 */
test('dashboard client action renders a server-provided KPI card', async ({ page }) => {
  // Deliberately NOT the login form: the edge throttles /web/login to 10 r/m
  // and this suite already spends that budget on the auth journeys. See
  // loginViaRpc() for the full explanation.
  await loginViaRpc(page, 'e2eclienta');

  await page.goto(
    `${TENANTS.e2eclienta}/odoo/action-ncollection_core.action_ncollection_dashboard`,
    { waitUntil: 'domcontentloaded' },
  );

  // The action resolved and the component mounted.
  await expect(page.locator('.nc-dashboard')).toBeVisible({ timeout: 30_000 });

  // At least one widget survived the server-side role gate and rendered.
  const card = page.locator('.nc-kpi-card').first();
  await expect(card).toBeVisible({ timeout: 20_000 });
  await expect(card.locator('.nc-kpi-card__label')).not.toBeEmpty();
  await expect(card.locator('.nc-kpi-card__value')).not.toBeEmpty();
});
