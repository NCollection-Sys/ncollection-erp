# -*- coding: utf-8 -*-
"""The retention / offboarding evidence package (R46): lib/export_package.py and models/export.py."""

import base64
import hashlib
import io
import json
import zipfile

from odoo.exceptions import AccessError, UserError
from odoo.tests import BaseCase, tagged

from ..lib import export_package as pkg
from .common import GROUP, EInvoiceCase


@tagged('post_install', '-at_install')
class TestPackageFormat(BaseCase):

    FILES = {'einvoices/A-1/record.json': b'{}\n', 'einvoices/A-1/xml-1-abc.xml': b'<x/>'}

    def test_the_same_evidence_gives_the_same_bytes(self):
        first, manifest = pkg.build(self.FILES, {'database': 'db'})
        second, _ = pkg.build(dict(reversed(list(self.FILES.items()))), {'database': 'db'})
        self.assertEqual(first, second)
        header, problems = pkg.verify(first)
        self.assertEqual(problems, [])
        self.assertEqual((header['files'], header['manifest_sha256']), (2, manifest))

    def test_any_change_is_detected(self):
        good, _ = pkg.build(self.FILES, {})
        archive = zipfile.ZipFile(io.BytesIO(good))
        for mutate in ('change', 'drop', 'add'):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as out:
                for info in archive.infolist():
                    data = archive.read(info.filename)
                    if mutate == 'drop' and info.filename.endswith('.xml'):
                        continue
                    if mutate == 'change' and info.filename.endswith('.xml'):
                        data = b'<y/>'
                    out.writestr(info.filename, data)
                if mutate == 'add':
                    out.writestr('einvoices/extra.xml', b'<z/>')
            _header, problems = pkg.verify(buffer.getvalue())
            self.assertTrue(problems, mutate)

    def test_unsafe_paths_are_refused_both_ways(self):
        for path in ('../etc/passwd', '/abs.xml', 'a/../../b', 'C:/x', 'a\\b', 'a//b', ''):
            with self.assertRaises(pkg.PackageError, msg=path):
                pkg.build({path: b'x'}, {})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as out:
            out.writestr('../evil.txt', b'x')
            out.writestr(pkg.MANIFEST, b'')
            out.writestr(pkg.HEADER, b'{}')
        header, problems = pkg.verify(buffer.getvalue())
        self.assertIsNone(header)
        self.assertTrue(any('unsafe' in p for p in problems))

    def test_a_record_name_becomes_a_safe_component(self):
        self.assertEqual(pkg.safe_part('EINV/2026/0001'), 'EINV-2026-0001')
        self.assertEqual(pkg.safe_part('..'), '_')
        self.assertEqual(pkg.safe_part('a b/../c'), 'a-b-..-c')

    def test_not_a_zip_is_a_problem_not_a_crash(self):
        self.assertEqual(pkg.verify(b'not a zip'), (None, ['not a ZIP file']))


@tagged('post_install', '-at_install')
class TestEvidenceExport(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls._user('exp_manager', [GROUP + 'group_einvoice_manager'])
        cls.clerk = cls._user('exp_clerk', [GROUP + 'group_einvoice_user'])
        cls.other = cls._company('Other Export Co', tin='1876543218', trn='187654321802003',
                                 registry='112345678900097')

    def _xml_record(self):
        record = self._einvoice()
        record.action_prepare()
        record.action_generate_xml()
        return record

    def _export(self):
        Export = self.env['ncollection.einvoice.export'].with_user(self.manager).with_company(self.company)
        action = Export.action_create_package()
        return Export.browse(action['res_id'])

    def _package(self, export):
        return base64.b64decode(export.sudo().attachment_id.datas)

    @staticmethod
    def _content(package):
        archive = zipfile.ZipFile(io.BytesIO(package))
        return b''.join(archive.read(name) for name in archive.namelist())

    def test_the_package_holds_the_evidence_and_verifies(self):
        record = self._xml_record()
        record.action_prepare()  # supersedes the XML: both versions must be in the package
        record.action_generate_xml()
        export = self._export()
        package = self._package(export)
        header, problems = pkg.verify(package)
        self.assertEqual(problems, [])
        self.assertEqual((header['database'], header['einvoices']), (self.env.cr.dbname, 1))
        names = zipfile.ZipFile(io.BytesIO(package)).namelist()
        xmls = [n for n in names if n.endswith('.xml') and n.startswith('einvoices/')]
        self.assertEqual(len(xmls), 2, 'the superseded XML too')
        data = json.loads(zipfile.ZipFile(io.BytesIO(package)).read(
            [n for n in names if n.endswith('record.json')][0]))
        self.assertEqual(data['einvoice']['uuid'], record.uuid)
        self.assertEqual({x['sha256'] for x in data['xml'] if x['current']}, {record.xml_sha256})
        self.assertTrue(data['history'] and data['findings'] is not None)
        self.assertEqual(export.package_sha256, hashlib.sha256(package).hexdigest())

    def test_another_companys_evidence_never_enters_the_package(self):
        self._xml_record()
        theirs = self.env['ncollection.einvoice'].sudo().search([('company_id', '=', self.company.id)], limit=1)
        theirs_name = theirs.name
        self.env.cr.execute('UPDATE ncollection_einvoice SET company_id = %s WHERE id = %s',
                            (self.other.id, theirs.id))
        export = self._export()
        self.assertEqual(export.einvoice_count, 0)
        self.assertNotIn(theirs_name.encode(), self._content(self._package(export)))

    def test_a_manager_cannot_export_a_company_that_is_not_theirs(self):
        with self.assertRaises(AccessError):
            self.env['ncollection.einvoice.export'].with_user(self.manager).action_create_package(self.other)

    def test_only_a_manager_exports(self):
        with self.assertRaises(AccessError):
            self.env['ncollection.einvoice.export'].with_user(self.clerk).action_create_package()

    def test_the_package_is_deterministic(self):
        self._xml_record()
        first, second = self._export(), self._export()
        self.assertEqual(first.package_sha256, second.package_sha256)
        self.assertNotEqual(first, second, 'each export is its own audit record')

    def test_an_export_is_append_only(self):
        export = self._export()
        with self.assertRaises(UserError):
            export.with_user(self.manager).write({'file_count': 0})
        with self.assertRaises(UserError):
            export.unlink()
        with self.assertRaises(UserError):
            self.env['ncollection.einvoice.export'].with_user(self.manager).create(
                {'company_id': self.company.id, 'requested_by': self.manager.id, 'requested_at': '2026-10-29'})

    def test_no_credential_is_ever_exported(self):
        self._xml_record()
        package = self._package(self._export())
        content = self._content(package)
        self.assertNotIn(b'ciphertext', content)
        self.assertNotIn(b'api_token', content)
