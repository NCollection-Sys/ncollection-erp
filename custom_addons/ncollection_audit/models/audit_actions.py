# -*- coding: utf-8 -*-
"""The contextual "Audit History" entry on audited documents.

ONE server action per audited document type, bound to its FORM's Action (gear) menu - no new button on any form, so no
form gets cluttered, and only readers of the trail see it (``group_ids``). It opens the central Audit Log filtered to
that one document; the Audit Log stays the one history screen. Kept in step with what is audited on every registry
load (idempotent: it creates what is missing and removes its own entries for types no longer audited).
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

MARKER = "# ncollection_audit:history"
CODE = MARKER + "\naction = env['ncollection.audit.service'].action_open_history(model._name, records.ids)"


class AuditHistoryActions(models.AbstractModel):
    _name = 'ncollection.audit.actions'
    _description = 'Audit History actions on documents'

    @api.model
    def _nc_sync_history_actions(self):
        env = self.env
        Action = env['ir.actions.server'].sudo().with_context(active_test=False)
        wanted = set(env['ncollection.audit.policy'].sudo().search([]).mapped('model'))
        wanted |= set(env['auditlog.rule'].sudo().search([('state', '=', 'confirmed')]).mapped('model_id.model'))
        wanted = {name for name in wanted if name in env and not env[name]._abstract and not env[name]._transient
                  and not getattr(env[name], '_auto', True) is False}
        existing = Action.search([('state', '=', 'code'), ('code', '=like', MARKER + '%')])
        have = {action.model_id.model: action for action in existing}
        group = env.ref('auditlog.group_auditlog_user')
        created = 0
        for name in sorted(wanted - set(have)):
            model = env['ir.model']._get(name)
            Action.create({
                'name': env._("Audit History"), 'model_id': model.id, 'binding_model_id': model.id,
                'binding_type': 'action', 'binding_view_types': 'form', 'state': 'code', 'code': CODE,
                'group_ids': [(6, 0, group.ids)],
            })
            created += 1
        stale = existing.filtered(lambda action: action.model_id.model not in wanted)
        if stale:
            stale.unlink()
        if created or stale:
            _logger.info("ncollection_audit: Audit History actions +%s / -%s", created, len(stale))

    def _register_hook(self):
        result = super()._register_hook()
        try:
            self._nc_sync_history_actions()
        except Exception:                                 # pragma: no cover - a registry load must finish
            _logger.exception("ncollection_audit: syncing Audit History actions failed")
        return result
