# -*- coding: utf-8 -*-
"""The NCollection document layout becomes the default of every company still on Odoo's stock default.

`post_init_hook` covers a fresh install; this covers the workspaces that already exist. A company that chose another
layout on purpose is left alone. Idempotent: it only fills in a default.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.ncollection_branding.models.res_company import adopt_nc_layout


def migrate(cr, version):
    if not version:
        return
    adopt_nc_layout(api.Environment(cr, SUPERUSER_ID, {}))
