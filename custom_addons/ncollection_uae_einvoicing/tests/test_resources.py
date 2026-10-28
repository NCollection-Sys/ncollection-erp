# -*- coding: utf-8 -*-
"""The pinned official PINT-AE resources: integrity, explicit version, loaders, and the recorded upstream
discrepancies."""

import os
import re
import shutil
import tempfile

from odoo.tests import TransactionCase, tagged

from ..lib import codelists, spec, upstream

LIB = os.path.join(os.path.dirname(__file__), '..', 'lib')


@tagged('post_install', '-at_install')
class TestPinnedResources(TransactionCase):

    def test_the_vendored_official_files_match_their_manifest(self):
        self.assertEqual(spec.verify_resources(), [], "the official resources were altered, removed or extended")

    def test_the_version_is_explicit_and_the_source_is_recorded(self):
        profile = spec.get_profile()
        self.assertEqual(profile.key, 'pint-ae-1.0.4')
        self.assertEqual(profile.pint_version, '1.0.4')
        self.assertEqual(profile.release_date, '2026-06-02')
        self.assertEqual(profile.status, 'Final')
        self.assertEqual(profile.customization_id, 'urn:peppol:pint:billing-1@ae-1')
        self.assertEqual(profile.profile_id, 'urn:peppol:bis:billing')
        self.assertEqual(profile.participant_scheme, '0235')
        self.assertTrue(profile.resource_dir.endswith(os.path.join('pint_ae', '1.0.4')))
        with open(os.path.join(profile.resource_dir, 'MANIFEST.txt'), encoding='utf-8') as handle:
            header = handle.read(1200)
        self.assertIn(profile.source_zip_sha256, header, "the source archive checksum must be in the manifest")
        self.assertIn(profile.source_url, header)
        self.assertIn('2026-06-02', header)

    def test_an_unknown_profile_is_an_error_not_a_fallback(self):
        with self.assertRaises(KeyError):
            spec.get_profile('pint-ae-9.9.9')

    def test_tampering_is_detected(self):
        profile = spec.get_profile()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = os.path.join(tmp, '1.0.4')
        shutil.copytree(profile.resource_dir, copy)
        self.assertEqual(spec.verify_resources(profile, copy), [])
        victim = os.path.join(copy, 'codelist', 'UNCL4461.gc')
        with open(victim, 'ab') as handle:
            handle.write(b' ')
        os.remove(os.path.join(copy, 'codelist', 'eas.gc'))
        with open(os.path.join(copy, 'codelist', 'extra.gc'), 'w', encoding='utf-8') as handle:
            handle.write('x')
        problems = spec.verify_resources(profile, copy)
        self.assertIn('changed: codelist/UNCL4461.gc', problems)
        self.assertIn('missing: codelist/eas.gc', problems)
        self.assertIn('unlisted: codelist/extra.gc', problems)

    def test_the_specification_version_lives_in_exactly_one_place(self):
        """No other module of the library may hard-code the version or the specification identifier."""
        offenders = []
        pattern = re.compile(r'pint-ae-1\.|billing-1@ae|1\.0\.4|2026-06-02')
        for name in sorted(os.listdir(LIB)):
            if not name.endswith('.py') or name in ('spec.py', 'upstream.py'):
                continue
            with open(os.path.join(LIB, name), encoding='utf-8') as handle:
                if pattern.search(handle.read()):
                    offenders.append(name)
        self.assertEqual(offenders, [], "version strings must come from lib/spec.py")

    def test_every_example_is_present(self):
        profile = spec.get_profile()
        invoices = os.listdir(os.path.join(profile.examples_dir, 'invoice'))
        credits = os.listdir(os.path.join(profile.examples_dir, 'creditnote'))
        self.assertEqual((len(invoices), len(credits)), (27, 3))


