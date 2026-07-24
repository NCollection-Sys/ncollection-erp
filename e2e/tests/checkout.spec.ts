import { test, expect, APIRequestContext } from '@playwright/test';
import { TENANTS, authenticate, callKw } from '../fixtures/tenants';

/**
 * P2-T18 gate — the public self-service checkout journey (P2-T16), the platform's
 * front door. Exercised against the e2eadmin platform DB (ncollection_saas + a
 * seeded `E2ESTARTER` plan; see setup_e2e_tenants.sh).
 *
 * The FULL live flow (email verification → provisioning → login-ready → Stripe
 * payment) needs the staging VPS + a mailer + the queue runner; here we prove the
 * deterministic public entrypoint: subdomain availability, and register → a DRAFT
 * tenant + subscription (no provisioning), then clean up so re-runs stay idempotent.
 */

const PLATFORM = TENANTS.e2eadmin;

async function rpc(request: APIRequestContext, path: string, params: Record<string, unknown>) {
  const res = await request.post(`${PLATFORM}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    data: { jsonrpc: '2.0', method: 'call', params },
  });
  return (await res.json()).result as any;
}

test.describe('public checkout journey (P2-T16/T18)', () => {
  test('subdomain availability: a free name is available, an existing tenant is not', async ({ request }) => {
    const free = `e2echk${Date.now()}`;
    const ok = await rpc(request, '/nc/checkout/availability', { subdomain: free });
    expect(ok.available, `free '${free}' should be available`).toBe(true);

    const taken = await rpc(request, '/nc/checkout/availability', { subdomain: 'e2eclienta' });
    expect(taken.available, "an existing tenant DB name must NOT be available").toBe(false);
  });

  test('register creates a DRAFT tenant + subscription; status is not-provisioned', async ({ request }) => {
    const sub = `e2echk${Date.now()}`;
    const reg = await rpc(request, '/nc/checkout/register', {
      company: 'E2E Checkout Co', contact: 'Tester', email: `${sub}@example.com`,
      subdomain: sub, plan: 'E2ESTARTER', cycle: 'monthly',
    });
    expect(reg.success, `register failed: ${JSON.stringify(reg)}`).toBe(true);
    expect(reg.tenant_uuid).toBeTruthy();

    // The progress page polls this until provisioning completes — at draft it is
    // not yet provisioned and the email is unverified.
    const st = await rpc(request, '/nc/checkout/status', { tenant_uuid: reg.tenant_uuid });
    expect(st.status).toBe('not_provisioned');
    expect(st.verified).toBe(false);

    // Confirm the platform-side records, then clean up (idempotent re-runs).
    await authenticate(request, 'e2eadmin', 'admin', 'admin');
    const found = await callKw(request, 'e2eadmin', 'ncollection.tenant', 'search_read',
      [[['database_name', '=', sub]], ['id', 'status', 'database_status']], { limit: 1 });
    const rec = (found.result ?? [])[0];
    expect(rec?.status, 'tenant should be created in trial/draft').toBe('trial');
    expect(rec?.database_status).toBe('not_provisioned');
    await callKw(request, 'e2eadmin', 'ncollection.tenant', 'unlink', [[rec.id]]);
  });

  test('register rejects missing required fields (before any side effect)', async ({ request }) => {
    const r = await rpc(request, '/nc/checkout/register', { company: '', email: '' });
    expect(r.success).toBe(false);
    expect(r.error).toBe('missing_fields');
  });
});
