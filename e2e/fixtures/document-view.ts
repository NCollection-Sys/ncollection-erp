import { Locator, Page, expect } from '@playwright/test';

/**
 * "View opens the canonical printed PDF in a new tab" — checked WITHOUT assuming what the browser does with a PDF.
 *
 * What the application guarantees is the HTTP contract of `/ncollection/document/view/<model>/<id>`: 200,
 * `application/pdf`, `Content-Disposition: inline; filename=<the document's own name>`, the PDF bytes. What a browser then
 * does with that response is the browser's business: Chrome with a PDF viewer navigates the new tab to the URL and fires
 * `load`; Chromium's headless shell (what Playwright runs by default, and CI) has no PDF viewer, so the same response
 * becomes a DOWNLOAD — the tab never commits a navigation, never fires `load`, and its URL stays blank. A wait for the tab's
 * URL or load state therefore passes in one and times out in the other, whatever the product does.
 *
 * So the contract is observed where it is browser-independent: the browser's own request/response for the canonical
 * route, registered BEFORE the click. The tab is only required to OPEN (View is a new tab, not a replacement of the list).
 */
export const VIEW_ROUTE = /\/ncollection\/document\/view\/([\w.]+)\/(\d+)(?:[?#]|$)/;

export interface ViewedDocument {
  url: string;
  model: string;
  id: number;
  /** The status and headers the BROWSER received for its own request. */
  status: number;
  headers: Record<string, string>;
  /** The same URL fetched with the browser context's session: the PDF bytes. */
  body: Buffer;
}

/** The file name the server derives from a document's print name: what it replaces is what a file name may not hold. */
export function pdfFileNameOf(documentName: string): string {
  return `${documentName.replace(/[\\/:*?"<>|\r\n\t]+/g, '-').replace(/^[ .-]+|[ .-]+$/g, '')}.pdf`;
}

export async function clickViewAndCapturePdf(
  page: Page,
  view: Locator,
  expected: { model: string; id?: number; fileName?: string },
): Promise<ViewedDocument> {
  const context = page.context();
  const before = new Set(context.pages());
  const tabOpened = context.waitForEvent('page', { timeout: 30_000 });
  const answered = context.waitForEvent('response', {
    predicate: (r) => VIEW_ROUTE.test(r.url()) && r.request().method() === 'GET',
    timeout: 90_000,                       // the server renders the PDF before it answers
  });
  await view.click();
  const [, response] = await Promise.all([tabOpened, answered]);

  const match = VIEW_ROUTE.exec(response.url())!;
  const model = match[1];
  const id = Number(match[2]);
  expect(model, 'View points at the canonical document-view route of the right model').toBe(expected.model);
  if (expected.id !== undefined) expect(id).toBe(expected.id);

  // 1. what the browser itself was told
  const seen = response.headers();
  expect(response.status(), 'the browser got the document').toBe(200);
  expect(seen['content-type']).toContain('application/pdf');
  expect(seen['content-disposition'] || '', 'shown inline, never forced to download').toMatch(/^inline\s*;/i);

  // 2. the document itself, over the same session
  const served = await page.request.get(response.url());
  expect(served.status()).toBe(200);
  const headers = served.headers();
  expect(headers['content-type']).toContain('application/pdf');
  const disposition = decodeURIComponent(headers['content-disposition'] || '');
  expect(disposition).toMatch(/^inline\s*;/i);
  expect(disposition).not.toContain('attachment');
  if (expected.fileName) expect(disposition, 'named after the document').toContain(expected.fileName);
  else expect(disposition).toMatch(/filename.*\.pdf/);
  expect(headers['cache-control']).toContain('no-store');
  expect(headers['x-content-type-options']).toBe('nosniff');
  const body = await served.body();
  expect(body.subarray(0, 5).toString(), 'a real PDF').toBe('%PDF-');

  // tidy: close the tab(s) View opened (a download-only browser has already closed it)
  for (const tab of context.pages()) {
    if (!before.has(tab)) await tab.close().catch(() => undefined);
  }
  return { url: response.url(), model, id, status: response.status(), headers, body };
}
