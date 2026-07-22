# syntax=docker/dockerfile:1
# ============================================================================
#  NCollection ERP — deployable application image  (ticket P2-T07)
# ============================================================================
#  Bakes OUR addons + the pinned OCA tree + the production config INTO the
#  image, so staging/production deploy an immutable, tagged artifact instead of
#  bind-mounting a working copy. (Dev still bind-mounts — see the base
#  docker-compose.yml; nothing here changes the dev workflow.)
#
#  BUILD-CONTEXT REQUIREMENT: ./oca is GENERATED, not committed (P1-T04). It
#  MUST be aggregated before building or the `COPY oca` below fails loudly:
#      locally :  make oca
#      in CI   :  the deploy-staging workflow runs gitaggregate first.
#
#      docker build -t ghcr.io/ncollection-sys/ncollection-erp:<tag> .
#
#  DB secrets are NEVER baked: the base image entrypoint appends
#  --db_host/--db_user/--db_password from the HOST/USER/PASSWORD env vars at
#  runtime (supplied from the server's .env).
# ============================================================================
FROM odoo:19

# Our addons + the pinned OCA repos + the multi-tenant production config.
# Odoo core addons stay where the base image already put them. The prod config
# carries addons_path (/mnt/extra-addons + each OCA repo), db_filter, workers.
COPY --chown=odoo:odoo custom_addons /mnt/extra-addons
COPY --chown=odoo:odoo oca /mnt/oca-addons
COPY --chown=odoo:odoo config/odoo.prod.conf /etc/odoo/odoo.prod.conf

# Odoo's built-in no-database health route — 200 OK once the server is live.
# Compose/orchestrators reuse this; deploy smoke tests hit the same path.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=60s \
    CMD curl -sf http://localhost:8069/web/health || exit 1

# `odoo` keeps the base entrypoint (it injects DB_ARGS from env, then execs
# `odoo -c /etc/odoo/odoo.prod.conf ${DB_ARGS}`).
CMD ["odoo", "-c", "/etc/odoo/odoo.prod.conf"]
