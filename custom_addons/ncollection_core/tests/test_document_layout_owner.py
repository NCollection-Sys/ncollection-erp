# -*- coding: utf-8 -*-
"""Option A: the workspace Owner configures THEIR company's document layout with Odoo's own wizard
(web's base.document.layout), without base.group_system / base.group_erp_manager. The wizard writes res.company through
related fields, where the Workspace Administrator delegation decides: an installed layout only, own company only,
allowlisted fields only."""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestOwnerConfiguresTheDocumentLayout(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other = cls.env['res.company'].create({'name': 'Layout Other Co'})
        cls.owner = new_test_user(
            cls.env, login='dl_owner', groups='base.group_user,ncollection_core.group_role_owner',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])
        cls.clerk = new_test_user(cls.env, login='dl_clerk', groups='base.group_user',
                                  company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])
        cls.layouts = cls.env['report.layout'].search([])

    def _wizard(self, user=None, **vals):
        layout = self.layouts[:1]
        base = {'company_id': self.company.id, 'report_layout_id': layout.id,
                'external_report_layout_id': layout.view_id.id}
        return self.env['base.document.layout'].with_user(user or self.owner).create(dict(base, **vals))

    def test_the_owner_opens_the_wizard_and_the_preview_renders(self):
        wizard = self._wizard()
        self.assertTrue(wizard.preview, "the live preview rendered for the Owner")

    def test_the_owner_saves_colours_font_and_header_and_they_persist(self):
        wizard = self._wizard(primary_color='#112233', secondary_color='#445566', font='Lato',
                              report_header='<p>Owner header</p>')
        wizard.document_layout_save()
        self.company.invalidate_recordset()
        self.assertEqual((self.company.primary_color, self.company.secondary_color, self.company.font),
                         ('#112233', '#445566', 'Lato'))
        self.assertIn('Owner header', str(self.company.report_header))

    def test_only_an_installed_layout_can_be_chosen(self):
        arbitrary = self.env['ir.ui.view'].create({
            'name': 'owner.probe', 'type': 'qweb', 'key': 'dl.probe', 'arch': '<t t-name="dl.probe"/>'})
        with self.assertRaises(ValidationError):
            self.company.with_user(self.owner).write({'external_report_layout_id': arbitrary.id})
        self.assertNotEqual(self.company.external_report_layout_id, arbitrary)

    def test_another_company_is_never_touched(self):
        with self.assertRaises(AccessError):
            self.other.with_user(self.owner).write({'primary_color': '#000000'})
        self.assertNotEqual(self.other.primary_color, '#000000')

    def test_the_wizard_is_refused_to_everyone_who_is_not_a_workspace_administrator(self):
        with self.assertRaises(AccessError):
            self._wizard(user=self.clerk)

    def test_the_owner_still_has_no_technical_rights_because_of_it(self):
        self.assertFalse(self.owner.has_group('base.group_system'))
        self.assertFalse(self.owner.has_group('base.group_erp_manager'))
        with self.assertRaises(AccessError):
            self.env['ir.ui.view'].with_user(self.owner).create({
                'name': 'x', 'type': 'qweb', 'key': 'dl.x', 'arch': '<t t-name="dl.x"/>'})

    def test_the_preview_is_computed_for_the_owner_from_scratch(self):
        wizard = self._wizard()
        self.env.registry.clear_cache('templates')      # the compiled template is not already in the cache
        wizard.invalidate_recordset(['preview'])
        self.assertTrue(wizard.preview, "computed again, as the Owner, not read from the cache")

    def test_the_preview_of_another_companys_layout_is_refused(self):
        wizard = self._wizard()
        wizard.sudo().company_id = self.other
        wizard.invalidate_recordset(['preview'])
        with self.assertRaises(AccessError):
            wizard.preview

    def test_the_elevation_is_only_for_the_workspace_administrator(self):
        wizard = self._wizard()
        wizard.invalidate_recordset(['preview'])
        self.assertTrue(wizard.sudo(False).with_user(self.owner).preview)
        self.assertFalse(self.owner.has_group('base.group_system'), "the Owner is still not a system administrator")
