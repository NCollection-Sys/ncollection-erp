# -*- coding: utf-8 -*-
"""#250: the XLSX download route — no artefact, and no new way in.

The point of the change is a NEGATIVE: `action_export_xlsx` must stop creating
an `ir.attachment`. That is asserted directly, because "no leak" is not
observable from a passing export.

The route takes a model name from the URL, which is the same forgery surface
`action_drill_down` had before #313. These tests hold that line: a forged model
is refused, and one user cannot pull another's report run.
"""
from odoo.tests import HttpCase, tagged

_ROUTE = '/ncollection/account_reports/xlsx/%s/%s'


@tagged('post_install', '-at_install')
class TestXlsxStream(HttpCase):

    def setUp(self):
        super().setUp()
        group = self.env.ref('account.group_account_readonly')
        base = self.env.ref('base.group_user')
        self.owner = self.env['res.users'].create({
            'name': 'Report Owner', 'login': 'nc_xlsx_owner',
            'password': 'nc_xlsx_owner_pw',
            'group_ids': [(6, 0, [group.id, base.id])],
        })
        self.other = self.env['res.users'].create({
            'name': 'Other Accountant', 'login': 'nc_xlsx_other',
            'password': 'nc_xlsx_other_pw',
            'group_ids': [(6, 0, [group.id, base.id])],
        })
        # Created BY the owner, so the create_uid ir.rule binds it to them.
        self.wizard = self.env['ncollection.account.report.reference'].with_user(
            self.owner).create({})

    # ---- the actual fix ---------------------------------------------------

    def test_export_creates_no_attachment(self):
        """The leak was an ir.attachment against a TRANSIENT wizard. The fix is
        that it no longer exists — assert the absence, not just a working
        download, because a working download looked fine before too."""
        before = self.env['ir.attachment'].search_count([])
        action = self.wizard.action_export_xlsx()
        self.assertEqual(
            self.env['ir.attachment'].search_count([]), before,
            "action_export_xlsx still persists an ir.attachment (#250)")
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertNotIn('/web/content/', action['url'])
        self.assertIn('/ncollection/account_reports/xlsx/', action['url'])
        # ...and it points at THIS wizard, not a guessable stored object.
        self.assertTrue(action['url'].endswith('/%s' % self.wizard.id))

    def test_route_streams_a_real_workbook(self):
        self.authenticate('nc_xlsx_owner', 'nc_xlsx_owner_pw')
        res = self.url_open(_ROUTE % (self.wizard._name, self.wizard.id))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'PK'),
                        "not a real xlsx workbook")
        self.assertIn('spreadsheetml', res.headers.get('Content-Type', ''))
        self.assertIn('attachment', res.headers.get('Content-Disposition', ''))
        self.assertIn('.xlsx', res.headers.get('Content-Disposition', ''))

    # ---- the surface this route must not re-open ---------------------------

    def test_a_forged_model_in_the_url_is_refused(self):
        """The #313 lesson: a model name from a URL reaches a dynamic
        self.env[...] lookup. Resolving through the report engine's class fails
        closed, so non-report models are unreachable."""
        self.authenticate('nc_xlsx_owner', 'nc_xlsx_owner_pw')
        for forged in ('res.users', 'ir.attachment', 'res.company', 'no.such.model',
                       # The engine's OWN name: isinstance() is reflexively true
                       # for it, so without the _abstract check this reached
                       # exists() on a table that _auto=False never created —
                       # an unhandled UndefinedTable, i.e. a 500 from one URL.
                       'ncollection.account.report'):
            with self.subTest(model=forged):
                res = self.url_open(_ROUTE % (forged, 1))
                self.assertEqual(
                    res.status_code, 404,
                    "%s was reachable through the export route" % forged)

    def test_another_user_cannot_download_my_report(self):
        """The F2-T01 IDOR class, carried over: previously the attachment id was
        the guessable object; now it is the wizard id. The create_uid ir.rule
        must still deny it."""
        self.authenticate('nc_xlsx_other', 'nc_xlsx_other_pw')
        res = self.url_open(_ROUTE % (self.wizard._name, self.wizard.id))
        self.assertNotEqual(
            res.status_code, 200,
            "another user downloaded a report run they do not own")
        # Control: the owner CAN, so the assertion above is not passing because
        # the route is simply broken for everyone.
        self.authenticate('nc_xlsx_owner', 'nc_xlsx_owner_pw')
        ok = self.url_open(_ROUTE % (self.wizard._name, self.wizard.id))
        self.assertEqual(ok.status_code, 200)

    def test_a_vacuumed_wizard_is_a_404_not_a_traceback(self):
        """Transient records are reclaimed ~1h after creation. A stale download
        link must fail cleanly rather than 500."""
        self.authenticate('nc_xlsx_owner', 'nc_xlsx_owner_pw')
        missing = self.wizard.id + 10_000
        res = self.url_open(_ROUTE % (self.wizard._name, missing))
        self.assertEqual(res.status_code, 404)
