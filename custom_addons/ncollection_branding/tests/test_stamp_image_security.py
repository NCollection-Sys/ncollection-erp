# -*- coding: utf-8 -*-
"""The company seal is not retrievable by an ordinary user: not by the model, not by /web/image, not by a
report context,
and not across companies, while the server-side overlay still draws it for whoever may print the document.
"""
import base64
import secrets

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

from .test_document_stamp import _b64, _png, _StampCommon


@tagged('post_install', '-at_install')
class TestStampImageIsGroupRestricted(_StampCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        base = cls.env.ref('base.group_user').id
        cls.ordinary = Users.create({'name': 'Seal Ordinary', 'login': 'seal_ordinary',
                                     'company_id': cls.company_a.id, 'company_ids': [Command.set(cls.company_a.ids)],
                                     'group_ids': [Command.set([base])]})
        manager_group = cls.env.ref('ncollection_branding.group_stamp_manager')
        # the kind names who may stamp it
        cls.type_company.sudo().manager_group_ids = [Command.link(manager_group.id)]
        cls.manager = Users.create({'name': 'Seal Manager', 'login': 'seal_manager',
                                    'company_id': cls.company_a.id, 'company_ids': [Command.set(cls.company_a.ids)],
                                    'group_ids': [Command.set([base, manager_group.id])]})
        cls.stamp = cls._stamp_for(cls, cls.type_company, cls.company_a)

    def _stamp_for(self, kind, company):
        return self.env['ncollection.document.stamp'].create({
            'name': 'Seal of ' + company.name, 'company_id': company.id, 'image': _b64(_png()),
            'document_type_ids': [Command.set(kind.ids)]})

    def test_a_stamp_manager_reads_and_configures_the_seal(self):
        stamp = self.stamp.with_user(self.manager)
        self.assertTrue(stamp.image)
        stamp.write({'x_mm': 33.0})
        self.assertEqual(self.stamp.x_mm, 33.0)

    def test_an_ordinary_user_cannot_read_the_raw_image_through_the_model(self):
        stamp = self.stamp.with_user(self.ordinary)
        self.assertEqual(stamp.name, self.stamp.name, "the template's name and geometry stay readable")
        with self.assertRaises(AccessError):
            stamp.image
        with self.assertRaises(AccessError):
            stamp.read(['image'])
        with self.assertRaises(AccessError):
            stamp.write({'image': _b64(_png())})

    def test_the_company_seal_field_is_restricted_the_same_way(self):
        self.company_a.write({'nc_stamp': _b64(_png())})
        with self.assertRaises(AccessError):
            self.company_a.with_user(self.ordinary).nc_stamp
        self.assertTrue(self.company_a.with_user(self.manager).nc_stamp)

    def test_the_overlay_still_draws_it_for_a_user_who_may_only_print(self):
        placement = self.stamp.with_user(self.ordinary)._nc_placement()
        self.assertTrue(base64.b64decode(base64.b64encode(placement['image'])).startswith(b'\x89PNG'))
        Stamp = self.env['ncollection.document.stamp'].with_user(self.ordinary).with_company(self.company_a)
        winner = Stamp._nc_winner(
            self.report, self.firm.with_user(self.ordinary))
        self.assertEqual(winner, self.stamp.with_user(self.ordinary))

    def test_a_report_context_cannot_be_used_to_reach_the_seal(self):
        Stamp = self.env['ncollection.document.stamp'].with_user(self.ordinary)
        self.assertFalse(Stamp._nc_may_preview({'stamp_id': self.stamp.id}))
        with self.assertRaises(AccessError):
            Stamp.with_context(nc_stamp_preview={'stamp_id': self.stamp.id}).browse(self.stamp.id).image

    def test_one_companys_seal_never_reaches_another_companys_users_or_documents(self):
        other = self._stamp_for(self.type_company, self.company_b)
        stamps = self.env['ncollection.document.stamp'].with_user(self.ordinary).search([])
        self.assertIn(self.stamp, stamps)
        self.assertNotIn(other, stamps)
        with self.assertRaises(AccessError):
            other.with_user(self.ordinary).name
        Stamp = self.env['ncollection.document.stamp'].with_user(self.ordinary).with_company(self.company_a)
        winner = Stamp._nc_winner(
            self.report, self.firm.with_user(self.ordinary))
        self.assertNotEqual(winner, other)


@tagged('post_install', '-at_install')
class TestStampImageOverTheWeb(HttpCase):

    def test_web_image_refuses_an_ordinary_user_and_serves_a_manager(self):
        company = self.env['res.company'].create({'name': 'Seal Web Co'})
        kind = self.env['ncollection.document.type'].create({'name': 'Web kind', 'code': 'web_kind'})
        kind.manager_group_ids = [Command.link(self.env.ref('ncollection_branding.group_stamp_manager').id)]
        stamp = self.env['ncollection.document.stamp'].create({
            'name': 'Web seal', 'company_id': company.id, 'image': _b64(_png()),
            'document_type_ids': [Command.set(kind.ids)]})
        base = self.env.ref('base.group_user').id
        ordinary_pw, manager_pw = secrets.token_urlsafe(16), secrets.token_urlsafe(16)   # throwaway, never a literal
        Users = self.env['res.users'].with_context(no_reset_password=True)
        manager_group = self.env.ref('ncollection_branding.group_stamp_manager')
        common = {'company_id': company.id, 'company_ids': [Command.set(company.ids)]}
        Users.create(dict(common, name='Web Ordinary', login='seal_web_ordinary', password=ordinary_pw,
                          group_ids=[Command.set([base])]))
        Users.create(dict(common, name='Web Manager', login='seal_web_manager', password=manager_pw,
                          group_ids=[Command.set([base, manager_group.id])]))
        self.env.flush_all()
        url = '/web/image/ncollection.document.stamp/%d/image' % stamp.id
        self.authenticate('seal_web_ordinary', ordinary_pw)
        refused = self.url_open(url)
        # Odoo answers a refused field with its generic placeholder image, never with the stamp
        self.assertNotEqual(refused.content, base64.b64decode(_b64(_png())))

        # the designer's preview draws the seal into a PDF: it is refused to someone who may not edit the stamp
        self.assertEqual(self.url_open('/ncollection/stamp/preview/%d' % stamp.id).status_code, 404)
        self.authenticate('seal_web_manager', manager_pw)
        self.assertEqual(self.url_open('/ncollection/stamp/preview/%d' % stamp.id).status_code, 200)
        served = self.url_open(url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers['Content-Type'].split(';')[0], 'image/png')
        self.assertEqual(served.content, base64.b64decode(_b64(_png())))
