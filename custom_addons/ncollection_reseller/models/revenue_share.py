# -*- coding: utf-8 -*-
"""Reseller revenue-share reporting (P10-T09) — admin DB.

A read-only rollup of each reseller's sub-tenant recurring revenue (MRR) and the
share owed, aggregated from existing admin-DB subscription data (never a tenant
DB). This ticket covers REPORTING only; automated payout invoicing is an
explicit follow-up. Any future payout write must post to admin-DB account.move
and carry the `# arch-guard: admin-db-billing` marker.
"""

from odoo import api, fields, models
from odoo.tools import SQL


class ResellerRevenueShare(models.Model):
    _name = 'ncollection.reseller.revenue.share'
    _description = 'Reseller Revenue Share (report)'
    _auto = False
    _order = 'reseller_id'

    reseller_id = fields.Many2one('ncollection.reseller', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    subtenant_count = fields.Integer(string='Sub-Tenants', readonly=True)
    total_mrr = fields.Monetary(
        string='Total MRR', readonly=True, currency_field='currency_id')
    revenue_share_pct = fields.Float(string='Share (%)', readonly=True)
    share_amount = fields.Monetary(
        string='Share Owed (monthly)', readonly=True, currency_field='currency_id')

    def init(self):
        # A SQL view: one row per reseller AND currency. Currency is in the
        # GROUP BY (never MAX'd) so MRRs in different currencies are NEVER summed
        # as raw numbers — a reseller with multi-currency sub-tenants gets one
        # correct row per currency. subtenant_count is scoped to each row's
        # currency group (tenants whose active subscription is in that currency;
        # tenants with no active subscription fall in the NULL-currency row).
        # ROW_NUMBER supplies the synthetic per-row id an _auto=False model needs.
        self.env.cr.execute(SQL(
            """
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    ROW_NUMBER() OVER (ORDER BY r.id, s.currency_id) AS id,
                    r.id                              AS reseller_id,
                    r.revenue_share_pct               AS revenue_share_pct,
                    s.currency_id                     AS currency_id,
                    COUNT(DISTINCT t.id)              AS subtenant_count,
                    COALESCE(SUM(s.mrr), 0.0)         AS total_mrr,
                    COALESCE(SUM(s.mrr), 0.0) * r.revenue_share_pct / 100.0
                                                      AS share_amount
                  FROM ncollection_reseller r
                  LEFT JOIN ncollection_tenant t
                         ON t.reseller_id = r.id
                  LEFT JOIN ncollection_subscription s
                         ON s.tenant_id = t.id
                        AND s.status = 'active'
                 GROUP BY r.id, r.revenue_share_pct, s.currency_id
            )
            """,
            SQL.identifier(self._table),
        ))

    @api.model
    def action_open(self):
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Reseller Revenue Share'),
            'res_model': self._name,
            'view_mode': 'list',
        }
