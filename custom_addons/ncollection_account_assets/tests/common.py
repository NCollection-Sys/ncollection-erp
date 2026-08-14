# -*- coding: utf-8 -*-
"""Shared fixture for the F4-T03 suites.

Everything here builds a chart that is CORRECT by the guard's rules, so each
test file can break exactly one thing and see only that break. A fixture that
was already slightly wrong would make every failure ambiguous.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class AssetCommon(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Asset = cls.env['account.asset']
        cls.Profile = cls.env['account.asset.profile']
        cls.Transfer = cls.env['ncollection.account.asset.transfer']
        cls.Register = cls.env['ncollection.account.report.asset.register']
        cls.journal = cls.company_data['default_journal_misc']

        cls.gross_it = cls._account('NCA1000', 'IT Equipment', 'asset_fixed')
        cls.accum_it = cls._account(
            'NCA1001', 'Accumulated Depr. — IT', 'asset_fixed')
        cls.gross_veh = cls._account('NCA1100', 'Vehicles', 'asset_fixed')
        cls.accum_veh = cls._account(
            'NCA1101', 'Accumulated Depr. — Vehicles', 'asset_fixed')
        cls.depr_expense = cls._account(
            'NCA6000', 'Depreciation Expense', 'expense_depreciation')

        cls.profile_it = cls._profile('IT Equipment', cls.gross_it, cls.accum_it)
        cls.profile_veh = cls._profile(
            'Vehicles', cls.gross_veh, cls.accum_veh)

    # ---- builders --------------------------------------------------------

    @classmethod
    def _account(cls, code, name, account_type):
        return cls.env['account.account'].create({
            'code': code, 'name': name, 'account_type': account_type,
            'company_ids': [(4, cls.env.company.id)],
        })

    @classmethod
    def _profile(cls, name, gross, accumulated):
        return cls.Profile.create({
            'name': name,
            'account_asset_id': gross.id,
            'account_depreciation_id': accumulated.id,
            'account_expense_depreciation_id': cls.depr_expense.id,
            'journal_id': cls.journal.id,
            'method': 'linear',
            'method_time': 'year',
            'method_number': 5,
            'method_period': 'year',
        })

    @classmethod
    def _asset(cls, name='Laptop', profile=None, value=5000.0,
               start=date(2026, 1, 1), validate=True):
        """A 5,000 asset over 5 years — 1,000 a year, so every expected figure
        below is a round number worked out by hand rather than by re-running the
        code under test."""
        asset = cls.Asset.create({
            'name': name,
            'profile_id': (profile or cls.profile_it).id,
            'purchase_value': value,
            'date_start': start,
        })
        if validate:
            asset.validate()
        return asset

    @classmethod
    def _post_board_through(cls, asset, through):
        """Post every planned depreciation line dated on or before ``through``.

        Returns the number of lines posted, so a test can assert the fixture
        did what it claims — a helper that silently posts nothing would make
        every figure downstream look like a code bug.
        """
        lines = asset.depreciation_line_ids.filtered(
            lambda line: (line.type == 'depreciate' and not line.move_id
                          and line.line_date and line.line_date <= through))
        if lines:
            lines.create_move()
        return len(lines)
