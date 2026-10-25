# -*- coding: utf-8 -*-
"""Self-tests of the release preflight and of the module list an upgrade may name (stdlib only, no database).

    python3 scripts/upgrade/test_preflight_versions.py

``verify_upgrade.sh`` runs this inside the odoo container BEFORE it trusts the preflight (a gate whose own rules are
broken must not report "pass"). It pins the pre-commit review findings M2 (the automatic module list could reach
``base``, i.e. a whole-database upgrade) and M3 (a preflight that checked nothing reported PASS).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import preflight_versions as pv  # noqa: E402


def _roots(modules):
    """A disposable custom root and OCA root holding ``modules`` {name: (root, version)}; returns the roots."""
    tmp = tempfile.mkdtemp()
    custom, oca = os.path.join(tmp, 'extra'), os.path.join(tmp, 'oca')
    for name, (where, version) in modules.items():
        base = custom if where == 'custom' else os.path.join(oca, 'server-tools')
        os.makedirs(os.path.join(base, name))
        with open(os.path.join(base, name, '__manifest__.py'), 'w', encoding='utf-8') as handle:
            handle.write(repr({'name': name, 'version': version}))
    return (custom, oca)


class TestModuleUniverse(unittest.TestCase):

    def setUp(self):
        self.disk = pv._disk_versions(_roots({
            'ncollection_core': ('custom', '19.0.1.23.0'),
            'ncollection_branding': ('custom', '19.0.1.12.0'),
            'queue_job': ('oca', '19.0.1.0.0'),
        }))

    def test_the_universe_is_the_custom_and_oca_roots(self):
        self.assertEqual(set(self.disk), {'ncollection_core', 'ncollection_branding', 'queue_job'})

    def test_valid_ncollection_and_oca_modules_are_accepted(self):
        self.assertEqual(pv.validate_modules('ncollection_core,ncollection_branding,queue_job', self.disk), [])

    def test_base_is_refused(self):
        problems = pv.validate_modules('ncollection_core,base', self.disk)
        self.assertEqual(len(problems), 1)
        self.assertIn('base', problems[0])

    def test_base_is_refused_even_if_a_root_holds_a_module_of_that_name(self):
        disk = dict(self.disk, base='19.0.1.3')
        self.assertTrue(pv.validate_modules('base', disk))

    def test_all_is_refused(self):
        for spec in ('all', 'ncollection_core,all', 'all,ncollection_core'):
            with self.subTest(spec=spec):
                self.assertTrue(pv.validate_modules(spec, self.disk))

    def test_a_module_outside_the_roots_is_refused(self):
        for name in ('web', 'account', 'mail', 'ncollection_not_deployed'):
            with self.subTest(module=name):
                problems = pv.validate_modules('ncollection_core,%s' % name, self.disk)
                self.assertEqual(len(problems), 1)
                self.assertIn('not an NCollection or OCA module', problems[0])

    def test_names_that_are_not_plain_are_refused(self):
        for spec in ('', ',', 'ncollection_core,', 'Ncollection_core', 'ncollection-core', 'a b', 'x;rm', '../x'):
            with self.subTest(spec=spec):
                self.assertTrue(pv.validate_modules(spec, self.disk))


class TestPreflightVerdict(unittest.TestCase):

    DISK = {'ncollection_core': '19.0.1.23.0', 'ncollection_branding': '19.0.1.12.0', 'queue_job': '19.0.1.0.0'}

    def test_a_current_database_passes(self):
        code, lines = pv.evaluate('db', [('base', '19.0.1.3'), ('ncollection_core', '19.0.1.23.0'),
                                         ('queue_job', '19.0.1.0.0')], self.DISK)
        self.assertEqual(code, 0)
        self.assertIn('PREFLIGHT=PASS', lines)
        self.assertIn('PREFLIGHT_CHECKED=2', lines)

    def test_a_database_behind_the_code_fails_and_names_each_module(self):
        code, lines = pv.evaluate('db', [('ncollection_core', '19.0.1.22.0'), ('ncollection_branding', '19.0.1.10.0')],
                                  self.DISK)
        self.assertEqual(code, 1)
        self.assertIn('PREFLIGHT=FAIL', lines)
        self.assertIn('PREFLIGHT_BEHIND=ncollection_core database 19.0.1.22.0 < code 19.0.1.23.0', lines)
        self.assertIn('PREFLIGHT_MODULES=ncollection_core,ncollection_branding', lines)

    def test_versions_compare_as_numbers_not_text(self):
        code, _lines = pv.evaluate('db', [('ncollection_branding', '19.0.1.9.0')], self.DISK)
        self.assertEqual(code, 1, '1.12 is newer than 1.9')

    def test_nothing_checked_is_an_error_not_a_pass(self):
        """An empty or wrong addons root (e.g. an image without the bind mount) used to PASS every database."""
        code, lines = pv.evaluate('db', [('base', '19.0.1.3'), ('ncollection_core', '19.0.1.22.0')], {})
        self.assertEqual(code, 2)
        self.assertIn('PREFLIGHT=ERROR', lines)
        self.assertIn('PREFLIGHT_CHECKED=0', lines)

    def test_an_installed_ncollection_module_without_code_is_an_error(self):
        code, lines = pv.evaluate('db', [('ncollection_core', '19.0.1.23.0'),
                                         ('ncollection_uae_einvoicing', '19.0.1.0.0')], self.DISK)
        self.assertEqual(code, 2)
        self.assertIn('PREFLIGHT=ERROR', lines)
        self.assertTrue(any(line.startswith('PREFLIGHT_MISSING=ncollection_uae_einvoicing') for line in lines))

    def test_a_missing_module_is_reported_even_when_another_is_behind(self):
        code, lines = pv.evaluate('db', [('ncollection_core', '19.0.1.1.0'), ('ncollection_gone', '1.0')], self.DISK)
        self.assertEqual(code, 2)
        self.assertTrue(any(line.startswith('PREFLIGHT_BEHIND=ncollection_core') for line in lines))

    def test_an_odoo_core_module_not_under_the_roots_is_neither_checked_nor_an_error(self):
        code, _lines = pv.evaluate('db', [('web', '19.0.1.0'), ('ncollection_core', '19.0.1.23.0')], self.DISK)
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
