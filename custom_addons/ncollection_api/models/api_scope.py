# -*- coding: utf-8 -*-
"""P8-T01: what a token is allowed to do, as data.

Scopes are records rather than a Selection so a later module can add one
without editing this one — the same reason `auditlog.rule` is data rather than
a hardcoded list. The seeded set is deliberately small: a scope that exists but
guards nothing is an invitation to grant it.
"""
from odoo import fields, models


class NcollectionApiScope(models.Model):
    _name = 'ncollection.api.scope'
    _description = 'NCollection API Scope'
    _order = 'code'
    _rec_name = 'code'

    code = fields.Char(required=True, index=True)
    description = fields.Char()

    # Two scopes with one code would make grants ambiguous, and the ambiguity
    # would resolve differently depending on search order. models.Constraint
    # because Odoo 19 ignores the legacy _sql_constraints list.
    _code_uniq = models.Constraint(
        'unique(code)', "A scope code must be unique.")
