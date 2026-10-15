# -*- coding: utf-8 -*-
"""Give a company's creator access to it when nobody was given access (My Companies).

Until this version a company created from the platform form was saved with an EMPTY allow-list
(`ncollection.tenant.platform_admin_ids`), because the form posts the field empty and the "creator may enter it"
default only applied when the key was absent. Such a company was provisioned and ready but listed for nobody and
enterable by nobody. This adds the creating user, and only where all three hold: the allow-list is empty, the creator
is a real platform administrator, and the creator is not the technical superuser. Idempotent (it only fills an
EMPTY list, so a second run, or a list an administrator emptied on purpose and refilled, is untouched).
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
        SELECT t.id, t.create_uid
          FROM ncollection_tenant t
          JOIN res_users u ON u.id = t.create_uid AND u.active AND NOT u.share AND u.id > 1
         WHERE NOT EXISTS (SELECT 1 FROM ncollection_tenant_platform_admin_rel r WHERE r.tenant_id = t.id)
           AND EXISTS (SELECT 1 FROM res_groups_users_rel g JOIN granting x ON x.gid = g.gid WHERE g.uid = u.id)
        ON CONFLICT DO NOTHING
    """)
