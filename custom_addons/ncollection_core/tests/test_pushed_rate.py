# -*- coding: utf-8 -*-
"""Tenant-side apply of a platform-pushed ECB rate (#308).

What must hold:

1. **The derivation is correct**, and derived from THIS tenant's own USD row --
   not from a peg constant copied platform-side, which a future re-peg would
   silently desynchronise.
2. **No USD row, no rate.** A tenant with nothing to derive from is skipped,
   never given an invented number. ARCHITECTURE_SECURITY §11 forbids a
   confidently wrong rate precisely because nothing downstream can detect one.
3. **The whitelist guard still bites.** The rate keys are popped before it
   runs, so this suite proves an *unknown* key is still refused -- otherwise
   the pop would have quietly widened what a compromised sync account can write.
4. **A currency can never fail a licensing push.** This rides the config-sync
   payload whose primary job is license enforcement.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

_USD_PER_EUR = 1.1535
_AED_PER_USD = 3.6725          # the CBUAE peg #46 seeded, mirrored here as data


@tagged("post_install", "-at_install")
class TestPushedRate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['ncollection.workspace.config']
        cls.Rate = cls.env['res.currency.rate']
        Currency = cls.env['res.currency'].with_context(active_test=False)
        cls.eur = Currency.search([('name', '=', 'EUR')], limit=1)
        cls.usd = Currency.search([('name', '=', 'USD')], limit=1)
        cls.root_id = cls.env.company.root_id.id
        if not cls.Config.get_config():
            cls.Config.create({'plan_code': 'STARTER'})

    def _seed_usd_peg(self):
        """The USD row #46 seeds: rate = USD per company currency."""
        self.Rate.search([('currency_id', '=', self.usd.id),
                          ('company_id', 'in', (False, self.root_id))]).unlink()
        return self.Rate.create({
            'currency_id': self.usd.id,
            'company_id': self.root_id,
            'name': '2020-01-01',
            'rate': 1.0 / _AED_PER_USD,
        })

    def _apply(self, usd_per_eur=_USD_PER_EUR, date='2026-08-03'):
        self.Config._nc_apply_pushed_rate(
            {'ecb_usd_per_eur': usd_per_eur, 'ecb_rate_date': date})

    def _eur_rows(self, date='2026-08-03'):
        return self.Rate.search([('currency_id', '=', self.eur.id),
                                 ('company_id', 'in', (False, self.root_id)),
                                 ('name', '=', date)])

    # ---- derivation ------------------------------------------------------

    def test_derives_eur_from_the_tenants_own_usd_row(self):
        """EUR per CC = (EUR per USD) x (USD per CC) = usd_row.rate / usd_per_eur."""
        usd_row = self._seed_usd_peg()
        self._apply()
        rows = self._eur_rows()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows.rate, usd_row.rate / _USD_PER_EUR, places=9)
        # and that is the measured real-world number, not just self-consistent
        self.assertAlmostEqual(rows.rate, 0.23605902, places=6)

    def test_no_usd_row_means_no_rate_is_invented(self):
        self.Rate.search([('currency_id', '=', self.usd.id),
                          ('company_id', 'in', (False, self.root_id))]).unlink()
        self._apply()
        self.assertFalse(self._eur_rows(), "invented a rate with no peg to derive from")

    def test_applying_twice_for_one_date_is_idempotent(self):
        self._seed_usd_peg()
        self._apply()
        self._apply()
        self.assertEqual(len(self._eur_rows()), 1)

    def test_a_new_date_adds_a_dated_row_rather_than_overwriting(self):
        """#46's own guard is coarse ('ever had any rate'); a daily writer must
        not inherit it or it would never write again."""
        self._seed_usd_peg()
        self._apply(date='2026-08-03')
        self._apply(usd_per_eur=1.20, date='2026-08-04')
        self.assertEqual(len(self._eur_rows('2026-08-03')), 1)
        self.assertEqual(len(self._eur_rows('2026-08-04')), 1)

    # ---- refusal ---------------------------------------------------------

    def test_unusable_values_are_refused_not_written(self):
        self._seed_usd_peg()
        for bad in (0, -1.0, None, 'nope', True):
            self._apply(usd_per_eur=bad)
        self._apply(date='not-a-date')
        self.assertFalse(self._eur_rows())

    def test_the_peg_rows_are_never_touched(self):
        usd_row = self._seed_usd_peg()
        before = usd_row.rate
        self._apply()
        self.assertEqual(usd_row.rate, before, "the USD peg was rewritten")

    # ---- the channel it rides on ----------------------------------------

    def test_the_whitelist_still_refuses_an_unknown_key(self):
        """Popping the rate keys must not have widened what can be written."""
        with self.assertRaises(UserError):
            self.Config.sync_from_platform({'wat': 1})

    def test_the_response_advertises_the_capability(self):
        """Its ABSENCE is what protects tenants running older code."""
        res = self.Config.sync_from_platform({'plan_code': 'pro'})
        self.assertTrue(res.get('accepts_rate'))

    def test_a_rate_cannot_fail_a_licensing_push(self):
        self._seed_usd_peg()
        res = self.Config.sync_from_platform({
            'plan_code': 'pro',
            'ecb_usd_per_eur': 'garbage',
            'ecb_rate_date': '2026-08-03',
        })
        self.assertTrue(res.get('ok'))
        self.assertEqual(self.Config.get_config().plan_code, 'pro')
