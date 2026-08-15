# -*- coding: utf-8 -*-
"""P8-T01: one answer to "how do we store a credential".

This module hashes TWO kinds of secret — the OAuth2 client secret and, via
core, the access token. They must use the same context, or the repo has two
answers to the same question and only one of them has been reviewed.

Core's `KEY_CRYPT_CONTEXT` is a module-level constant in
`odoo/addons/base/models/res_users.py`. Exposing it through a method rather
than importing the constant at each call site means there is one place to look
when someone asks what algorithm is in use, and one place to change if core
ever moves it.
"""
from odoo import api, models
from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT


class ResUsersApikeys(models.Model):
    _inherit = 'res.users.apikeys'

    @api.model
    def _nc_crypt_context(self):
        """Core's password context, for anything this platform hashes.

        NOT a new context. If core hardens its algorithm, everything hashed
        here follows automatically — which is the point of not choosing one.
        """
        return KEY_CRYPT_CONTEXT
