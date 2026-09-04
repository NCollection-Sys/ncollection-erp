# -*- coding: utf-8 -*-
"""Odoo onboarding tours are off in NCollection ERP (#472).

WHAT THE TOUR FEATURE ACTUALLY IS. `web_tour` computes
`res.users.tour_enabled` (True for an admin on a database with no demo data),
publishes it into the session (`web_tour/models/ir_http.py`), and
`web_tour/models/tour.py` uses it to decide whether onboarding tours are served
to a user at all. The visible trigger is the "Onboarding" toggle the tour
service adds to the debug menu; the invisible half is Odoo-authored onboarding
tours running inside a product that is not Odoo.

WHY SERVER-SIDE AND NOT CSS. Hiding the toggle would leave the feature running:
`tour_enabled` would still be True, tours would still be consumed, and the
pointer could still appear. Turning the flag off is the feature's own switch —
`switch_tour_enabled` writes exactly this field — so nothing is monkey-patched
and nothing breaks when `web_tour` changes its UI.

`web_tour` is a transitive dependency of `web`, so it is always installed; this
override is inert if that ever stops being true.
"""
from odoo import api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.depends('create_date')
    def _compute_tour_enabled(self):
        """Always False. Overrides web_tour's compute, which turns tours ON for
        every admin on a demo-free database — i.e. every NCollection platform
        and every provisioned tenant.

        `@api.depends('create_date')` mirrors upstream exactly. It is not
        decorative: the field is `store=True`, so the dependency is what decides
        when the value is (re)computed, and a different one here would leave the
        stored value untouched on user creation — the only moment it is ever
        written.
        """
        self.tour_enabled = False


def post_init_hook(env):
    """Turn tours off for users that already exist (#472).

    The compute above only fires when a user is created, so on a database that
    already has users — every existing platform and every provisioned tenant —
    the stored `tour_enabled` keeps whatever web_tour computed at ITS install.
    Without this, the fix would apply only to users created afterwards, which
    is exactly the admin account nobody creates again.

    Idempotent: writes only the rows that are still True.
    """
    users = env['res.users'].sudo().with_context(active_test=False).search(
        [('tour_enabled', '=', True)])
    if users:
        users.write({'tour_enabled': False})
