NCollection SaaS Admin
======================

The platform (admin-DB) SaaS layer. P2-T01 adds the **tenant provisioning
engine**: it turns a queued ``ncollection.provisioning.job`` into a login-ready
tenant database, or rolls back cleanly.

Engine (``ncollection.provisioning.job`` inherited)
---------------------------------------------------

``action_run`` enqueues the job on the OCA ``queue_job`` channel
``root.provisioning`` so it runs in the isolated **provisioning-runner**
container, OFF the HTTP workers (``docker-compose.saas.yml``). ``action_run_sync``
runs the same engine inline (button / tests). Steps:

1. **validate** the DB name — regex + reserved-word list + collision check
   (ARCHITECTURE_SECURITY §11),
2. **create** the DB in an isolated ``odoo`` subprocess (base + ncollection_core
   + ncollection_branding + the plan's modules),
3. **seed** the tenant in an isolated ``odoo shell`` subprocess — admin with a
   forced password reset, the ``ncollection.workspace.config`` projection,
   branding,
4. on any failure, **roll back** (drop the DB) so no zombie is left.

Isolation
---------

The admin process never opens an ORM cursor on a tenant DB (Rule 3); all tenant
writes go through subprocesses, and rollback uses a direct ``psycopg2``
maintenance connection. A per-hour quota
(``ncollection_saas.provisioning_quota_per_hour``) caps DB-creation load.

See ``docs/markdown/PROVISIONING.md`` for the full flow, the OCA decision, and
the CI-vs-local test split.
