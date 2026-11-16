# -*- coding: utf-8 -*-
"""AI Readiness: SIX separate states, never one "AI works" light (final review #10).

  1. Gateway reachable        GET /healthz answers
  2. Gateway authenticated    this workspace's SIGNED identity is accepted (POST /v1/auth-check: the provider is NOT
                              called, nothing is spent, no content is sent)
  3. Business-data tools      the read-only topics whose models are installed here, and that this user can read
  4. Real model provider      the gateway's provider is not the mock (and which one / which model)
  5. Free-typed questions     the workspace policy switch (ncollection_ai.enable_free_text_questions)
  6. External use authorised  the platform's explicit authorisation to send (pseudonymised) business context to a
                              third-party provider (ncollection_ai.external_provider_authorized;
                              Platform Administrator)

A Workspace Administrator sees this screen; an ordinary user only ever sees a neutral "not available" message, never a
configuration detail. Turning free text on is refused until 1, 2 and 3 hold.
"""
import json
import os
import urllib.error
import urllib.request

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from . import ai_topics
from .gateway_client import _GATEWAY_KEY_ENV, _HEADER_SIGNATURE, _HEADER_TIMESTAMP, _sign

FREE_TEXT_PARAM = 'ncollection_ai.enable_free_text_questions'
EXTERNAL_PARAM = 'ncollection_ai.external_provider_authorized'
_TIMEOUT = 5


# One screen, kept beside the probe it displays.
class AiReadiness(models.TransientModel):  # pylint: disable=no-wizard-in-models
    _name = 'ncollection.ai.readiness'
    _description = 'AI Assistant readiness'

    key_configured = fields.Boolean(readonly=True)
    reachable = fields.Boolean(string='1. Gateway reachable', readonly=True)
    authenticated = fields.Boolean(string='2. Gateway authenticated', readonly=True)
    tools_ready = fields.Boolean(string='3. Business-data tools operational', readonly=True)
    tools_detail = fields.Char(string='Topics available', readonly=True)
    real_provider = fields.Boolean(string='4. Real model provider configured', readonly=True)
    provider = fields.Char(readonly=True)
    model = fields.Char(readonly=True)
    free_text = fields.Boolean(string='5. Free-typed questions allowed', readonly=True)
    external_authorized = fields.Boolean(string='6. External provider use authorised', readonly=True)
    mode = fields.Selection([('unavailable', 'Unavailable'), ('demo', 'Demo (mock provider, no real model)'),
                             ('live', 'Live model')], readonly=True)
    notes = fields.Text(readonly=True)

    @api.model
    def _nc_check_admin(self):
        user = self.env.user
        if not (user.has_group('ncollection_core.group_tenant_admin') or user.has_group('base.group_system')):
            raise AccessError(self.env._("Only a Workspace Administrator can see the AI readiness."))

    @api.model
    def _nc_probe(self):
        """The six states as a dict. Never raises for a down gateway: a readiness screen that crashes is useless."""
        notes = []
        gateway = self.env['ncollection.ai.gateway']
        base = gateway._base_url()
        master = os.environ.get(_GATEWAY_KEY_ENV, '')
        state = {'key_configured': bool(master), 'reachable': False, 'authenticated': False, 'provider': '',
                 'model': '', 'real_provider': False}
        try:
            with urllib.request.urlopen(base + '/healthz', timeout=_TIMEOUT) as resp:
                health = json.loads(resp.read(4096).decode())
            state['reachable'] = health.get('status') == 'ok'
            state['provider'] = str(health.get('provider') or '')[:40]
        except (OSError, ValueError, urllib.error.URLError):
            notes.append(self.env._("The AI gateway did not answer at %s.", base))
        if not master:
            notes.append(self.env._("This server has no %s: it cannot prove the workspace's identity.",
                                    _GATEWAY_KEY_ENV))
        elif state['reachable']:
            body = json.dumps({'tenant': self.env.cr.dbname}).encode()
            stamp = '%d' % int(fields.Datetime.now().timestamp())
            request = urllib.request.Request(base + '/v1/auth-check', data=body, method='POST', headers={
                'content-type': 'application/json', _HEADER_TIMESTAMP: stamp,
                _HEADER_SIGNATURE: _sign(master, self.env.cr.dbname, stamp, body)})
            try:
                with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
                    check = json.loads(resp.read(4096).decode())
                state['authenticated'] = bool(check.get('authenticated'))
                state['real_provider'] = bool(check.get('real_provider'))
                state['provider'] = str(check.get('provider') or state['provider'])[:40]
                state['model'] = str(check.get('model') or '')[:80]
            except urllib.error.HTTPError as exc:
                notes.append(self.env._("The gateway refused this workspace's identity (HTTP %s): the key on this "
                                        "server and on the gateway differ.", exc.code))
            except (OSError, ValueError, urllib.error.URLError):
                notes.append(self.env._("The gateway's identity check did not answer."))
        topics = []
        for key, _en, _ar, factory in ai_topics.TOPICS:
            models_used = {spec['model'] for spec in factory(fields.Date.context_today(self))}
            if all(name in self.env for name in models_used) and all(
                    self.env[name].has_access('read') for name in models_used):
                topics.append(key)
        state.update({
            'tools_ready': bool(topics), 'tools_detail': ', '.join(topics) or '-',
            'free_text': self.env['ncollection.ai.question']._free_text_enabled(),
            'external_authorized': self.env['ir.config_parameter'].sudo().get_param(EXTERNAL_PARAM, '') == 'True',
        })
        if state['real_provider'] and not state['external_authorized']:
            notes.append(self.env._("A real provider is configured but the platform has not authorised sending "
                                    "business context to it."))
        live = state['reachable'] and state['authenticated'] and state['tools_ready']
        state['mode'] = 'unavailable' if not live else ('live' if state['real_provider'] and
                                                        state['external_authorized'] else 'demo')
        state['notes'] = '\n'.join(notes)
        return state

    @api.model
    def action_open(self):
        self._nc_check_admin()
        record = self.create(self._nc_probe())
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': record.id, 'view_mode': 'form',
                'target': 'current', 'name': self.env._('AI Assistant Readiness')}

    def action_refresh(self):
        return self.action_open()

    def action_set_free_text(self):
        """Turn free-typed questions ON: a Workspace Administrator's decision, refused while the backend cannot answer
        them (states 1-3)."""
        self._nc_check_admin()
        state = self._nc_probe()
        if not (state['reachable'] and state['authenticated'] and state['tools_ready']):
            raise UserError(self.env._("Free-typed questions need a reachable, authenticated gateway and at least one "
                                       "business-data topic. See the states above."))
        self.env['ir.config_parameter'].sudo().set_param(FREE_TEXT_PARAM, 'True')
        return self.action_open()

    def action_unset_free_text(self):
        self._nc_check_admin()
        self.env['ir.config_parameter'].sudo().set_param(FREE_TEXT_PARAM, 'False')
        return self.action_open()
