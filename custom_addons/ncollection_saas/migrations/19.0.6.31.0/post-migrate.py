# -*- coding: utf-8 -*-
"""Custom tenant domains: existing records are platform subdomains, already serving their tenant.

The new ``state`` column is filled with its default ('active') by the ORM; this makes it explicit for any row the
default did not reach and marks each tenant's subdomain as its primary address (every tenant had exactly one
address until now). Idempotent: a re-run changes nothing. No tenant, database or hostname changes.
"""


def migrate(cr, version):
    cr.execute("UPDATE ncollection_domain SET state = 'active' WHERE state IS NULL AND domain_type = 'subdomain'")
    cr.execute("""
        UPDATE ncollection_domain d SET is_primary = TRUE
         WHERE d.domain_type = 'subdomain' AND d.is_primary IS NOT TRUE
           AND NOT EXISTS (SELECT 1 FROM ncollection_domain o WHERE o.tenant_id = d.tenant_id AND o.is_primary)
    """)
