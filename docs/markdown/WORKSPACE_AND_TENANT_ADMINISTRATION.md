# Workspace Administration and Tenant Administration

Two screens, one rule set. **Workspace Administration** is what a company's own administrator uses inside their workspace.
**Tenant Administration** is what a Platform Administrator uses, from the platform, on one company's workspace. Both end in the
same methods on `res.users` inside the tenant database, so a rule exists once.

```
Platform (ncollection_saas)                       Tenant database (ncollection_core)
 Tenants > <company> > Users & Access
   ncollection.tenant._nc_admin_call  ──JSON-2──▶  ncollection.admin.service  (config-sync service account only)
   (authorise: group + allow-list + ready)           │  fixed keys, managed accounts only, superuser for ONE operation
                                                      ▼
 Workspace Administration (owner) ───────────────▶  res.users.nc_set_roles / nc_reset_password / nc_set_active
                                                    ncollection.role (a VIEW of real res.groups)
                                                    ncollection.auth.log (audit, no secrets)
```

## Roles and permissions: no engine of our own

`ncollection.role` is a translated name for a set of **real `res.groups`**. What a person may do is decided by those groups,
their `ir.model.access` rows and record rules, exactly as before.

| | Standard role | Custom role |
|---|---|---|
| Defined by | the product (`data/role_templates.xml`) | the workspace administrator |
| Groups | external ids, resolved at use (`group_xmlids`) | `custom_group_ids` |
| Application missing in this workspace | `available = False`, `missing_apps` says which | n/a |
| May grant administration | only `administrator` | never (`FORBIDDEN_GROUPS` + the whole implied closure) |

* Holding a role = holding **all** of its groups (`all_group_ids`). `res.users.nc_role_ids` is *computed* from the groups; its inverse
  adds / removes real groups. There is no stored assignment that could drift from what Odoo enforces.
* The role shown for a person is the most specific one (Sales Manager covers Sales User).
* Removing a role removes the groups only that role justified; groups another held role needs stay.
* Guards (in `res.users._nc_apply_roles`): only an administrator gives or takes administration; nobody hands out a group they do not
  hold; the last Administrator can never be removed or deactivated; roles apply to the workspace's own users only (not portal,
  not the platform proxy).
* The **access matrix** ("What this role allows") is read from `ir.model.access` when opened and never stored, so it is what Odoo enforces.
  There are no checkboxes that "change" a permission: rights come from groups.
* Standard roles shipped: Administrator, Finance Manager, Accountant, Sales Manager / User, Purchase Manager / User,
  Inventory Manager / User, HR Manager / User, CRM Manager / User, Reports Viewer. The eight legacy roles (`group_role_*`,
  `docs/ROLE_MATRIX.md`) are unchanged; the standard roles are built on them.

## Passwords

* The platform stores none. A **temporary** password is generated inside the tenant (`secrets`, 16 characters, no look-alikes)
  and returned once to the administrator who asked; a **typed** password travels in the request body to the tenant and is cleared
  from the dialog row before the call (like Odoo's own Change Password dialog).
* The tenant stores Odoo's hash. Neither password is written to a log, an audit row, the chatter or any record of ours
  (asserted by tests and by the live check, which searches the databases for the value).
* A reset sets `nc_must_change_password`. `ir.http._authenticate` then sends every web page to `/ncollection/password/change`
  and refuses every RPC route until the person has chosen their own password (they must give the one they were handed). The session is
  ended afterwards. Bearer-token API clients (`ncollection_api`) are not session users and are unaffected.
* A platform administrator's *entry* session (the passwordless proxy) cannot set passwords inside the workspace
  (`res.users.write` guard): the platform's Tenant Administration is the way.

## Tenant Administration (platform)

`ncollection.tenant` form pages: Overview, Subscription, **Users & Access**, Platform Access, System, Modules, Configuration Sync.

* **Authorisation, on every call, server-side:** platform administrator group **and** on this tenant's `platform_admin_ids` allow-list
  (the list that already governs Enter Company) **and** the tenant database is `ready`. The database name is the registry's, never the caller's.
* **Transport:** the existing per-tenant derived bearer over JSON-2 (`derive_tenant_key`), `Host: <db>.<base domain>`. The service
  account stays least-privilege (config only): it may call the methods of `ncollection.admin.service` and nothing else; those methods
  accept a fixed set of keys, refuse service / cron / proxy / portal accounts and run as the superuser for that one operation
  (the documented platform -> tenant elevation). The seat limit, last-Administrator rule and forbidden groups still apply because they live
  in `res.users` / `ncollection.role`, not in the caller.
* **Identification of the Tenant Administrator:** the login recorded at provisioning (`admin_login`) is flagged in the user list, with
  *Reset password* and *Roles* on its row, and a *Reset administrator password* button on the page.
* **Audit:** the platform writes a chatter note (who, what, never a secret); the tenant writes `ncollection.auth.log` rows with
  `actor_ref = platform:u<id> (<name>)`, the target user and a short detail. `actor_ref` is honoured only when the environment was
  elevated by code, so a client-sent context cannot forge it.

## Workspace Administration (tenant)

Menu root `Workspace Administration` (administrators only): Overview, Users, Invite User, Roles & Permissions, Teams & Departments,
Security, Company, Appearance, Document Stamps, System. Every screen reads real records.

* Users: list/form with role, last sign-in, "must change password"; reset password, activate / deactivate (existing guards), roles.
* Security: the audit log (`ncollection.auth.log`): sign-ins, password events, role and user changes, platform entry / exit / refusal.
* System: the plan and licence, **read only** (the licence is the platform's decision; see `ncollection.workspace.config.write`).
* Teams & Departments: opens HR's departments when HR is part of the workspace; says so when it is not (a foundation, not a second
  organisation model).
* A company administrator cannot manage other tenants, cannot widen the licence, and cannot create platform roles.

## Verified how

`ncollection_core/tests/test_workspace_admin.py`, `test_admin_service.py`, `test_forced_password_change.py`;
`ncollection_saas/tests/test_tenant_admin.py`; `ncollection_saas/scripts/provisioning/verify_tenant_admin.sh` (a really provisioned
tenant, real HTTP, 30 checks including "the temporary password signs in, is then forced to change, and no table of either database holds it");
Playwright specs under `e2e/tests/`.

## Known limits

* A typed password is held in a transient row between the form save and the button; it is cleared first thing in the action.
* The temporary password is shown in a sticky notification: it can be read once, then it is gone (there is no "copy" of it anywhere).
* Departments / teams are HR's; there is no organisation tree of our own.
