# Platform Administrator → Tenant ("Enter Company")

A Platform Administrator manages many tenants from one platform account. This system is **database-per-tenant**, so
"entering" a tenant is **not** a company switch inside one database and it **never replays a password**.

```
Platform Summary ── My Companies ── [Enter Company]
        │  (server checks: platform admin group AND on this tenant's allow-list AND tenant ready)
        ▼
ncollection_saas  ── one-time token; only its SHA-256 is pushed ──►  tenant DB: ncollection.platform.entry
        │                  (existing per-tenant HMAC-derived service channel, json2)
        ▼  browser → https://<tenant>/ncollection/platform/enter?token=…
tenant: redeem once (atomic UPDATE … RETURNING) → sign in as the admin's passwordless proxy user
        ▼
Atlas Summary ── "Atlas · Platform Admin Mode · <name> · Back to Platform"
```

## Invariants

| Rule | Where it is enforced |
|---|---|
| Only platform administrators; only tenants **explicitly** listed on `ncollection.tenant.platform_admin_ids` | `ncollection_saas/models/platform_entry.py` `action_enter_company` (server-side, no sudo) |
| The target database is `tenant.database_name` from the registry — never a value the caller sends; must match `^[a-z][a-z0-9]{1,62}$` | same method; tests `test_the_target_database_comes_from_the_registry…` |
| No password is stored, sent or replayed. The proxy user has none and a password credential is refused | `ncollection_core/models/res_users.py` `_check_credentials` |
| Token: 256-bit random, single use, ≤ 120 s, only its hash reaches the tenant, redeemed atomically | `ncollection_core/models/platform_entry.py` `_redeem` |
| Tenant answers every refusal with the same bare 403 | `ncollection_core/controllers/platform_entry.py` |
| Cross-tenant: A's session is not valid on B, B's token is refused by A, an unregistered host opens nothing | `scripts/provisioning/verify_platform_entry.sh` (22 checks, two real databases) |
| Attribution: inside a tenant every write is made by `Platform Admin: <name>` (create_uid/write_uid/chatter), never an anonymous Admin; the platform records who entered, in the tenant's chatter | `platform_entry.py` (platform side), proxy user (tenant side), `ncollection.platform.entry` rows (`used_at`, `user_id`, `remote_ip`) |
| The proxy is not a customer seat and its session ends 8 h after entry (the session token stops matching) | `_ncollection_check_user_limit`, `ResUsers._compute_session_token` |
| MFA is **not** skipped for the proxy (`mfa: default`) | `_check_credentials` |
| Tenant users cannot reach Platform Administration: the platform modules are not installed in tenant databases | `verify_platform_entry.sh` check 8 |

## Upgrade

`ncollection_subscription` 19.0.1.9.0 adds the allow-list. Its post-migration authorises today's platform
administrators on today's tenants **once** (so nobody loses access); afterwards the list is authoritative. Tenant
databases need `-u ncollection_core` (new column on `res.users` and the entry model); `ncollection_sale_attachments`
installs itself next to `sale`.

## Known limits

* Platform → tenant reachability is the existing service channel: a tenant that is down cannot be entered.
* Removing an administrator from the allow-list stops *new* entries immediately; an open tenant session ends at the
  8-hour window, or when they use **Back to Platform**.
* The one-time token travels in the `GET /ncollection/platform/enter?token=` query string (a browser navigation
  cannot carry a header). It is single-use, lives at most 120 s, only its SHA-256 is stored, and the redirect answers
  `Referrer-Policy: no-referrer`; it can still appear in front-end access logs, where it is already worthless.
* The service account that registers grants is the existing config-sync account. Its extra right is **create** on
  `ncollection.platform.entry` only (no read, write or delete); it is wider than `workspace.config` in that it
  can *register* an entry for any platform administrator ref, which is why the platform checks the per-tenant
  allow-list first and the tenant validates the shape of every grant.
* The proxy user is not deactivated after the window; its session token simply stops matching, and it holds no password.
* The Summary page runs the Balance Sheet and Profit & Loss engines once each per load (aggregate queries, not
  per-row); it is not cached.
