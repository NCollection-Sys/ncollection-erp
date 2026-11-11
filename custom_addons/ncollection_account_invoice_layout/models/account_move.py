# -*- coding: utf-8 -*-
"""Invoice header: the exact percentage for a fixed-amount discount, and the discount summary above Untaxed Amount.

TOTAL DISCOUNT  = sum over the product lines of (undiscounted tax-excluded line total - booked Amount).
TOTAL AFTER DISCOUNT = Untaxed Amount minus the invoice-level charges (see invoice adjustments). With no charge it
equals
Untaxed Amount; both are derived from the booked lines, so they cannot contradict Untaxed Amount, Taxes and Total.
"""

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    nc_total_discount = fields.Monetary(
        string='Total Discount',
        currency_field='currency_id',
        compute='_compute_nc_discount_totals',
        help="All line discounts, tax-excluded: what the lines would total without any discount, minus what they "
        "total.",
    )
    nc_total_after_discount = fields.Monetary(
        string='Total After Discount',
        currency_field='currency_id',
        compute='_compute_nc_discount_totals',
        help="The discounted, tax-excluded total of the product lines, before tax and before invoice-level charges.",
    )
    nc_has_discount = fields.Boolean(compute='_compute_nc_discount_totals')
    nc_adjusted_untaxed = fields.Monetary(
        string='Untaxed after Charges & Discounts',
        currency_field='currency_id',
        compute='_compute_nc_adjusted_untaxed',
        help="Live preview: product lines + charges - discounts, before tax. Equals Untaxed Amount once saved.",
    )

    def _prepare_product_base_line_for_taxes_computation(self, product_line):
        """A fixed-amount discount feeds the tax engine its EXACT percentage so the subtotal is gross - fixed to
        the cent."""
        base_line = super()._prepare_product_base_line_for_taxes_computation(product_line)
        if self.is_invoice(include_receipts=True) and product_line.nc_discount_mode == 'amount':
            gross = product_line.quantity * product_line.price_unit
            base_line['discount'] = product_line.nc_discount_fixed / gross * 100.0 if gross else 0.0
        return base_line

    @api.depends(
        'invoice_line_ids.price_subtotal',
        'invoice_line_ids.discount',
        'invoice_line_ids.nc_discount_fixed',
        'invoice_line_ids.nc_discount_mode',
        'invoice_line_ids.quantity',
        'invoice_line_ids.price_unit',
        'invoice_line_ids.tax_ids',
        'invoice_line_ids.nc_adjustment_id',
        'amount_untaxed',
    )
    def _compute_nc_discount_totals(self):
        tax_engine = self.env['account.tax']
        for move in self:
            discount = 0.0
            if move.is_invoice(include_receipts=True):
                company = move.company_id or self.env.company
                for line in move.invoice_line_ids.filtered(lambda line: line.display_type == 'product'):
                    base_line = move._prepare_product_base_line_for_taxes_computation(line)
                    base_line['discount'] = 0.0
                    tax_engine._add_tax_details_in_base_line(base_line, company)
                    undiscounted = base_line['tax_details']['raw_total_excluded_currency']
                    discount += move.currency_id.round(undiscounted) - line.price_subtotal
            charges = invoice_discounts = 0.0
            for line in move.invoice_line_ids.filtered(
                lambda line: line.display_type == 'product' and line.nc_adjustment_id
            ):
                if line.nc_adjustment_id.kind == 'discount':
                    invoice_discounts += -line.price_subtotal
                else:
                    charges += line.price_subtotal
            discount = (
                move.currency_id.round(discount + invoice_discounts)
                if move.currency_id
                else discount + invoice_discounts
            )
            move.nc_total_discount = discount
            move.nc_total_after_discount = (
                move.currency_id.round((move.amount_untaxed or 0.0) - charges)
                if move.currency_id
                else (move.amount_untaxed or 0.0) - charges
            )
            move.nc_has_discount = bool(move.currency_id and not move.currency_id.is_zero(discount))

    # ------------------------------------------------------------------ invoice-level charges and discounts
    nc_adjustment_ids = fields.One2many(
        'ncollection.invoice.adjustment', 'move_id', string='Charges & Adjustments', copy=False
    )

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._nc_carry_reversed_adjustments()
        moves._nc_add_default_adjustments()
        moves._nc_sync_adjustments()
        return moves

    def write(self, vals):
        if 'invoice_line_ids' in vals:
            self = self.with_context(nc_line_command_write=True)
        result = super().write(vals)
        if not self.env.context.get('nc_adjustment_sync') and (
            {'invoice_line_ids', 'line_ids', 'currency_id', 'invoice_date'} & set(vals)
        ):
            self._nc_sync_adjustments()
        return result

    def _nc_add_default_adjustments(self):
        Type = self.env['ncollection.invoice.adjustment.type']
        Adjustment = self.env['ncollection.invoice.adjustment'].with_context(nc_adjustment_sync=True)
        for move in self.filtered(lambda m: m.state == 'draft' and m.is_invoice(include_receipts=True)):
            existing = move.nc_adjustment_ids.type_id
            for type_ in Type.search([('company_id', '=', move.company_id.id), ('auto_add', '=', True)]):
                if type_ not in existing and type_._applies_to_move(move):
                    Adjustment.create(
                        {
                            'move_id': move.id,
                            'type_id': type_.id,
                            'value': type_.default_value,
                            'tax_ids': [(6, 0, type_.tax_ids.ids)],
                            'analytic_distribution': type_.analytic_distribution,
                        }
                    )

    def _nc_adjustment_amounts(self):
        """{adjustment: money} for the adjustments of ONE invoice (saved or still being edited): the single
        calculation behind the stored Amount, the booked lines and the live onchange."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        basis = currency.round(self._nc_adjustment_basis())
        adjustments = self.nc_adjustment_ids.sorted(lambda a: (a.sequence, a._origin.id or 0))
        amounts = {}
        discount_total = 0.0
        for adj in adjustments.filtered(lambda a: a.kind == 'discount'):
            amount = adj.value if adj.value_mode == 'fixed' else basis * adj.value / 100.0
            amounts[adj] = currency.round(min(amount, max(basis - discount_total, 0.0)))
            discount_total += amounts[adj]
        for adj in adjustments.filtered(lambda a: a.kind == 'charge'):
            if adj.value_mode == 'fixed':
                amount = adj.value
            else:
                base = basis - discount_total if adj.type_id.basis == 'lines_after_discount' else basis
                amount = base * adj.value / 100.0
            amounts[adj] = currency.round(amount)
        return amounts

    @api.depends('invoice_line_ids.price_subtotal', 'nc_adjustment_ids.amount', 'nc_adjustment_ids.kind')
    def _compute_nc_adjusted_untaxed(self):
        for move in self:
            charges = sum(a.amount for a in move.nc_adjustment_ids if a.kind == 'charge')
            discounts = sum(a.amount for a in move.nc_adjustment_ids if a.kind == 'discount')
            move.nc_adjusted_untaxed = move._nc_adjustment_basis() + charges - discounts

    @api.onchange('invoice_line_ids', 'nc_adjustment_ids')
    def _onchange_nc_adjustments(self):
        """LIVE recalculation while editing: every adjustment row shows its money, and an adjustment whose invoice line
        already exists re-prices that line, so Untaxed / Taxes / Total follow at once. A row added just now is booked
        as its line when the invoice is saved (never twice: the booking is keyed on the adjustment)."""
        for move in self:
            if move.state != 'draft' or not move.is_invoice(include_receipts=True) or not move.nc_adjustment_ids:
                continue
            for adj, amount in move._nc_adjustment_amounts().items():
                sign = -1.0 if adj.kind == 'discount' else 1.0
                adj.amount = amount
                key = adj._origin.id
                lines = move.invoice_line_ids.filtered(
                    lambda ln, key=key: key and ln.nc_adjustment_id and ln.nc_adjustment_id._origin.id == key
                )
                if not lines:
                    continue
                parts = {self._nc_tax_key(taxes): part for taxes, part in move._nc_adjustment_split(adj, amount)}
                for line in lines:
                    part = parts.get(self._nc_tax_key(line.tax_ids))
                    if part is None:
                        continue
                    price = move._nc_adjustment_price_unit(line.tax_ids, sign * part)
                    if line.price_unit != price:
                        line.price_unit = price

    @staticmethod
    def _nc_tax_key(taxes):
        return tuple(sorted(taxes._origin.ids if hasattr(taxes, '_origin') else taxes.ids))

    def _nc_adjustment_split(self, adj, amount):
        """[(taxes, tax-excluded amount)] for ONE adjustment of this invoice: the booked line(s) it needs.

        Taxes from 'The taxes below': one part with the adjustment's taxes. Taxes from 'the invoice lines': one part
        per tax group of the product lines, pro rata to the group's tax-excluded subtotal; the rounding remainder goes
        to the largest group, so the parts always add up to the amount exactly."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        if adj.tax_mode != 'lines':
            return [(adj.tax_ids, amount)]
        groups = {}
        for line in self.invoice_line_ids.filtered(
            lambda ln: ln.display_type == 'product' and not ln.nc_adjustment_id
        ):
            key = self._nc_tax_key(line.tax_ids)
            taxes, subtotal = groups.get(key, (line.tax_ids, 0.0))
            groups[key] = (taxes, subtotal + line.price_subtotal)
        basis = sum(subtotal for _taxes, subtotal in groups.values())
        if not groups or currency.is_zero(basis):
            return [(adj.tax_ids, amount)]
        ordered = sorted(groups.values(), key=lambda g: abs(g[1]), reverse=True)
        parts = []
        allocated = 0.0
        for taxes, subtotal in ordered[1:]:
            part = currency.round(amount * subtotal / basis)
            parts.append((taxes, part))
            allocated += part
        parts.insert(0, (ordered[0][0], currency.round(amount - allocated)))
        return [(taxes, part) for taxes, part in parts if not currency.is_zero(part)]

    def _nc_adjustment_price_unit(self, taxes, amount):
        """The unit price that books ``amount`` TAX-EXCLUDED with these taxes: a price-included tax is added on top,
        so the line's tax-excluded subtotal is the adjustment's amount, not the amount minus its VAT."""
        taxes = taxes._origin if hasattr(taxes, '_origin') else taxes
        included = taxes.filtered('price_include')
        if not included:
            return amount
        currency = self.currency_id or self.company_id.currency_id
        result = taxes.compute_all(amount, currency=currency, quantity=1.0, handle_price_include=False)
        extra = sum(tax['amount'] for tax in result['taxes'] if tax['id'] in included.ids)
        return currency.round(amount + extra)

    def _get_move_lines_to_report(self):
        """The printed item list: the commercial lines only. A booked charge / discount prints in its own section
        (``_nc_report_adjustments``), never as a product row as well."""
        return super()._get_move_lines_to_report().filtered(lambda line: not line.nc_adjustment_id)

    def _nc_report_adjustments(self):
        """The Charges & Discounts section of the printed invoice, from the BOOKED lines (what the journal entry and
        the totals contain), one row per adjustment: label, its taxes, signed amount tax-excluded and tax-included."""
        self.ensure_one()
        rows = []
        lines = self.invoice_line_ids.filtered(lambda line: line.display_type == 'product' and line.nc_adjustment_id)
        for adj in lines.nc_adjustment_id.sorted(lambda a: (a.sequence, a.id)):
            booked = lines.filtered(lambda line, adj=adj: line.nc_adjustment_id == adj)
            rows.append(
                {
                    'name': adj.type_id.name,
                    'kind': adj.kind,
                    'value': adj.value,
                    'value_mode': adj.value_mode,
                    'taxes': ', '.join(booked.tax_ids.mapped(lambda tax: tax.invoice_label or tax.name)),
                    'subtotal': sum(booked.mapped('price_subtotal')),
                    'total': sum(booked.mapped('price_total')),
                }
            )
        return rows

    def _nc_carry_reversed_adjustments(self):
        """A credit note (or a reversal) carries the invoice's charges and discounts AS adjustments, linked to the
        lines Odoo copied, so it shows and prints them the same way and never as product rows.

        Called from ``create``: Odoo creates a reversal with ``reversed_entry_id`` set and the source's lines copied
        (the link is copy=False). ``_reverse_moves`` itself is an engine method this module must not override
        (ncollection_account_core/tests/test_engine_boundary.py)."""
        Adjustment = self.env['ncollection.invoice.adjustment'].with_context(nc_adjustment_sync=True)
        for reverse in self.filtered(lambda m: m.reversed_entry_id and m.is_invoice(include_receipts=True)):
            move = reverse.reversed_entry_id
            if not move.nc_adjustment_ids or reverse.nc_adjustment_ids.filtered(
                lambda a: a.type_id in move.nc_adjustment_ids.type_id
            ):
                continue
            free = reverse.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
            for adj in move.nc_adjustment_ids:
                new_adj = Adjustment.create(
                    {
                        'move_id': reverse.id,
                        'type_id': adj.type_id.id,
                        'value': adj.value,
                        'tax_ids': [(6, 0, adj.tax_ids.ids)],
                        'analytic_distribution': adj.analytic_distribution,
                        'amount': adj.amount,
                    }
                )
                for src in adj.line_ids:
                    match = free.filtered(
                        lambda line, src=src: not line.nc_adjustment_id
                        and line.account_id == src.account_id
                        and line.name == src.name
                        and line.tax_ids == src.tax_ids
                        and line.currency_id.compare_amounts(line.price_unit, src.price_unit) == 0
                    )[:1]
                    if match:
                        match.with_context(nc_adjustment_sync=True, check_move_validity=False).write(
                            {'nc_adjustment_id': new_adj.id}
                        )
                        free -= match

    def _nc_adjustment_basis(self):
        """P: the discounted, tax-excluded product lines that are NOT adjustment lines."""
        self.ensure_one()
        return sum(
            self.invoice_line_ids.filtered(
                lambda line: line.display_type == 'product' and not line.nc_adjustment_id
            ).mapped('price_subtotal')
        )

    def _nc_sync_adjustments(self):
        """Book every adjustment of a draft invoice as a real invoice line, in the documented calculation order."""
        if self.env.context.get('nc_adjustment_sync'):
            return
        for move in self.filtered(lambda m: m.state == 'draft' and m.is_invoice(include_receipts=True)):
            adjustments = move.nc_adjustment_ids.sorted(lambda a: (a.sequence, a.id))
            if not adjustments and not move.invoice_line_ids.filtered('nc_adjustment_id'):
                continue
            amounts = move._nc_adjustment_amounts()
            sync = move.with_context(nc_adjustment_sync=True, check_move_validity=False)
            currency = move.currency_id or move.company_id.currency_id
            Line = self.env['account.move.line'].with_context(nc_adjustment_sync=True, check_move_validity=False)
            for adj in adjustments:
                amount = amounts[adj]
                sign = -1.0 if adj.kind == 'discount' else 1.0
                adj.with_context(nc_adjustment_sync=True).write({'amount': amount})
                parts = [] if currency.is_zero(amount) else move._nc_adjustment_split(adj, amount)
                existing = {}
                for line in move.invoice_line_ids.filtered(lambda ln, adj=adj: ln.nc_adjustment_id == adj):
                    key = self._nc_tax_key(line.tax_ids)
                    if key in existing:
                        existing[key + ('dup',)] = line  # a duplicate is never kept
                    else:
                        existing[key] = line
                for taxes, part in parts:
                    line_vals = {
                        'name': adj.type_id.name,
                        'quantity': 1.0,
                        'price_unit': move._nc_adjustment_price_unit(taxes, sign * part),
                        'discount': 0.0,
                        'nc_discount_mode': 'percent',
                        'account_id': adj.type_id.account_id.id,
                        'tax_ids': [(6, 0, taxes.ids)],
                        'analytic_distribution': adj.analytic_distribution or adj.type_id.analytic_distribution,
                        'nc_adjustment_id': adj.id,
                        'sequence': 9000 + adj.sequence,
                    }
                    line = existing.pop(self._nc_tax_key(taxes), None)
                    if line:
                        line.with_context(nc_adjustment_sync=True, check_move_validity=False).write(line_vals)
                    else:
                        Line.create(dict(line_vals, move_id=move.id, display_type='product'))
                stale = self.env['account.move.line'].concat(*existing.values())
                if stale:
                    stale.with_context(nc_adjustment_sync=True).unlink()
            # a line whose adjustment is gone must not stay booked
            orphan = sync.invoice_line_ids.filtered(
                lambda line: line.nc_adjustment_id and line.nc_adjustment_id not in adjustments
            )
            if orphan:
                orphan.with_context(nc_adjustment_sync=True).unlink()

    def unlink(self):
        # deleting a draft invoice deletes its booked adjustment lines with it
        return super(AccountMove, self.with_context(nc_adjustment_sync=True)).unlink()
