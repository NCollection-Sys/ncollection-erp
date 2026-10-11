# -*- coding: utf-8 -*-
"""A platform administrator's entry session can never leave a persistent credential behind.

The proxy user (models/platform_entry.py) is an Owner + system administrator for a few hours so the platform can
support a tenant. Everything it does is attributed to it, and its session ends by itself. An API key would outlive
both: it is a bearer secret with no session, no 8-hour window and no MFA. Odoo's own key wizard asks for a password
the proxy does not have; this is the second, independent lock on the one place every key is created
(``res.users.apikeys._generate``: the core wizard and the NCollection API tokens both end here), so no RPC, ORM or
``sudo()`` path can reach it. ``sudo()`` keeps the acting user, which is what is checked.
"""
from odoo import models
from odoo.exceptions import AccessError


class ResUsersApikeys(models.Model):
    _inherit = 'res.users.apikeys'

    def _generate(self, scope, name, expiration_date):
        if self.env['res.users']._nc_actor_is_platform_proxy():
            raise AccessError(self.env._(
                "A platform administrator session cannot create API keys. Use a named tenant user."))
        return super()._generate(scope, name, expiration_date)
