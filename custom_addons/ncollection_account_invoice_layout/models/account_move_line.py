# -*- coding: utf-8 -*-
"""Invoice line: a monetary Discount that works both ways, and an Amount / Total incl. Tax the user can type.

DEFINITIONS (one set, used everywhere; worked examples in docs/markdown/INVOICE_LAYOUT_AND_CALCULATIONS.md)

* gross            = quantity x price_unit, in the basis of the Price column (tax-excluded, or tax-included when the
                     line's taxes are price-included).
* Disc.%           = Odoo's own ``discount``: the percentage the tax engine applies to the gross.
* Discount         = the same discount as a MONEY amount in the basis of Price (``nc_discount_amount``).
* Amount           = Odoo's ``price_subtotal``: discounted, tax-excluded, rounded by the document currency.
* Total incl. Tax  = Odoo's ``price_total``.

MODE. A line is in one of two modes, never both:

* ``percent``: the user's number is Disc.%; the Discount is derived from it.  Quantity or price change: percentage
stays.
* ``amount``:  the user's number is the Discount; ``nc_discount_fixed`` is authoritative and Disc.% is derived from it.
  Quantity or price change: the money amount stays, the percentage is recomputed.
  The tax engine is fed the EXACT percentage (fixed / gross x 100), not the 2-decimal stored one, so the booked
  subtotal is gross - fixed to the cent and the stored, rounded Disc.% can never move the books.

AMOUNT / TOTAL INCL. TAX are editable. Typing a target solves the unit price with Odoo's tax engine (never a second
tax formula): the engine's result is affine in the price (a percentage tax, a price-included tax and a fixed tax are
all
affine), so two probes give the exact price, which is then snapped to the price precision and the nearest neighbour
that
the engine's own ROUNDING turns into the target is chosen. Quantity, taxes and the discount (percentage or fixed) are
never touched. A target that cannot be reached (zero quantity, a 100 % discount, a fixed tax) is refused with a clear
message instead of booking a wrong figure.

POSTED invoices: none of these fields may be used. Core lets a posted line's price be written (measured,
INVOICE_AMOUNT_AUDIT.md
section 7); this module does not add a second way to do it and does not change that policy.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero, float_round

_SEARCH_RADIUS = 3  # price steps examined each side of the solved price
_PRODUCT = 'product'
_BASIS_FIELDS = {
    'price_unit',
    'quantity',
    'discount',
    'tax_ids',
    'nc_discount_fixed',
    'nc_discount_mode',
    'nc_discount_amount',
    'nc_amount',
    'nc_total_incl',
    'product_id',
}
# What a user can change on a line. Core also writes bookkeeping flags (dirty markers, term keys) on every line while
# it
# synchronises a move; those are not the user's edit and must pass.
_PROTECTED_LINE_FIELDS = {
    'name',
    'quantity',
    'price_unit',
    'discount',
    'tax_ids',
    'account_id',
    'analytic_distribution',
    'product_id',
    'product_uom_id',
    'nc_discount_amount',
    'nc_discount_fixed',
    'nc_discount_mode',
    'nc_amount',
    'nc_total_incl',
    'move_id',
    'display_type',
    'nc_adjustment_id',
    'price_subtotal',
    'price_total',
}


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    nc_discount_mode = fields.Selection(
        [('percent', 'Percentage'), ('amount', 'Fixed amount')],
        string='Discount mode',
        default='percent',
        required=True,
        copy=True,
        help="Which of Disc.% and Discount the user entered. The other one is derived from it.",
    )
    nc_discount_fixed = fields.Monetary(
        string='Fixed discount',
        currency_field='currency_id',
        copy=True,
        help="The discount in money when the mode is Fixed amount, in the basis of the Price column.",
    )
    nc_discount_percent = fields.Float(
        string="Discount percentage (as entered)",
        digits="Discount",
        copy=True,
        help="The percentage the user typed. It is the source of Disc.% in Percentage mode, so a recomputation "
        "never has to read "
        "the field it is computing.",
    )
    discount = fields.Float(compute="_compute_nc_discount", store=True, readonly=False)
    nc_discount_amount = fields.Monetary(
        string='Discount',
        currency_field='currency_id',
        compute='_compute_nc_discount_amount',
        inverse='_inverse_nc_discount_amount',
        help="The discount in money, in the basis of the Price column (quantity x price x Disc.%).",
    )
    nc_amount = fields.Monetary(
        string='Amount',
        currency_field='currency_id',
        compute='_compute_nc_amounts',
        inverse='_inverse_nc_amount',
        help="Discounted, tax-excluded line total. Typing a value solves the unit price.",
    )
    nc_total_incl = fields.Monetary(
        string='Total incl. Tax',
        currency_field='currency_id',
        compute='_compute_nc_amounts',
        inverse='_inverse_nc_total_incl',
        help="Line total with tax. Typing a value solves the unit price.",
    )

    # ------------------------------------------------------------------ discount: percentage <-> money
    @api.depends('nc_discount_mode', 'nc_discount_fixed', 'nc_discount_percent', 'quantity', 'price_unit')
    def _compute_nc_discount(self):
        for line in self:
            if line.nc_discount_mode == 'amount':
                gross = line.quantity * line.price_unit
                line.discount = line.nc_discount_fixed / gross * 100.0 if gross else 0.0
            else:
                line.discount = line.nc_discount_percent

    @api.depends('nc_discount_mode', 'nc_discount_fixed', 'discount', 'quantity', 'price_unit', 'currency_id')
    def _compute_nc_discount_amount(self):
        for line in self:
            if line.nc_discount_mode == 'amount':
                amount = line.nc_discount_fixed
            else:
                amount = line.quantity * line.price_unit * line.discount / 100.0
            line.nc_discount_amount = line.currency_id.round(amount) if line.currency_id else amount

    def _inverse_nc_discount_amount(self):
        for line in self:
            if line.display_type != _PRODUCT:
                continue
            line._nc_refuse_posted()
            line.nc_discount_mode = 'amount'
            line.nc_discount_fixed = line.nc_discount_amount

    @api.onchange('discount')
    def _onchange_nc_discount_percent(self):
        """The user typed Disc.%: the percentage is now the source (a programmatic change never reaches an
        onchange)."""
        for line in self:
            if line.display_type == _PRODUCT:
                line.nc_discount_mode = 'percent'
                line.nc_discount_percent = line.discount

    @api.onchange('nc_discount_amount')
    def _onchange_nc_discount_amount(self):
        for line in self:
            if line.display_type == _PRODUCT:
                line.nc_discount_mode = 'amount'
                line.nc_discount_fixed = line.nc_discount_amount

    @api.constrains('nc_discount_mode', 'nc_discount_fixed', 'quantity', 'price_unit')
    def _check_nc_discount_fixed(self):
        for line in self:
            if line.display_type != _PRODUCT or line.nc_discount_mode != 'amount':
                continue
            gross = line.quantity * line.price_unit
            fixed = line.nc_discount_fixed
            rounding = line.currency_id.rounding if line.currency_id else 0.01
            if float_is_zero(fixed, precision_rounding=rounding):
                continue
            if fixed * gross < 0 or abs(fixed) > abs(gross) + rounding:
                raise ValidationError(
                    self.env._(
                        "The discount of %(fixed)s on line \"%(line)s\" is more than the line's value of %(gross)s "
                        "(quantity x price) or has the opposite sign.",
                        fixed=fixed,
                        line=line.name or '',
                        gross=line.currency_id.round(gross) if line.currency_id else gross,
                    )
                )

    # ------------------------------------------------------------------ write: a typed Disc.% means percentage mode
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'discount' in vals and not {'nc_discount_mode', 'nc_discount_amount', 'nc_discount_fixed'} & set(vals):
                vals['nc_discount_mode'] = 'percent'
                vals['nc_discount_percent'] = vals['discount']
        lines = super().create(vals_list)
        lines._nc_resync_adjustments()
        return lines

    def write(self, vals):
        if self.env.context.get('nc_line_command_write') and set(vals) <= {
            'price_unit',
            'nc_amount',
            'nc_total_incl',
        }:
            # the web client echoes the live-recalculated price of a booked adjustment line when the invoice is saved:
            # the invoice's own sync re-books the authoritative value right after, so the echo is simply not applied
            self -= self.filtered('nc_adjustment_id')
            if not self:
                return True
        self._nc_check_adjustment_lines_untouched(vals)
        if 'discount' in vals and not {'nc_discount_mode', 'nc_discount_amount', 'nc_discount_fixed'} & set(vals):
            vals = dict(vals, nc_discount_mode='percent', nc_discount_percent=vals['discount'])
        result = super().write(vals)
        if _BASIS_FIELDS & set(vals):
            self._nc_resync_adjustments()
        return result

    def unlink(self):
        self._nc_check_adjustment_lines_untouched()
        moves = self.move_id.filtered('nc_adjustment_ids')
        result = super().unlink()
        moves.exists()._nc_sync_adjustments()
        return result

    def _nc_resync_adjustments(self):
        """A product line changed: percentage adjustments are recalculated (the adjustment lines themselves never
        count)."""
        if self.env.context.get('nc_adjustment_sync'):
            return
        moves = self.filtered(
            lambda line: line.display_type == _PRODUCT and not line.nc_adjustment_id
        ).move_id.filtered('nc_adjustment_ids')
        moves._nc_sync_adjustments()

    # ------------------------------------------------------------------ Amount / Total incl. Tax
    @api.depends('price_subtotal', 'price_total')
    def _compute_nc_amounts(self):
        for line in self:
            line.nc_amount = line.price_subtotal
            line.nc_total_incl = line.price_total

    def _inverse_nc_amount(self):
        self._nc_apply_target('amount')

    def _inverse_nc_total_incl(self):
        self._nc_apply_target('total')

    @api.onchange('nc_amount')
    def _onchange_nc_amount(self):
        return self._nc_onchange_target('amount')

    @api.onchange('nc_total_incl')
    def _onchange_nc_total_incl(self):
        return self._nc_onchange_target('total')

    def _nc_refuse_posted(self):
        if self.move_id and self.move_id.state == 'posted':
            raise UserError(
                self.env._(
                    "Posted invoice lines cannot be re-priced here. Reset the invoice to draft first, or issue a "
                    "credit note."
                )
            )

    def _nc_probe(self, price, rounded=False):
        """(tax-excluded, tax-included) total of THIS line if its unit price were ``price``, from Odoo's own engine.

        In fixed-discount mode the money discount stays the same whatever the price is (it is what the user fixed)."""
        self.ensure_one()
        move = self.move_id
        tax_engine = self.env['account.tax']
        company = self.company_id or self.env.company
        base_line = move._prepare_product_base_line_for_taxes_computation(self)
        base_line['price_unit'] = price
        if self.nc_discount_mode == 'amount':
            gross = self.quantity * price
            base_line['discount'] = self.nc_discount_fixed / gross * 100.0 if gross else 0.0
        tax_engine._add_tax_details_in_base_line(base_line, company)
        if rounded:
            tax_engine._round_base_lines_tax_details([base_line], company)
            details = base_line['tax_details']
            return details['total_excluded_currency'], details['total_included_currency']
        details = base_line['tax_details']
        return details['raw_total_excluded_currency'], details['raw_total_included_currency']

    def _nc_solve_price(self, kind, target):
        """The unit price whose booked ``kind`` ('amount' = tax-excluded, 'total' = tax-included) is closest to
        ``target``.

        Returns (price, achieved, exact). Raises UserError when no price can reach any target."""
        self.ensure_one()
        index = 0 if kind == 'amount' else 1
        if not self.quantity:
            raise UserError(
                self.env._("The quantity of \"%s\" is zero, so no unit price can produce a total.", self.name or '')
            )
        first, second = self._nc_probe(1.0)[index], self._nc_probe(2.0)[index]
        slope = second - first
        if float_is_zero(slope, precision_digits=9):
            raise UserError(
                self.env._(
                    "The unit price of \"%s\" has no effect on its total (a 100%% discount, or a fixed tax only), "
                    "so the requested figure cannot be reached.",
                    self.name or '',
                )
            )
        digits = self.env['decimal.precision'].precision_get('Product Price')
        step = 10.0**-digits
        exact_price = 1.0 + (target - first) / slope
        centre = float_round(exact_price, precision_digits=digits)
        best = None
        for offset in range(-_SEARCH_RADIUS, _SEARCH_RADIUS + 1):
            candidate = float_round(centre + offset * step, precision_digits=digits)
            achieved = self._nc_probe(candidate, rounded=True)[index]
            key = (abs(achieved - target), abs(candidate - self.price_unit), abs(candidate - exact_price))
            if best is None or key < best[0]:
                best = (key, candidate, achieved)
        _key, price, achieved = best
        rounding = self.currency_id.rounding if self.currency_id else 0.01
        return price, achieved, float_is_zero(achieved - target, precision_rounding=rounding)

    def _nc_apply_target(self, kind):
        for line in self:
            if line.display_type != _PRODUCT or not line.move_id:
                continue
            line._nc_refuse_posted()
            target = line.nc_amount if kind == 'amount' else line.nc_total_incl
            current = line.price_subtotal if kind == 'amount' else line.price_total
            rounding = line.currency_id.rounding if line.currency_id else 0.01
            if float_is_zero(target - current, precision_rounding=rounding):
                continue  # a value the engine already produces: nothing to solve
            price, _achieved, _exact = line._nc_solve_price(kind, target)
            line.price_unit = price

    def _nc_onchange_target(self, kind):
        warning = None
        for line in self:
            if line.display_type != _PRODUCT or not line.move_id:
                continue
            target = line.nc_amount if kind == 'amount' else line.nc_total_incl
            try:
                price, achieved, exact = line._nc_solve_price(kind, target)
            except UserError as error:
                warning = {'title': self.env._("Cannot reach this figure"), 'message': str(error)}
                continue
            line.price_unit = price
            if not exact:
                warning = {
                    'title': self.env._("Closest figure used"),
                    'message': self.env._(
                        "No unit price at the current price precision gives exactly %(target)s. The closest, "
                        "%(achieved)s, "
                        "was used (unit price %(price)s).",
                        target=target,
                        achieved=achieved,
                        price=price,
                    ),
                }
        return {'warning': warning} if warning else None

    # ------------------------------------------------------------------ the line an invoice adjustment is booked on
    nc_adjustment_id = fields.Many2one(
        'ncollection.invoice.adjustment',
        string='Charge / discount',
        copy=False,
        ondelete='set null',
        index=True,
        help="Set on the line that books an invoice-level charge or discount: it is managed from the Charges & "
        "Adjustments block of the invoice, not edited here.",
    )

    def _nc_check_adjustment_lines_untouched(self, vals=None):
        """A booked adjustment line is changed only by the adjustment itself (a user editing it here would be
        undone)."""
        if self.env.context.get('nc_adjustment_sync'):
            return
        locked = self.filtered('nc_adjustment_id')
        if locked and (vals is None or _PROTECTED_LINE_FIELDS & set(vals)):
            raise UserError(
                self.env._(
                    "\"%s\" is an invoice charge or discount. Change it in the Charges & Adjustments block of the"
                    " invoice.",
                    locked[0].name or '',
                )
            )
