# -*- coding: utf-8 -*-
"""Switching language must not need a second, manual reload.

Odoo refreshes request.session.context only while rendering the page, after the
LTR/RTL stylesheet was already chosen, so the first reload after a change came
back in the old direction. The write that saves the language now updates the
session in the same request. (Verified end to end in a browser; this pins the
mechanism.)
"""
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLanguageSwitchSession(TransactionCase):

    def _session(self, uid):
        session = MagicMock()
        session.uid = uid
        session.context = {'lang': 'en_US', 'tz': 'UTC'}
        return session

    def test_saving_my_language_updates_the_session_in_the_same_request(self):
        self.env['res.lang']._activate_lang('ar_001')
        user = self.env.user
        session = self._session(user.id)
        with patch('odoo.http.request', MagicMock(session=session)):
            user.write({'lang': 'ar_001'})
        self.assertEqual(session.context['lang'], 'ar_001')
        self.assertEqual(session.context['tz'], 'UTC', 'other context keys survive')

    def test_changing_someone_elses_language_leaves_my_session_alone(self):
        self.env['res.lang']._activate_lang('ar_001')
        other = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Other', 'login': 'lang_other_x',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        session = self._session(self.env.user.id)
        with patch('odoo.http.request', MagicMock(session=session)):
            other.write({'lang': 'ar_001'})
        self.assertEqual(session.context['lang'], 'en_US')

    def test_no_http_request_is_not_an_error(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.env.user.write({'lang': 'ar_001'})  # shell/cron/test: nothing to refresh


@tagged('post_install', '-at_install')
class TestSettingsLanguageList(TransactionCase):

    def test_general_settings_lists_the_real_active_languages(self):
        self.env['res.lang']._activate_lang('ar_001')
        settings = self.env['res.config.settings'].create({})
        active = self.env['res.lang'].search([('active', '=', True)])
        self.assertEqual(settings.nc_active_language_ids, active)
        self.assertIn('ar_001', settings.nc_active_language_ids.mapped('code'))
