# -*- coding: utf-8 -*-
"""#472 — turn Odoo onboarding tours off on databases that already exist.

`post_init_hook` covers a FRESH install. This covers the ones that matter more:
every platform and every provisioned tenant already has its users, and
`tour_enabled` is stored and only recomputed when a user is created — so
without this the fix would apply to nobody who is already using the system.

Idempotent: touches only rows still set to True.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'tour_enabled' not in env['res.users']._fields:
        return
    users = env['res.users'].sudo().with_context(active_test=False).search(
        [('tour_enabled', '=', True)])
    if users:
        users.write({'tour_enabled': False})
