import { test, expect } from '@playwright/test';
import { TENANTS, loginViaRpc } from '../fixtures/tenants';

/**
 * Department dashboards — render + role-gate smoke tests (#363 follow-up).
 *
 * WHAT WAS LEFT UNCOVERED
 * -----------------------
 * #370 closed #363 with browser coverage for the four FINANCIAL dashboards. Its
 * own header says "Financial + department dashboards" and counts 8 OWL files,
 * but its DASHBOARDS array lists four. Sales, HR and Warehouse shipped with no
 * browser coverage at all, which is the same gap #363 was filed for, three
 * dashboards smaller.
 *
 * WHY THE ASSERTION IS `__period` AND NOT A KPI VALUE
 * ---------------------------------------------------
 * The financial spec asserts a non-empty `.nc-kpi-card__value`, and it is right
 * to: those dashboards always have figures once entries are posted. Department
 * KPIs are OPTIONAL by construction — `get_sales_dashboard` builds
 * `[k for k in (...) if k is not None]`, so an absent `crm`/`sale` module or a
 * quiet period yields an empty `kpis` list and the template renders a
 * legitimate "no data" empty state. Asserting a KPI value here would fail for a
 * reason that has nothing to do with the dashboard working.
 *
 * `.nc-fin-dashboard__period` renders under `t-if="state.payload"`. Its presence
 * therefore proves the RPC RETURNED — which is exactly what the #356 role guard
 * breaks when it denies. Empty-but-loaded and denied are different DOM states,
 * and this distinguishes them; asserting only that the root mounted would not,
 * because the root renders in the error branch too.
 *
 * THE DENIAL TESTS ARE THE POINT
 * ------------------------------
 * #356 gated these three at the RPC after the security review found a Manager
 * could read the Sales pipeline. That is asserted at the ORM in
 * tests/test_department_dashboards.py. Nothing proved it end to end: that a
 * denied user actually SEES a refusal rather than a half-rendered dashboard or
 * a silent empty shell. `biz` is the ideal probe and already exists — it holds
 * `sales_team.group_sale_salesman` and NOT `ncollection_core.group_role_sales`,
 * which is precisely the shape of the user the exposure allowed through.
 */

const DEPARTMENTS = [
  { name: 'Sales',     probe: 'dept_sales', title: 'Sales Dashboard',
    action: 'ncollection_account_dashboard.action_sales_dashboard' },
  { name: 'HR',        probe: 'dept_hr',    title: 'HR Dashboard',
    action: 'ncollection_account_dashboard.action_hr_dashboard' },
  { name: 'Warehouse', probe: 'dept_wh',    title: 'Warehouse Dashboard',
    action: 'ncollection_account_dashboard.action_warehouse_dashboard' },
] as const;

const open = (page: import('@playwright/test').Page, action: string) =>
  page.goto(`${TENANTS.e2eclienta}/odoo/action-${action}`, {
    waitUntil: 'domcontentloaded',
  });

test.describe('department dashboards render for their own role (#363)', () => {
  for (const { name, probe, title, action } of DEPARTMENTS) {
    test(`${name} dashboard mounts and receives a server payload`, async ({ page }) => {
      // Not the login form: the edge throttles /web/login to 10 r/m and the
      // auth journeys already spend that budget.
      await loginViaRpc(page, 'e2eclienta', probe, 'demo1234');
      await open(page, action);

      await expect(page.locator('.nc-fin-dashboard')).toBeVisible({ timeout: 30_000 });

      // The RIGHT dashboard: all three extend the same base and reuse its
      // template verbatim, so the root class alone cannot tell them apart —
      // three tests could pass against one dashboard without this.
      await expect(page.locator('.nc-fin-dashboard__title')).toHaveText(title);

      // The load-bearing assertion: `__period` is `t-if="state.payload"`, so it
      // appears only once the RPC has returned. An AccessError leaves
      // state.payload null and this absent.
      await expect(page.locator('.nc-fin-dashboard__period'))
        .toBeVisible({ timeout: 20_000 });

      // And not the error branch, stated explicitly rather than inferred.
      await expect(page.getByText('Unable to load the dashboard')).toHaveCount(0);
    });
  }
});

test.describe('department dashboards refuse a caller without the role (#356)', () => {
  test('a user with the native Sales group but not the Sales ROLE is refused', async ({ page }) => {
    // `biz` holds sales_team.group_sale_salesman and no ncollection role. Before
    // #356 that user could call get_sales_dashboard and receive the pipeline
    // funnel; the ORM test pins the AccessError, this pins what the user sees.
    await loginViaRpc(page, 'e2eclienta', 'biz', 'demo1234');
    await open(page, 'ncollection_account_dashboard.action_sales_dashboard');

    await expect(page.getByText('Unable to load the dashboard'))
      .toBeVisible({ timeout: 30_000 });
    // No payload reached the client — the guard refused before any data.
    await expect(page.locator('.nc-fin-dashboard__period')).toHaveCount(0);
  });

  test('a Sales-role user is refused the HR dashboard', async ({ page }) => {
    // Cross-role, not merely unauthenticated: proves each guard names its OWN
    // role rather than admitting any department role, which a single
    // copy-pasted check would.
    await loginViaRpc(page, 'e2eclienta', 'dept_sales', 'demo1234');
    await open(page, 'ncollection_account_dashboard.action_hr_dashboard');

    await expect(page.getByText('Unable to load the dashboard'))
      .toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.nc-fin-dashboard__period')).toHaveCount(0);
  });
});
