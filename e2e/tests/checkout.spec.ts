import { test, expect, APIRequestContext } from '@playwright/test';
import { TENANTS, authenticate, callKw } from '../fixtures/tenants';

/**
 * P2-T18 gate — the public self-service checkout journey (P2-T16), the platform's
 * front door. Exercised against the e2eadmin platform DB (ncollection_saas + a
 * seeded `E2ESTARTER` plan; see setup_e2e_tenants.sh).
 *
 * The FULL live flow (approval → provisioning → login-ready → Stripe payment)
 * needs the staging VPS + a mailer + the queue runner; here we prove the
 * deterministic public entrypoint: subdomain availability, and register → a
 * PENDING-APPROVAL request (no database, no provisioning, a USERNAME as the
 * login, the password stored only as a hash), then clean up so re-runs stay
 * idempotent.
 */

const PLATFORM = TENANTS.e2eadmin;

/** Credentials every registration now carries. The login is a USERNAME, never an email. */
const CREDS = { username: 'e2eowner', password: 'E2e-Owner#2026', password_confirm: 'E2e-Owner#2026' };

/** POST a jsonrpc call to a public checkout route and return the typed `result`. */
async function rpc<T = unknown>(
  request: APIRequestContext,
  path: string,
  params: Record<string, unknown>,
): Promise<T> {
  const res = await request.post(`${PLATFORM}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    data: { jsonrpc: '2.0', method: 'call', params },
  });
  return (await res.json()).result as T;
}

test.describe('public checkout journey (P2-T16/T18)', () => {
  test('subdomain availability: a free name is available, an existing tenant is not', async ({ request }) => {
    const free = `e2echk${Date.now()}`;
    const ok = await rpc<{ available: boolean }>(request, '/nc/checkout/availability', { subdomain: free });
    expect(ok.available, `free '${free}' should be available`).toBe(true);

    const taken = await rpc<{ available: boolean }>(request, '/nc/checkout/availability', { subdomain: 'e2eclienta' });
    expect(taken.available, "an existing tenant DB name must NOT be available").toBe(false);
  });

  test('register creates a PENDING-APPROVAL request; nothing is provisioned', async ({ request }) => {
    const sub = `e2echk${Date.now()}`;
    try {
      const reg = await rpc<{ success: boolean; error?: string; tenant_uuid?: string }>(
        request, '/nc/checkout/register', {
          company: 'E2E Checkout Co', contact: 'Tester', email: `${sub}@example.com`,
          subdomain: sub, plan: 'E2ESTARTER', cycle: 'monthly', ...CREDS,
        });
      expect(reg.success, `register failed: ${JSON.stringify(reg)}`).toBe(true);
      expect(reg.tenant_uuid).toBeTruthy();

      // The progress page polls this until provisioning completes — at draft it is
      // not yet provisioned and the email is unverified.
      const st = await rpc<{ status: string; stage: string; verified: boolean; login_url?: string }>(
        request, '/nc/checkout/status', { tenant_uuid: reg.tenant_uuid });
      expect(st.status).toBe('not_provisioned');
      expect(st.stage, 'a new registration awaits approval').toBe('pending');
      expect(st.verified).toBe(false);
      expect(st.login_url, 'no workspace exists to log in to yet').toBeUndefined();

      // Confirm the platform-side records were created as a draft trial tenant.
      await authenticate(request, 'e2eadmin', 'admin', 'admin');
      const found = await callKw(request, 'e2eadmin', 'ncollection.tenant', 'search_read',
        [[['database_name', '=', sub]],
         ['id', 'status', 'database_status', 'registration_state', 'admin_login', 'email']], { limit: 1 });
      const rec = (found.result ?? [])[0];
      expect(rec?.status, 'tenant should be created in trial/draft').toBe('trial');
      expect(rec?.database_status).toBe('not_provisioned');
      expect(rec?.registration_state, 'waits for an administrator').toBe('pending');
      expect(rec?.admin_login, 'the login is the username').toBe(CREDS.username);
      expect(rec?.admin_login).not.toBe(rec?.email);
    } finally {
      // Guaranteed teardown by the unique subdomain, wherever a failure hit. A
      // leaked `status='trial'` row counts against the per-IP trial quota
      // (3 / 24h, checkout._nc_trial_quota_exceeded), so an abort here would turn
      // one flaky run into a compounding false failure of the gate — clean up
      // regardless of assertion outcome, not only on the happy path.
      await authenticate(request, 'e2eadmin', 'admin', 'admin');
      const leftover = await callKw(request, 'e2eadmin', 'ncollection.tenant', 'search',
        [[['database_name', '=', sub]]]);
      const ids = (leftover.result ?? []) as number[];
      if (ids.length) {
        await callKw(request, 'e2eadmin', 'ncollection.tenant', 'unlink', [ids]);
      }
    }
  });

  test('an email address is refused as the username (never silently converted)', async ({ request }) => {
    const r = await rpc<{ success: boolean; error?: string }>(
      request, '/nc/checkout/register', {
        company: 'E2E Email Login', email: 'x@example.com', subdomain: `e2eeml${Date.now()}`,
        plan: 'E2ESTARTER', ...CREDS, username: 'x@example.com',
      });
    expect(r.success).toBe(false);
    expect(r.error).toBe('username_is_email');
  });

  test('mismatched or short passwords are refused', async ({ request }) => {
    const base = { company: 'E2E Pw', email: 'pw@example.com', plan: 'E2ESTARTER', ...CREDS };
    const mismatch = await rpc<{ success: boolean; error?: string }>(
      request, '/nc/checkout/register',
      { ...base, subdomain: `e2epwa${Date.now()}`, password_confirm: 'Different#2026' });
    expect(mismatch.error).toBe('password_mismatch');
    const short = await rpc<{ success: boolean; error?: string }>(
      request, '/nc/checkout/register',
      { ...base, subdomain: `e2epwb${Date.now()}`, password: 'short', password_confirm: 'short' });
    expect(short.error).toBe('password_too_short');
  });

  test('register rejects missing required fields (before any side effect)', async ({ request }) => {
    const r = await rpc<{ success: boolean; error?: string }>(
      request, '/nc/checkout/register', { company: '', email: '' });
    expect(r.success).toBe(false);
    expect(r.error).toBe('missing_fields');
  });
});
