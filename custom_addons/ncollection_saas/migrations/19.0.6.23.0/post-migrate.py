# -*- coding: utf-8 -*-
"""The platform database stops sending as noreply@ncollection.com.

ncollection_branding used to seed ``mail.default.from`` = noreply@ncollection.com (and its from-filter) on every
database, the platform included. Tenants are cleaned when the platform pushes their company identity; the platform is
not a tenant, so it is cleaned here, by the same function: only a value still equal to the shipped placeholder is
removed. Idempotent. (Delete Tenant, this version's feature, needs no data migration: its model is new.)
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ncollection.workspace.config']._nc_clear_placeholder_mail_sender()
