"""Duplicate transaction review — after the fact, read-only detection, explained, never deleting anything.

Import already refuses a re-imported line (bank id or fingerprint). This finds what is ALREADY in the database: two
transactions of the same journal with the same date, the same amount and the same label (and the same reference when
both have one). It deliberately does NOT flag:

* two lines that both carry a bank-issued transaction id (FITID, CAMT reference, a CSV id column) and the ids differ:
  the bank itself says they are two transactions (a fingerprint we generated is not a bank id);
* a pair an accountant already marked "Not a duplicate".

Each candidate says why it was proposed. The review can only (a) open the transactions and (b) mark a pair as not a
duplicate. Removing a real duplicate stays the standard, explicit Odoo action on that transaction (unreconcile first
if it was matched), by a user with accounting write rights: nothing here deletes or cancels an entry.
"""
from collections import defaultdict

from odoo import api, fields, models


class DuplicateReview(models.TransientModel):
    _name = 'ncollection.bank.duplicate.review'
    _description = 'Duplicate Transaction Review'

    journal_id = fields.Many2one('account.journal', required=True, readonly=True,
                                 domain="[('type', 'in', ('bank', 'cash'))]")
    candidate_ids = fields.One2many('ncollection.bank.duplicate.candidate', 'review_id', readonly=True)
    candidate_count = fields.Integer(compute='_compute_candidate_count')

    @api.depends('candidate_ids')
    def _compute_candidate_count(self):
        for review in self:
            review.candidate_count = len(review.candidate_ids)

    @staticmethod
    def _norm(text):
        return ' '.join((text or '').lower().split())

    @staticmethod
    def _bank_id(line):
        uid = line.unique_import_id or ''
        return '' if not uid or '-nc-fp-' in uid or uid.startswith('nc-fp-') else uid

    def _find(self):
        self.ensure_one()
        lines = self.env['account.bank.statement.line'].search(
            [('journal_id', '=', self.journal_id.id), ('nc_duplicate_reviewed', '=', False)], order='date, id')
        groups = defaultdict(lambda: self.env['account.bank.statement.line'])
        currency = self.journal_id.currency_id or self.journal_id.company_id.currency_id
        for line in lines:
            groups[(line.date, currency.round(line.amount), self._norm(line.payment_ref))] |= line
        found = []
        for (_date, _amount, _label), group in groups.items():
            if len(group) < 2:
                continue
            for i, first in enumerate(group):
                for second in group[i + 1:]:
                    id_a, id_b = self._bank_id(first), self._bank_id(second)
                    if id_a and id_b and id_a != id_b:
                        continue
                    if first.ref and second.ref and self._norm(first.ref) != self._norm(second.ref):
                        continue
                    reasons = [self.env._("same date"), self.env._("same amount"), self.env._("same label")]
                    if first.ref and second.ref:
                        reasons.append(self.env._("same reference"))
                    if first.partner_id and first.partner_id == second.partner_id:
                        reasons.append(self.env._("same partner"))
                    if id_a and id_a == id_b:
                        reasons.append(self.env._("same bank transaction id"))
                    elif not (id_a and id_b):
                        reasons.append(self.env._("no bank transaction id to tell them apart"))
                    found.append({'line_a_id': first.id, 'line_b_id': second.id, 'reason': ', '.join(reasons)})
        return found

    def action_refresh(self):
        self.ensure_one()
        self.candidate_ids.unlink()
        self.write({'candidate_ids': [fields.Command.create(vals) for vals in self._find()]})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'current',
                'name': self.env._("Duplicate review: %(journal)s", journal=self.journal_id.display_name)}


class DuplicateCandidate(models.TransientModel):
    _name = 'ncollection.bank.duplicate.candidate'
    _description = 'Duplicate Transaction Candidate'

    review_id = fields.Many2one('ncollection.bank.duplicate.review', required=True, ondelete='cascade')
    line_a_id = fields.Many2one('account.bank.statement.line', string='Transaction', required=True, readonly=True)
    line_b_id = fields.Many2one('account.bank.statement.line', string='Possible duplicate', required=True,
                                readonly=True)
    date = fields.Date(related='line_a_id.date')
    amount = fields.Monetary(related='line_a_id.amount', currency_field='currency_id')
    currency_id = fields.Many2one(related='line_a_id.currency_id')
    label = fields.Char(related='line_a_id.payment_ref', string='Label')
    a_reconciled = fields.Boolean(related='line_a_id.is_reconciled', string='First matched')
    b_reconciled = fields.Boolean(related='line_b_id.is_reconciled', string='Second matched')
    reason = fields.Char(readonly=True)

    def action_not_duplicate(self):
        """An accountant's decision, recorded on both lines and in their journal entries' history."""
        (self.line_a_id | self.line_b_id).check_access('write')
        for candidate in self:
            pair = candidate.line_a_id | candidate.line_b_id
            pair.write({'nc_duplicate_reviewed': True})
            for line in pair:
                line.move_id.message_post(body=self.env._(
                    "Reviewed by %(user)s: not a duplicate of %(other)s.", user=self.env.user.name,
                    other=(pair - line).move_id.name), message_type='notification')
        review = self.review_id
        self.unlink()
        return review.action_refresh() if review else True

    def action_open_pair(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.bank.statement.line', 'view_mode': 'list,form',
                'domain': [('id', 'in', (self.line_a_id | self.line_b_id).ids)],
                'name': self.env._("Possible duplicates")}
