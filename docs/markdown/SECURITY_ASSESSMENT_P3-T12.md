# Pre-Launch Security Assessment (P3-T12)

**Date:** 2026-07-25 · **Assessor:** DEV-1 (internal red-team) · **Scope:** the
NCollection multi-tenant SaaS platform, pre-production, before any real tenant
data arrives. Fulfils the ARCHITECTURE_SECURITY §12 *Phase 3 / go-live* gate
(pre-prod half; production-only items carried to **P3-T13**).

## Verdict

The assessment **found 2 CRITICAL + 2 HIGH** — exactly what a pre-launch
red-team exists to surface. Remediation status:

| Finding | Severity | Status |
|---|---|---|
| **C-1** public self-signup enabled in every tenant | 🔴 CRITICAL | ✅ **fixed in this PR** |
| **ISO-1** cross-tenant config-sync takeover via `database_name` | 🔴 CRITICAL | ✅ **closed in #225** — `UNIQUE(database_name)` + write-guard fix + model-level grammar/blocklist guard (auditor-proven vs ORM + raw SQL); residual status-transition/ownership defense-in-depth → #228 |
| **H-1** no edge rate-limit on checkout; reCAPTCHA off | 🟠 HIGH | ✅ **fixed in this PR** (+ reCAPTCHA = P3-T13 gate) |
| **ISO-2** restore-drill can clobber a live tenant DB | 🟠 HIGH | ✅ **fixed in this PR** |

**The ISO-1 takeover is closed by #225** (the load-bearing `UNIQUE(database_name)`
constraint makes it physically impossible for two records to claim one DB — proven
against ORM create/write and raw SQL). Remaining ISO-1 items (a status-transition
guard + a now-redundant config-sync ownership check) are **defense-in-depth in
#228**, not go-live blockers. P3-T12 delivers the
assessment + three of the four crit/high fixes; ISO-1 is a substantial platform-
model change (unique constraint + migration + `@api.constrains` + config-sync
ownership) that gets its own focused, reviewed PR — a blocking input to the
P3-T13 go-live gate. MEDIUM/LOW residuals: **#226**.

## 1. Methodology

Internal red-team, attacker's-eye, layered against ARCHITECTURE_SECURITY.md:

1. **Automated pre-prod probe** — [`scripts/audit/phase3_security_assessment.sh`](../../scripts/audit/phase3_security_assessment.sh)
   (`make security-assess`): headers, TLS/HSTS, DB-manager block, public-endpoint
   abuse-resistance, secrets scan, dependency review.
2. **Cross-tenant proof** — [`scripts/audit/phase1_security_audit.sh`](../../scripts/audit/phase1_security_audit.sh)
   + `make verify-all` (routing/isolation · provisioning · config-sync · e2e).
3. **OWASP code deep-dive** — `security-reviewer` on the public attack surface
   (checkout + signup), `tenant-isolation-auditor` on the full platform model.

## 2. §12 Phase-3 checklist

| Item | Result |
|---|---|
| OWASP probing of login/checkout/portal | ✅ done — findings §4–5 (crit/high remediated or tracked) |
| Security headers audit | ✅ X-Frame-Options · nosniff · Referrer-Policy · CSP present |
| TLS / HSTS / HTTP→HTTPS | ✅ prod nginx: 301 · TLS 1.2/1.3 · HSTS |
| SSL Labs grade A | ⏭️ **deferred to P3-T13** (needs the live TLS endpoint) |
| Dependency scan review | ✅ 0 open crit/high; 3 moderate (react-router on `main`, fixed on develop) |
| Secrets audit | ✅ no secrets in tracked files; `.env` gitignored |
| Isolation suite (7 guarantees) | ✅ verify-all + phase1 audit (§3) |
| License enforcement (URL + RPC) | ✅ verify-all e2e + phase1 audit |
| Auth hardening (lockout/timeout/cookie/CSRF) | ✅ (§4) |
| Incident runbook + on-call + breach tree | ⏭️ **deferred to P3-T13** |
| Restore drill ON PRODUCTION | ⏭️ **deferred to P3-T13** |
| Rate limits load-tested end-to-end | ⏭️ partial (P3-T03 dev baseline; full at P3-T13) |

## 3. Automated evidence

- **`make security-assess`** → **14 passed · 0 failed · 4 known/deferred**:
  headers present; prod TLS/HSTS/redirect configured; DB-manager
  `/web/database/{manager,selector,create}` → **403** at the edge; `availability`
  sanitizes a malicious subdomain (no 5xx/stack leak); secrets scan clean; 0
  open crit/high Dependabot alerts.
- **`make verify-all`** → **ALL GREEN**: routing **8/8** (isolation), provisioning
  **10/10**, config-sync **7/7**, e2e **12/12** (auth/license/roles/visibility).
- **`phase1_security_audit.sh`** (live during audit) → cross-tenant RPC/session
  isolation, license Ring-2 ORM denial, 8-role matrix **8/8**, db-manager edge block.

## 4. Public attack surface — OWASP deep-dive (checkout / signup)

