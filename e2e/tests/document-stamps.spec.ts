import { test, expect, Page } from '@playwright/test';
import { callKw, loginViaRpc, TENANTS } from '../fixtures/tenants';
import { clickViewAndCapturePdf, pdfFileNameOf } from '../fixtures/document-view';

/**
 * Document stamps and View (e2eclienta: Accounting; admin = workspace Owner).
 *  - the Document Stamps admin lists the templates and the designer (page to scale, nudge, real PDF preview) works;
 *  - View opens the printed PDF inline, with the document's filename, in a new tab;
 *  - a stamp for one kind of document never lands on another kind.
 * Synthetic data only: a 1x1 PNG, a made-up customer.
 */
const TENANT = 'e2eclienta' as const;
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC';

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

async function typeId(page: Page, code: string): Promise<number> {
  const rows = (await callKw(page.request, TENANT, 'ncollection.document.type', 'search_read',
    [[['code', '=', code]]], { fields: ['id'] })).result;
  return rows[0].id;
}

test.describe('Document stamps and View', () => {
  test.beforeEach(async ({ page }) => {
    await loginViaRpc(page, TENANT);
  });

  test('the designer shows the paper, moves the stamp and previews a real PDF', async ({ page }) => {
    const errors = collectErrors(page);
    const stamp = (await callKw(page.request, TENANT, 'ncollection.document.stamp', 'create', [{
      name: `E2E stamp ${Date.now()}`, image: PNG, x_mm: 150, y_mm: 225, width_mm: 40, height_mm: 40,
      document_type_ids: [[6, 0, [await typeId(page, 'customer_invoice')]]],
    }])).result;
    await page.goto(`${TENANTS[TENANT]}/odoo/action-ncollection_branding.action_document_stamp/${stamp}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_nc_stamp_designer', { timeout: 60_000 });
    await expect(page.locator('.o_nc_stamp_designer__page')).toBeVisible();
    await expect(page.locator('.o_nc_stamp_designer__box')).toBeVisible();
    const before = Number(await page.locator('.o_field_widget[name=x_mm] input').inputValue());
    await page.locator('.o_nc_stamp_designer__pad button[title=Left]').click();
    await expect.poll(async () => Number(await page.locator('.o_field_widget[name=x_mm] input').inputValue())).toBeLessThan(before);
    await page.waitForSelector('iframe.o_nc_stamp_designer__pdf', { timeout: 30_000 });
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
    // the preview route is a real PDF, shown inline
    const preview = await page.request.get(`${TENANTS[TENANT]}/ncollection/stamp/preview/${stamp}`);
    expect(preview.status()).toBe(200);
    expect(preview.headers()['content-type']).toContain('application/pdf');
    expect(preview.headers()['content-disposition']).toContain('inline');
    expect((await preview.body()).subarray(0, 5).toString()).toBe('%PDF-');
    await callKw(page.request, TENANT, 'ncollection.document.stamp', 'unlink', [[stamp]]);
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('View opens the invoice PDF inline with its filename, in a new tab', async ({ page }) => {
    const errors = collectErrors(page);
    const partner = (await callKw(page.request, TENANT, 'res.partner', 'create', [{ name: 'E2E View Customer' }])).result;
    const move = (await callKw(page.request, TENANT, 'account.move', 'create', [{
      move_type: 'out_invoice', partner_id: partner,
      invoice_line_ids: [[0, 0, { name: 'E2E view line', quantity: 1, price_unit: 42, tax_ids: [[5]] }]],
    }])).result;
    await callKw(page.request, TENANT, 'account.move', 'action_post', [[move]]);
    await page.goto(`${TENANTS[TENANT]}/odoo/action-account.action_move_out_invoice_type`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_list_view .o_data_row', { timeout: 60_000 });
    const row = page.locator('.o_data_row', { hasText: 'E2E View Customer' }).first();
    const number = (await callKw(page.request, TENANT, 'account.move', 'read', [[move], ['name']])).result[0].name;
    // browser-independent: the canonical route's own response (see fixtures/document-view.ts), not the tab's load state
    await clickViewAndCapturePdf(page, row.locator('button[name=action_nc_view]'), {
      model: 'account.move', id: move, fileName: pdfFileNameOf(number),
    });
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('View of a record the user cannot print is refused, and a missing one is not found', async ({ page }) => {
    expect((await page.request.get(`${TENANTS[TENANT]}/ncollection/document/view/res.partner/1`)).status()).toBe(404);
    expect((await page.request.get(`${TENANTS[TENANT]}/ncollection/document/view/account.move/99999999`)).status()).toBe(404);
  });

  test('a stamp for one kind of document is never printed on another kind', async ({ page }) => {
    const partner = (await callKw(page.request, TENANT, 'res.partner', 'create', [{ name: 'E2E Kind Customer' }])).result;
    const mk = async (type: string) => {
      const id = (await callKw(page.request, TENANT, 'account.move', 'create', [{
        move_type: type, partner_id: partner,
        invoice_line_ids: [[0, 0, { name: 'E2E kind line', quantity: 1, price_unit: 10, tax_ids: [[5]] }]],
      }])).result;
      await callKw(page.request, TENANT, 'account.move', 'action_post', [[id]]);
      return id;
    };
    const invoice = await mk('out_invoice');
    const refund = await mk('out_refund');
    const stamp = (await callKw(page.request, TENANT, 'ncollection.document.stamp', 'create', [{
      name: `E2E kind ${Date.now()}`, image: PNG, document_type_ids: [[6, 0, [await typeId(page, 'customer_invoice')]]],
    }])).result;
    const viewInvoice = await page.request.get(`${TENANTS[TENANT]}/ncollection/document/view/account.move/${invoice}`);
    const viewRefund = await page.request.get(`${TENANTS[TENANT]}/ncollection/document/view/account.move/${refund}`);
    expect(viewInvoice.status()).toBe(200);
    expect(viewRefund.status()).toBe(200);
    // the stamp is an extra image object on the invoice page only: the stamped PDF is the larger of the two renderings
    // of comparable documents, and the refund (another kind) carries no stamp object of this template
    const count = (body: Buffer) => (body.toString('latin1').match(/\/Subtype\s*\/Image/g) || []).length;
    const invoiceImages = count(await viewInvoice.body());
    const refundImages = count(await viewRefund.body());
    expect(invoiceImages).toBeGreaterThan(refundImages);
    await callKw(page.request, TENANT, 'ncollection.document.stamp', 'unlink', [[stamp]]);
  });
});
