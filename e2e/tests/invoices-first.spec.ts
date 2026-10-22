import { test, expect, Page } from '@playwright/test';
import { callKw, loginViaRpc, NAVBAR, TENANTS } from '../fixtures/tenants';
import { clickViewAndCapturePdf, pdfFileNameOf } from '../fixtures/document-view';

/**
 * Sales opens on Invoices (e2eclienta: Sales + Accounting, admin = workspace Owner).
 *  - the Sales menu lists Invoices first, then the quotation / order workflow, which is untouched;
 *  - Sales > Invoices > New is a customer invoice straight away (no quotation or order);
 *  - an invoice row carries View and Edit; View opens the printed PDF inline, in a new tab (ncollection_core's own endpoint);
 *  - a quotation row still carries the paperclip column and its own View.
 */
const TENANT = 'e2eclienta' as const;

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

/** The Sales APP (its root menu): the quotations action alone also sits under the Reports hub, so it is not the app. */
async function openSales(page: Page) {
  const rows = (await callKw(page.request, TENANT, 'ir.model.data', 'search_read',
    [[['module', '=', 'sale'], ['name', '=', 'sale_menu_root']], ['res_id']])).result;
  await page.goto(`${TENANTS[TENANT]}/odoo?menu_id=${rows[0].res_id}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(NAVBAR, { timeout: 60_000 });
  await page.waitForSelector('.o_main_navbar .o_menu_sections', { timeout: 60_000 });
}

test.describe('Sales: invoices first', () => {
  test.beforeEach(async ({ page }) => {
    await loginViaRpc(page, TENANT);
  });

  test('Invoices is the first Sales entry and the quotation / order menus are still there', async ({ page }) => {
    const errors = collectErrors(page);
    await openSales(page);
    const sections = await page.$$eval('.o_main_navbar .o_menu_sections > *', (nodes) =>
      nodes.map((n) => (n as HTMLElement).innerText.trim().split('\n')[0]).filter(Boolean),
    );
    expect(sections[0]).toBe('Invoices');
    expect(sections).toContain('Orders');
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('Sales > Invoices > New is a customer invoice, with no quotation or order behind it', async ({ page }) => {
    const errors = collectErrors(page);
    await openSales(page);
    await page.locator('.o_main_navbar .o_menu_sections').getByText('Invoices', { exact: true }).first().click();
    await page.waitForSelector('.o_list_view', { timeout: 30_000 });
    await page.locator('.o_control_panel .o_list_button_add').first().click();
    await page.waitForSelector('.o_form_view', { timeout: 30_000 });
    await expect(page.locator('.o_form_view')).toContainText('Customer Invoice');
    await expect(page.locator('.o_form_view [name=partner_id]').first()).toBeVisible();
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('an invoice row has View and Edit; View is the printed PDF, inline', async ({ page, playwright }) => {
    const errors = collectErrors(page);
    const request = await playwright.request.newContext();
    const auth = await request.post(`${TENANTS[TENANT]}/web/session/authenticate`, {
      data: { jsonrpc: '2.0', params: { db: TENANT, login: 'admin', password: 'admin' } },
    });
    expect((await auth.json()).result.uid).toBeTruthy();
    const partner = (await callKw(request, TENANT, 'res.partner', 'create', [{ name: 'E2E Invoice Customer' }])).result;
    const move = (await callKw(request, TENANT, 'account.move', 'create', [{
      move_type: 'out_invoice', partner_id: partner,
      invoice_line_ids: [[0, 0, { name: 'E2E line', quantity: 1, price_unit: 42, tax_ids: [[5]] }]],
    }])).result;
    await callKw(request, TENANT, 'account.move', 'action_post', [[move]]);
    await page.goto(`${TENANTS[TENANT]}/odoo/action-account.action_move_out_invoice_type`, {
      waitUntil: 'domcontentloaded',
    });
    await page.waitForSelector('.o_list_view .o_data_row', { timeout: 60_000 });
    const row = page.locator('.o_data_row', { hasText: 'E2E Invoice Customer' }).first();
    await expect(row.locator('button[name=action_nc_view]')).toBeVisible();
    await expect(row.locator('button[name=action_nc_edit]')).toBeVisible();
    // the invoice's own status column (Odoo's) is still there beside the computed payment columns
    await expect(row).toContainText('Posted');
    const number = (await callKw(request, TENANT, 'account.move', 'read', [[move], ['name']])).result[0].name;
    // browser-independent: the canonical route's own response (see fixtures/document-view.ts), not the tab's load state
    await clickViewAndCapturePdf(page, row.locator('button[name=action_nc_view]'), {
      model: 'account.move', id: move, fileName: pdfFileNameOf(number),
    });
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('the quotation list is unchanged: the Files column and the new View action are both there', async ({ page }) => {
    const errors = collectErrors(page);
    await openSales(page);
    await page.waitForSelector('.o_list_view', { timeout: 30_000 });
    await expect(page.locator('.o_list_view thead')).toContainText('Files');
    const hasRows = (await page.locator('.o_data_row').count()) > 0;
    if (hasRows) {
      await expect(page.locator('.o_data_row').first().locator('button[name=action_nc_view]')).toBeVisible();
    }
    expect(errors, 'no uncaught page error').toEqual([]);
  });
});
