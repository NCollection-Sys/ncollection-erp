# -*- coding: utf-8 -*-
"""Which IFRS component of equity an EQUITY account belongs to (ifrs-full:ComponentsOfEquityAxis).

Odoo classifies accounts by TYPE ("equity" holds share capital, premium, reserves and, on many charts, retained
earnings), not by economic component, so the books alone cannot say which member a balance belongs to. The accountant
states it here, once per account. The Changes in Equity XBRL exports per-component facts ONLY when every equity
account carrying a balance is mapped; otherwise it exports the total column alone (models/xbrl_export.py). The current
year earnings account and the profit and loss accounts are retained earnings by nature and need no mapping.
"""
from odoo import fields, models

EQUITY_MEMBERS = {
    'issued_capital': 'IssuedCapitalMember',
    'share_premium': 'SharePremiumMember',
    'treasury_shares': 'TreasurySharesMember',
    'retained_earnings': 'RetainedEarningsMember',
    'other_reserves': 'OtherReservesMember',
}


class AccountAccount(models.Model):
    _inherit = 'account.account'

    nc_equity_component = fields.Selection(
        [('issued_capital', 'Issued capital'), ('share_premium', 'Share premium'),
         ('treasury_shares', 'Treasury shares'), ('retained_earnings', 'Retained earnings'),
         ('other_reserves', 'Other reserves')],
        string='Component of Equity',
        help="The IFRS component of equity this account belongs to (used by the Changes in Equity XBRL export).")
