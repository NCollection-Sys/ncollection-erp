# -*- coding: utf-8 -*-
"""The Tenant Owner can save the document layout (Option A, Phase 0.1).

Odoo's ``document_layout_save`` ticks an onboarding step as its last act. The onboarding models are administrator-only,
so the Owner's save would fail after the layout was written. Only that tick is elevated: it records progress on a
checklist, writes nothing about the company, and is restricted to the Tenant Owner group."""
from odoo import models


class OnboardingStep(models.Model):
    _inherit = 'onboarding.onboarding.step'

    def action_set_just_done(self):
        if self.env.user.has_group('ncollection_core.group_tenant_admin') and not self.env.su:
            self = self.sudo()
        return super().action_set_just_done()
