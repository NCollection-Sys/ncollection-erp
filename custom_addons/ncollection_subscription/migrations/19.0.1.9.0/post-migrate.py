# -*- coding: utf-8 -*-
"""Platform administrators of tenants (Enter Company): keep today's access.

Until this version every platform administrator could administer every tenant. The allow-list
(`ncollection.tenant.platform_admin_ids`) starts EMPTY, which would silently lock them all out, so the
upgrade authorises the platform administrators that exist today on the tenants that exist today ONCE.
Afterwards the list is authoritative and is edited on the tenant form. Idempotent: it only adds missing
pairs and never removes anyone. "Platform administrator" = a member of the platform group itself or of a
group that implies it (Settings / Administration does).
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        WITH platform AS (
            SELECT res_id AS gid FROM ir_model_data
             WHERE module = 'ncollection_subscription' AND name = 'group_platform_admin' AND model = 'res.groups'
        ), granting AS (
            SELECT gid FROM platform
            UNION
            SELECT i.gid FROM res_groups_implied_rel i JOIN platform p ON p.gid = i.hid
        )
        INSERT INTO ncollection_tenant_platform_admin_rel (tenant_id, user_id)
        SELECT t.id, u.id
          FROM ncollection_tenant t
          JOIN res_users u ON u.active AND NOT u.share AND u.id > 1
         WHERE EXISTS (SELECT 1 FROM res_groups_users_rel r JOIN granting g ON g.gid = r.gid WHERE r.uid = u.id)
        ON CONFLICT DO NOTHING
    """)
