# -*- coding: utf-8 -*-
"""#476 — carry the dead `domain` Char over to the real `subdomain` field.

`ncollection.tenant.domain` was a free-text Char that NOTHING in the codebase
read: an operator could type anything into it and it changed nothing. The real
hostname was built by `ncollection.domain._fqdn_for_tenant()` from
`database_name`. It is replaced by `subdomain`, which the FQDN builder now
actually reads.

Some records nevertheless hold a meaningful value — the public checkout wrote
the chosen subdomain into it, and the demo data carried full hostnames — so the
column is HARVESTED before it is dropped rather than simply discarded:

  * `atlas`                 -> taken as-is
  * `atlas.ncollectionerp.com` -> first label taken
  * anything that is not a legal label -> left empty, which is safe because
    `_nc_subdomain_label()` falls back to `database_name`, exactly what the old
    FQDN builder used.

Runs PRE-migration so it reads the old column before Odoo drops it. Idempotent:
it only fills `subdomain` where it is still NULL.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'ncollection_tenant' AND column_name = 'domain'
    """)
    if not cr.fetchone():
        return  # already dropped (a re-run, or a fresh install)

    cr.execute("""
        ALTER TABLE ncollection_tenant
          ADD COLUMN IF NOT EXISTS subdomain VARCHAR
    """)
    # split_part on '.' takes the host label; the regex then discards anything
    # that is not a legal one, leaving NULL -> falls back to database_name.
    cr.execute(r"""
        UPDATE ncollection_tenant
           SET subdomain = lower(split_part(trim(domain), '.', 1))
         WHERE subdomain IS NULL
           AND domain IS NOT NULL
           AND lower(split_part(trim(domain), '.', 1)) ~ '^[a-z][a-z0-9]{2,62}$'
    """)
