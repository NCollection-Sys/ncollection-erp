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
from odoo import api, fields, models


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

    def write(self, vals):
        res = super().write(vals)
        if 'lang' in vals:
            self._nc_refresh_session_language()
        return res

    def _nc_refresh_session_language(self):
        """Make the NEXT page render use the language just saved.

        Odoo refreshes ``request.session.context`` only inside
        ``session_info()`` — which runs while the page is being rendered, AFTER
        the stylesheet bundle (LTR vs RTL) was already chosen from the stale
        session language. So the first reload after a language change came back
        in the old direction and only a second, manual reload fixed it. Writing
        the new language into the session here, in the request that saves it,
        means the reload that follows is already rendered correctly.
        """
        if len(self) != 1 or self != self.env.user:
            return
        try:
            from odoo.http import request
            session = request.session if request else None
        except RuntimeError:  # no HTTP request (cron, shell, tests)
            return
        if session and session.uid == self.id:
            context = dict(session.context)
            context['lang'] = self.lang
            session.context = context

    # ------------------------------------------------------------------ Preferences > Notification
    # mail words the choice "In Odoo" (a static label and help). The values stay 'email' / 'inbox' - only the words
    # change - and they speak in the System Branding name, resolved on every read, so a renamed system shows at once.
    notification_type = fields.Selection(selection='_nc_notification_type_selection')

    @api.model
    def _nc_notification_type_selection(self):
        name = self.env['ncollection.product.branding'].nc_brand()['name']
        return [('email', self.env._("By Emails")), ('inbox', self.env._("In %(system)s", system=name))]

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        res = super().fields_get(allfields, attributes)
        if 'help' in res.get('notification_type', {}):
            name = self.env['ncollection.product.branding'].nc_brand()['name']
            res['notification_type']['help'] = self.env._(
                "Policy on how to handle Chatter notifications:\n"
                "- By Emails: notifications are sent to your email address\n"
                "- In %(system)s: notifications appear in your %(system)s Inbox", system=name)
        return res


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
    from .res_company import adopt_nc_layout
    adopt_nc_layout(env)