**C-1 (CRITICAL, FIXED).** `POST /ncollection/api/signup` (`ncollection_core`,
`auth='public'`) created an Internal User with no gatekeeping. `ncollection_core`
ships in every tenant (`CORE_TENANT_MODULES`), so a stranger could create a user
in any tenant's live ERP and bypass the Owner-only invite wizard + (via `.sudo()`)
the seat limit. **Fix:** the route is now **disabled by default** behind
`ir.config_parameter ncollection_core.public_signup_enabled` (secure by absence);
only the demo/dev DB enables it. Regression test: `TestPublicSignupGate`.

**H-1 (HIGH, FIXED).** The prod edge rate-limited only `/web/login`; the checkout
funnel had none, and reCAPTCHA seeds empty (fails open). **Fix:** added `limit_req`
zones to `nginx/conf.d/ncollection.prod.conf` — `checkout` (6 r/m) on
`/nc/checkout/register` + `/ncollection/api/signup` + `/nc/checkout/verify/<token>`
(the GET that actually fires `CREATE DATABASE`), `onboard` (60 r/m) on
`/nc/checkout/availability` + `/pricing`. **reCAPTCHA must be enabled at go-live**
— a P3-T13 gate item. Note: the C-1 flag also disables demo signup by default; the
demo bootstrap must enable it (tracked in #226).

**MEDIUM/LOW** (→ **#226**): `availability` still lacks an app-level per-IP cap +
opens a psycopg2 connection per call (M-1); no length caps on checkout free-text
(M-3); `GET /nc/checkout/register` returns 500 not 405 (LOW hygiene — no
stack-leak, rejected before the handler runs).

**Accepted (no action):** `/nc/checkout/status|pending` keyed by a random UUIDv4
capability token (2¹²² keyspace, no listing endpoint) — not IDOR; jsonrpc CSRF
exemption is safe (no session-scoped identity to forge; JSON body forces preflight);
`availability` name-existence disclosure is the standard SaaS signup pattern.

**Confirmed SOLID:** provisioning SQL fully parameterized (`%s` / `psycopg2.sql.Identifier`,
no `shell=True`); db-name validated 3× against `^[a-z][a-z0-9]{2,62}$`; email
verification truly gates provisioning; rollback only drops self-created DBs;
QWeb escaping clean (no `t-raw`); reCAPTCHA fails-closed when configured;
`provisioning_quota_per_hour` is a real hard ceiling.

## 5. Tenant isolation

**ISO-1 (CRITICAL, CLOSED in #225).** A lesser-privileged *Platform Admin* could
point a `not_provisioned` tenant record at **another tenant's `database_name`** and
drive config-sync to it (suspend the victim's users / revoke their apps). Root
causes: no uniqueness constraint on `database_name`; the immutability guard read the
*pre-write* `database_status` so a combined `write({database_name,
database_status:'ready'})` bypassed it; and config-sync derives the bearer purely
from the db-name string. **Closed in #225** by a `UNIQUE(database_name)` constraint
(via Odoo-19 `models.Constraint`) + a write-guard that now evaluates the post-write
status + a model-level `@api.constrains` enforcing the db-name grammar and the
reserved/blocklist + platform-db-name policy at provisioning/ready (on create and
write) + a pre-migration nulling invalid names — the isolation auditor confirmed the
unique constraint holds against ORM create/write AND raw SQL, so the takeover is
structurally impossible. A status-transition guard + a (now-redundant) config-sync
ownership check remain as defense-in-depth in **#228**.

**ISO-2 (HIGH, FIXED).** The unattended monthly `_cron_restore_drill`
(`backup.py`) did `dropdb`/`createdb` on `drill_<name>` with no live-tenant
collision guard (the interactive wizard has one) — a "ready" tenant named
`drill_<other>` could be overwritten. **Fix:** the cron now mirrors the wizard's
guard — if the scratch target collides with a live tenant it **skips + alerts**
instead of destroying it. Regression test:
`TestBackup.test_restore_drill_skips_live_tenant_collision`.

**Confirmed SOLID (live):** cross-tenant RPC/session isolation (phase1 §A +
routing 10/10); license Ring-2 ORM denial; 8-role matrix 8/8; DB-manager blocked
on every subdomain; config-sync #212 per-tenant KDF 7/7 (the KDF is sound —
ISO-1 is a *different* bug, no ownership check on which record may target a db);
no cross-DB ORM/SQL cursor (Rule 3) — every `psycopg2`/`env.cr` use is either the
`postgres` maintenance DB or a single-tenant subprocess; fixture ownership (R-004).

## 6. Deferred to P3-T13 (production-dependent)

SSL Labs grade-A (live TLS endpoint); restore drill on production infra; on-call
rotation + breach-notification tree; end-to-end rate-limit load test on prod.
(ISO-1 defense-in-depth → #228, non-blocking.)

## 7. Sign-off

Pre-production assessment complete. **All four crit/high remediated** — C-1/H-1/ISO-2
in the P3-T12 PR, **ISO-1 closed in #225** (its takeover is structurally impossible
via the `UNIQUE(database_name)` constraint). Remaining go-live items are the
production-dependent §6 list; ISO-1 defense-in-depth is tracked non-blocking in #228.
Re-run `make security-assess` + `make verify-all` before go-live.
