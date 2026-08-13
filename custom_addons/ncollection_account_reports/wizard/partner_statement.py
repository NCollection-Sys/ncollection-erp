# -*- coding: utf-8 -*-
"""F2-T05: Customer / Vendor Statements — a sendable, branded document.

A statement is the Partner Ledger presented as ONE DOCUMENT PER PARTNER: opening
balance, the period's transactions, closing balance — addressed to that partner
and suitable for sending.

IT INHERITS THE PARTNER LEDGER RATHER THAN RECOMPUTING. Odoo prototype
inheritance (`_name` + `_inherit`) copies the ledger's fields and methods onto a
new model, so `_nc_partner_rows()` is literally the same code. That is the point:
a statement whose closing balance disagrees with the ledger it was derived from
is worse than no statement, and the only way to guarantee agreement is to not
have a second implementation. There is no architecture spec for statements —
FINANCIAL_PLATFORM_ARCHITECTURE.md covers Partner Ledger and Aged, not this — so
this builds to the issue's acceptance: branded PDF, sendable, drill-down.

BRANDING IS INHERITED, NOT BUILT. The template calls `web.external_layout`, the
same Odoo layout every sibling report uses, which carries the tenant's logo and
colours from `ncollection_branding`. That is what satisfies "branded per #49" —
no new layout system, and a tenant's branding change reaches statements without
touching this file.

WHAT "SENDABLE" MEANS HERE, stated because the word invites assumption: this
opens Odoo's standard composer with the rendered PDF attached and the partners
pre-filled. It does NOT silently email anyone — a statement is a financial
communication and dispatching it without a human seeing the draft is the wrong
default. OCA's equivalent module has no send action at all.
"""
from odoo import models
from odoo.exceptions import UserError


class NcollectionPartnerStatement(models.TransientModel):
    _name = 'ncollection.account.report.statement'
    # Prototype inheritance: same fields, same row arithmetic, new model.
    _inherit = ['ncollection.account.report.partner.ledger']
    _description = 'NCollection Customer / Vendor Statement'

    def _nc_report_title(self):
        return (self.env._("Customer Statement")
                if self.partner_scope == 'receivable'
                else self.env._("Vendor Statement"))

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_partner_statement'

    def _nc_statement_docs(self):
        """One dict per partner: ``{partner, opening, lines, closing}``.

        Built by regrouping the ledger's own rows, so the closing balance here
        is the ledger's last running balance by construction — not a second
        calculation that could drift from it.
        """
        self.ensure_one()
        rows = self._nc_partner_rows()
        by_partner = {}
        for row in rows:
            by_partner.setdefault(row['partner_id'], []).append(row)

        docs = []
        Partner = self.env['res.partner']
        for partner_id, partner_rows in by_partner.items():
            opening = next((r for r in partner_rows if r['is_initial']), None)
            movements = [r for r in partner_rows if not r['is_initial']]
            docs.append({
                'partner': Partner.browse(partner_id),
                'opening': opening['running_balance'] if opening else 0.0,
                'lines': movements,
                'closing': partner_rows[-1]['running_balance'],
            })
        return sorted(docs, key=lambda d: d['partner'].display_name or '')

    def action_send(self):
        """Open the standard composer with the statement PDF attached.

        Deliberately opens a draft rather than sending: a statement is a
        financial communication to a customer, and a report wizard is not the
        place to dispatch one unseen.
        """
        self.ensure_one()
        docs = self._nc_statement_docs()
        if not docs:
            raise UserError(self.env._(
                "No transactions in this period — there is nothing to send."))

        report = self.env.ref(self._nc_report_action_ref())
        pdf, _ = self.env['ir.actions.report']._render_qweb_pdf(
            report.report_name, res_ids=self.ids)
        attachment = self.env['ir.attachment'].create({
            'name': '%s.pdf' % self._nc_report_title(),
            'raw': pdf,
            'mimetype': 'application/pdf',
            # No res_model/res_id: this is a transient run, and hanging the
            # attachment off a record that is about to vanish would orphan it.
            # `public` stays False — an unattached public PDF is readable by
            # anyone who guesses the id.
        })
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Send Statement"),
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_ids': [d['partner'].id for d in docs],
                'default_subject': self._nc_report_title(),
                'default_attachment_ids': [(6, 0, attachment.ids)],
                'default_composition_mode': 'comment',
            },
        }