@tagged('post_install', '-at_install')
class TestCodeLists(TransactionCase):

    def test_the_lists_the_engine_relies_on_are_the_published_ones(self):
        self.assertEqual(codelists.codes('payment_means'), {'1', '10', '20', '21', '30', '49', '54', '55', '68'})
        self.assertEqual(codelists.codes('item_type'), {'G', 'S', 'B'})
        self.assertEqual(codelists.codes('tax_exemption'), {'DL8.46.1', 'DL8.46.2', 'DL8.46.3', 'DL8.46.4'})
        self.assertEqual(
            codelists.codes('credit_reason'),
            {'DL8.61.1.A', 'DL8.61.1.B', 'DL8.61.1.C', 'DL8.61.1.D', 'DL8.61.1.E', 'VD'},
        )
        self.assertEqual(
            codelists.codes('billing_frequency'),
            {'DLY', 'WKY', 'Q15', 'MTH', 'Q45', 'Q60', 'QTR', 'YRL', 'HYR', 'OTH'},
        )
        self.assertEqual(len(codelists.codes('transaction_type')), 8)
        self.assertEqual(codelists.codes('invoice_type'), {'380', '480'})
        self.assertEqual(codelists.codes('credit_type'), {'381', '81'})
        self.assertEqual(len(codelists.codes('rcm_goods_type')), 5)
        self.assertIn('0235', codelists.codes('endpoint_scheme'))

    def test_the_tax_categories_are_the_profiles_explicit_set_with_the_known_defect_accounted_for(self):
        """The profile's set is explicit and Latin; the file differs by exactly the documented Greek-Nu defect."""
        profile = spec.get_profile()
        published = codelists.codes('tax_category')
        fixed = frozenset('N' if code == 'Ν' else code for code in published)
        self.assertEqual(fixed, frozenset(profile.tax_categories))
        self.assertNotEqual(published, frozenset(profile.tax_categories), "the defect must still be in the file")

    def test_the_profiles_emirate_codes_are_the_ones_the_schematron_accepts(self):
        with open(
            os.path.join(spec.get_profile().schematron_dir, 'PINT-jurisdiction-aligned-rules.sch'), encoding='utf-8'
        ) as handle:
            rules = handle.read()
        match = re.search(r'id="ibr-128-ae".*?\(([^)]*)\)\)', rules, re.S)
        accepted = set(re.findall(r'&#34;([A-Z]{3})&#34;', match.group(1)))
        self.assertEqual(accepted, set(spec.get_profile().emirate_codes))

    def test_the_profiles_standard_rate_and_slack_are_the_ones_the_schematron_uses(self):
        profile = spec.get_profile()
        with open(
            os.path.join(profile.schematron_dir, 'PINT-jurisdiction-aligned-rules.sch'), encoding='utf-8'
        ) as handle:
            rules = handle.read()
        self.assertIn('xs:decimal($p) = 5.00', rules)
        self.assertEqual(str(profile.standard_rate), '5.00')
        self.assertIn(',0.02 )', rules)
        self.assertEqual(str(profile.tax_slack), '0.02')


@tagged('post_install', '-at_install')
class TestUpstreamArtifactRegister(TransactionCase):
    """Each recorded discrepancy is still true of the pinned files. When a new PINT-AE release fixes one, this fails so
    a person re-reads the entry and the engine's handling before adopting the release."""

    def test_every_recorded_discrepancy_is_still_present_in_the_pinned_files(self):
        for artifact in upstream.ARTIFACTS:
            self.assertTrue(
                artifact.still_present(spec.get_profile()),
                "%s is no longer true of the pinned files: re-review it" % artifact.id,
            )

    def test_the_register_covers_the_four_discrepancies_that_were_called_out(self):
        ids = {artifact.id for artifact in upstream.ARTIFACTS}
        self.assertTrue(
            {
                'greek-nu-margin-code',
                'hs-sac-min-digits-placeholder',
                'self-billing-type-codes-missing',
                'no-endpoint-format-rule',
            }
            <= ids
        )

    def test_every_entry_states_its_evidence_and_the_engines_handling(self):
        for artifact in upstream.ARTIFACTS:
            self.assertTrue(artifact.summary and artifact.evidence and artifact.handling, artifact.id)

    def test_the_official_files_are_not_edited_to_hide_a_discrepancy(self):
        self.assertEqual(spec.verify_resources(), [])
