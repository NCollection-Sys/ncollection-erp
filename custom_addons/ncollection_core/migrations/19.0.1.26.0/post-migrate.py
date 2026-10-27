# -*- coding: utf-8 -*-
"""Service accounts stop carrying the old vendor name.

The scheduler account ("NCollection Scheduler") and the two service groups were seeded with the vendor's name in a
noupdate block, so an upgrade never renamed them, and the account's contact showed up wherever contacts are listed
(it was picked as an invoice's customer). They get neutral service names; only a name still equal to the shipped one
is changed, the logins and groups are untouched, so every scheduled job and the config-sync service keep working.
Idempotent.
"""
from odoo import SUPERUSER_ID, api

RENAMES = (
    ('ncollection_core.user_cron_service', 'NCollection Scheduler', 'Scheduler (service)'),
    ('ncollection_core.group_cron_service', 'NCollection Scheduler (service)', 'Scheduler (service)'),
    ('ncollection_core.group_config_sync', 'NCollection Config Sync (service)', 'Config Sync (service)'),
)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {'lang': 'en_US'})
    for xmlid, old, new in RENAMES:
        record = env.ref(xmlid, raise_if_not_found=False)
        if record and record.name == old:
            record.name = new
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module']._load_module_terms(['ncollection_core'], langs, overwrite=True)
