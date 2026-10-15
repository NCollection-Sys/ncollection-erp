# -*- coding: utf-8 -*-
"""Warehouse barcode workflow on Odoo's own stock engine.

WHY THIS EXISTS. Odoo's scanning app (``stock_barcode``) is Enterprise, and the OCA port (``stock_barcodes``) is not on
19.0. Community does ship everything the scanning needs underneath: product / location / lot barcodes, the barcode
scanner service and the camera scanner. What it lacks is the workflow screen. This service is that screen's server side
and NOTHING more: it never replaces a stock flow. A scan only changes the same fields a person would change by hand on
the picking (``stock.move.line.quantity`` / ``picked`` / ``lot``), and validating calls the stock engine's own
``button_validate`` (its backorder wizard included). An inventory count writes the quant's own ``inventory_quantity``
and applies it with the engine's ``action_apply_inventory``.

SECURITY. Every call runs as the calling user (no sudo anywhere): the picking is read and written under the user's
record rules and ACL, so a user sees and scans only the pickings, products and locations they could open by hand, in
their own company, in this tenant's database. A refusal is reported, never hidden.

STATELESS. The server keeps no scan session: each call carries the picking and the barcode, and answers with the whole
picking state. A "tracked product needs a lot" prompt is a round trip (``lot_for_product_id``), not server memory.
"""
import math

from odoo import api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare

_LIST_LIMIT = 40
_OPEN_STATES = ('assigned', 'confirmed', 'waiting')


class NcStockBarcode(models.AbstractModel):
    _name = 'ncollection.stock.barcode'
    _description = 'NCollection warehouse barcode service'

    # ------------------------------------------------------------------ helpers
    @api.model
    def _reply(self, picking, ok, message, level=None, **extra):
        reply = {'ok': ok, 'message': message, 'level': level or ('success' if ok else 'danger'),
                 'state': self._picking_state(picking) if picking else None}
        reply.update(extra)
        return reply

    @api.model
    def _picking(self, picking_id):
        picking = self.env['stock.picking'].browse(self._id(picking_id)).exists()
        if not picking:
            raise UserError(self.env._("This operation no longer exists."))
        picking.check_access('write')          # a scan IS a write: refuse a user who could not do it by hand
        return picking

    @api.model
    def _picked_quantity(self, move):
        return sum(ml.quantity for ml in move.move_line_ids if ml.picked)

    @api.model
    def _picking_state(self, picking):
        lines = []
        for move in picking.move_ids.filtered(lambda m: m.state != 'cancel'):
            picked = self._picked_quantity(move)
            lines.append({
                'move_id': move.id, 'product_id': move.product_id.id,
                'name': move.product_id.display_name, 'barcode': move.product_id.barcode or '',
                'demand': move.product_uom_qty, 'picked': picked, 'uom': move.product_uom.display_name,
                'tracking': move.has_tracking,
                'lots': sorted({ml.lot_id.name or ml.lot_name for ml in move.move_line_ids
                                if ml.picked and (ml.lot_id or ml.lot_name)}),
                'complete': float_compare(picked, move.product_uom_qty,
                                          precision_rounding=move.product_uom.rounding) >= 0,
            })
        return {
            'id': picking.id, 'name': picking.name, 'state': picking.state, 'type': picking.picking_type_code,
            'partner': picking.partner_id.display_name or '', 'origin': picking.origin or '',
            'location_src': picking.location_id.display_name, 'location_dest': picking.location_dest_id.display_name,
            'lines': lines,
            'complete': bool(lines) and all(line['complete'] for line in lines),
            'any_picked': any(line['picked'] for line in lines),
        }

    # ------------------------------------------------------------------ the operations to work on
    @api.model
    def nc_barcode_operations(self, code=None):
        """Open receipts / deliveries / transfers of the user's own companies (record rules apply)."""
        domain = [('state', 'in', _OPEN_STATES)]
        if code in ('incoming', 'outgoing', 'internal'):
            domain.append(('picking_type_code', '=', code))
        pickings = self.env['stock.picking'].search(domain, order='scheduled_date, id', limit=_LIST_LIMIT)
        return [{'id': p.id, 'name': p.name, 'type': p.picking_type_code, 'state': p.state,
                 'partner': p.partner_id.display_name or '', 'lines': len(p.move_ids)} for p in pickings]

    @api.model
    def nc_barcode_open(self, picking_id):
        picking = self._picking(picking_id)
        if picking.state in ('draft', 'confirmed', 'waiting'):
            picking.action_assign()                         # reserve what is available, exactly as the form's button
        return self._reply(picking, True, self.env._("Operation %s is ready to scan.", picking.name))

    # ------------------------------------------------------------------ scanning
    @api.model
    def _clean_qty(self, qty, default=1.0):
        """A quantity from the client: finite and positive, or a plain refusal (JSON accepts NaN and Infinity)."""
        try:
            qty = float(default if (qty is None or qty is False or qty == '') else qty)
        except (TypeError, ValueError):
            raise UserError(self.env._("The quantity must be a number."))
        if not math.isfinite(qty) or qty <= 0:
            raise UserError(self.env._("The quantity must be positive."))
        return qty

    @api.model
    def _id(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise UserError(self.env._("This request is not valid."))

    # Barcode lookups are scoped to the operation's own company (or shared records): a user who belongs to two
    # companies must never match, or create, a record of the other one.
    @api.model
    def _find_product(self, code, company):
        return self.env['product.product'].search(
            [('barcode', '=', code), ('company_id', 'in', [False, company.id])], limit=1)

    @api.model
    def _find_location(self, code, company):
        return self.env['stock.location'].search(
            [('barcode', '=', code), ('company_id', 'in', [False, company.id])], limit=1)

    @api.model
    def nc_barcode_scan(self, picking_id, barcode, qty=1.0, lot_for_product_id=False, location_id=False):
        """One scan on one operation. Returns the new picking state and what the scan was understood as."""
        picking = self._picking(picking_id)
        code = (barcode or '').strip()
        if not code:
            return self._reply(picking, False, self.env._("Nothing was scanned."))
        if picking.state in ('done', 'cancel'):
            return self._reply(picking, False, self.env._("This operation is already %s.", picking.state))
        qty = self._clean_qty(qty)
        company = picking.company_id

        if lot_for_product_id:                              # the answer to a "which lot?" prompt
            product = self.env['product.product'].browse(self._id(lot_for_product_id)).exists()
            return self._scan_product(picking, product, qty, lot_name=code, location_id=location_id)

        product = self._find_product(code, company)
        if product:
            return self._scan_product(picking, product, qty, location_id=location_id)

        location = self._find_location(code, company)
        if location:
            return self._scan_location(picking, location)

        # a lot / serial number of a product that is on this operation
        lots = self.env['stock.lot'].search(
            [('name', '=', code), ('product_id', 'in', picking.move_ids.product_id.ids),
             ('company_id', 'in', [False, company.id])])
        if len(lots) == 1:
            return self._scan_product(picking, lots.product_id, qty, lot_name=code, location_id=location_id)

        other = self.env['stock.picking'].search([('name', '=', code)], limit=1)
        if other:
            return self._reply(picking, True, self.env._("Operation %s selected.", other.name), 'info',
                               switch_to=other.id)
        return self._reply(picking, False, self.env._(
            "%s is not a product, lot or location of this operation.", code))

    @api.model
    def _location_in_scope(self, picking, location):
        """A location this operation may use: its source (deliveries) or destination (others), or a sub-location."""
        anchor = picking.location_id if picking.picking_type_code == 'outgoing' else picking.location_dest_id
        return (location.parent_path or '').startswith(anchor.parent_path or '-')

    @api.model
    def _scan_location(self, picking, location):
        outgoing = picking.picking_type_code == 'outgoing'
        anchor = picking.location_id if outgoing else picking.location_dest_id
        if not self._location_in_scope(picking, location):
            return self._reply(picking, False, self.env._(
                "%(location)s is outside this operation's %(where)s (%(anchor)s).",
                location=location.display_name, where=self.env._("source") if outgoing else self.env._("destination"),
                anchor=anchor.display_name))
        return self._reply(picking, True, self.env._("Location %s selected.", location.display_name), 'info',
                           location={'id': location.id, 'name': location.display_name})

    @api.model
    def _scan_product(self, picking, product, qty, lot_name=False, location_id=False):
        moves = picking.move_ids.filtered(lambda m: m.product_id == product and m.state not in ('done', 'cancel'))
        if not moves:
            return self._reply(picking, False, self.env._(
                "%(product)s is not part of operation %(picking)s.",
                product=product.display_name, picking=picking.name))
        tracking = product.tracking
        if tracking != 'none' and not lot_name:
            return self._reply(picking, True, self.env._(
                "%s is tracked: scan its lot or serial number.", product.display_name), 'info',
                need_lot={'product_id': product.id, 'name': product.display_name, 'tracking': tracking})
        if tracking == 'serial':
            qty = 1.0
        move = next((m for m in moves if float_compare(self._picked_quantity(m), m.product_uom_qty,
                                                       precision_rounding=m.product_uom.rounding) < 0), moves[0])
        lot = False
        if lot_name:
            lot = self.env['stock.lot'].search([('name', '=', lot_name), ('product_id', '=', product.id)], limit=1)
            if tracking == 'serial' and any(
                    (ml.lot_id.name or ml.lot_name) == lot_name and ml.picked for ml in move.move_line_ids):
                return self._reply(picking, False, self.env._(
                    "Serial number %s was already scanned on this operation.", lot_name))
            if not lot and picking.picking_type_code != 'incoming':
                return self._reply(picking, False, self.env._("Unknown lot or serial %s.", lot_name))
        line = self._line_for(move, lot, lot_name)
        if line and line.picked:
            line.quantity += qty
        elif line:
            line.write({'quantity': qty, 'picked': True})
        else:
            vals = move._prepare_move_line_vals(quantity=qty)
            vals.update({'picked': True, 'move_id': move.id, 'picking_id': picking.id})
            if lot:
                vals['lot_id'] = lot.id
            elif lot_name:
                vals['lot_name'] = lot_name
            line = self.env['stock.move.line'].create(vals)
        if lot and not line.lot_id:
            line.lot_id = lot
        elif lot_name and not lot and not line.lot_name:
            line.lot_name = lot_name
        if location_id:
            self._apply_location(picking, line, self._id(location_id))
        picked = self._picked_quantity(move)
        over = float_compare(picked, move.product_uom_qty, precision_rounding=move.product_uom.rounding) > 0
        message = self.env._("%(product)s: %(picked)s of %(demand)s", product=product.display_name,
                             picked=picked, demand=move.product_uom_qty)
        if over:
            return self._reply(picking, True, message + " " + self.env._("(more than demanded)"), 'warning')
        return self._reply(picking, True, message)

    @api.model
    def _line_for(self, move, lot, lot_name):
        """The move line a scan lands on: the one with the same lot, else an untouched reserved line, else none."""
        lines = move.move_line_ids
        if lot_name:
            same = lines.filtered(
                lambda ml: (ml.lot_id and ml.lot_id == lot) or (ml.lot_name and ml.lot_name == lot_name))
            if same:
                return same[:1]
            free = lines.filtered(lambda ml: not ml.picked and not ml.lot_id and not ml.lot_name)
            return free[:1]
        return (lines.filtered('picked') or lines.filtered(lambda ml: not ml.picked))[:1]

    @api.model
    def _apply_location(self, picking, line, location_id):
        location = self.env['stock.location'].browse(location_id).exists()
        if not location or not self._location_in_scope(picking, location):
            return                                       # the same rule as scanning the location itself
        if picking.picking_type_code == 'outgoing':
            line.location_id = location
        else:
            line.location_dest_id = location

    @api.model
    def nc_barcode_adjust(self, picking_id, move_id, delta):
        """Correct a mis-scan: take ``delta`` (negative) off, or add to, what was picked for one product."""
        picking = self._picking(picking_id)
        move = picking.move_ids.filtered(lambda m: m.id == self._id(move_id))
        if not move:
            raise UserError(self.env._("This line is not part of the operation."))
        if picking.state in ('done', 'cancel'):
            return self._reply(picking, False, self.env._("This operation is already %s.", picking.state))
        picked_lines = move.move_line_ids.filtered('picked').sorted('id', reverse=True)
        if not picked_lines:
            return self._reply(picking, False, self.env._("Nothing has been scanned for this product yet."))
        try:
            delta = float(delta or 0.0)
        except (TypeError, ValueError):
            delta = 0.0
        if not math.isfinite(delta):
            raise UserError(self.env._("The quantity must be a number."))
        if delta >= 0:
            picked_lines[0].quantity += delta
        else:
            remaining = -delta
            for line in picked_lines:
                take = min(line.quantity, remaining)
                if float_compare(line.quantity, take, precision_rounding=line.product_uom_id.rounding) <= 0:
                    line.write({'picked': False, 'quantity': 0.0})
                else:
                    line.quantity -= take
                remaining -= take
                if remaining <= 0:
                    break
        picking.action_assign()                  # whatever was un-picked is reserved again, as on the form
        return self._reply(picking, True, self.env._("%s corrected.", move.product_id.display_name))

    # ------------------------------------------------------------------ validating
    @api.model
    def nc_barcode_validate(self, picking_id):
        """Validate with the stock engine's own ``button_validate``; its wizards (backorder, ...) come back as an
        action."""
        picking = self._picking(picking_id)
        state = self._picking_state(picking)
        if not state['any_picked']:
            raise UserError(self.env._("Scan at least one item before validating."))
        result = picking.button_validate()
        if result is True:
            return self._reply(picking, True, self.env._("Operation %s validated.", picking.name), done=True)
        return self._reply(picking, True, self.env._("Odoo needs a decision to finish this operation."), 'info',
                           done=False, action=result)

    # ------------------------------------------------------------------ inventory count
    @api.model
    def _quants(self, location):
        return self.env['stock.quant'].with_context(inventory_mode=True).search(
            [('location_id', '=', location.id), ('inventory_quantity_set', '=', True)])

    @api.model
    def _count_state(self, location):
        rows = [{'quant_id': q.id, 'name': q.product_id.display_name, 'lot': q.lot_id.name or '',
                 'counted': q.inventory_quantity, 'on_hand': q.quantity, 'diff': q.inventory_diff_quantity}
                for q in self._quants(location)]
        return {'location': {'id': location.id, 'name': location.display_name}, 'rows': rows}

    @api.model
    def nc_barcode_count_open(self, barcode):
        location = self._find_location((barcode or '').strip(), self.env.company)
        if not location or location.usage != 'internal':
            raise UserError(self.env._("Scan the barcode of an internal location to count it."))
        location.check_access('read')
        return self._count_state(location)

    @api.model
    def nc_barcode_count_scan(self, location_id, barcode, qty=1.0, lot_for_product_id=False):
        location = self.env['stock.location'].browse(self._id(location_id)).exists()
        if not location or location.usage != 'internal':
            raise UserError(self.env._("Choose an internal location to count."))
        location.check_access('read')
        code = (barcode or '').strip()
        lot_name = False
        if lot_for_product_id:
            product, lot_name = self.env['product.product'].browse(self._id(lot_for_product_id)).exists(), code
        else:
            product = self._find_product(code, location.company_id or self.env.company)
        if not product:
            return dict(self._count_state(location), ok=False, level='danger',
                        message=self.env._("%s is not a product barcode.", code))
        if product.tracking != 'none' and not lot_name:
            return dict(self._count_state(location), ok=True, level='info',
                        message=self.env._("%s is tracked: scan its lot or serial number.", product.display_name),
                        need_lot={'product_id': product.id, 'name': product.display_name,
                                  'tracking': product.tracking})
        qty = 1.0 if product.tracking == 'serial' else self._clean_qty(qty)
        lot = False
        if lot_name:
            company = location.company_id or self.env.company
            lot = self.env['stock.lot'].search([('name', '=', lot_name), ('product_id', '=', product.id),
                                                ('company_id', 'in', [False, company.id])], limit=1)
            if not lot:
                lot = self.env['stock.lot'].create(
                    {'name': lot_name, 'product_id': product.id, 'company_id': company.id})
        Quant = self.env['stock.quant'].with_context(inventory_mode=True)
        quant = Quant.search([('product_id', '=', product.id), ('location_id', '=', location.id),
                              ('lot_id', '=', lot.id if lot else False)], limit=1)
        if quant:
            counted = (quant.inventory_quantity if quant.inventory_quantity_set else 0.0) + qty
            quant.inventory_quantity = counted
        else:
            Quant.create({'product_id': product.id, 'location_id': location.id, 'inventory_quantity': qty,
                          'lot_id': lot.id if lot else False})
        return dict(self._count_state(location), ok=True, level='success',
                    message=self.env._("%(product)s counted.", product=product.display_name))

    @api.model
    def nc_barcode_count_apply(self, location_id):
        """Apply the count with the stock engine's own ``action_apply_inventory`` (and its rights)."""
        location = self.env['stock.location'].browse(self._id(location_id)).exists()
        if not location:
            raise UserError(self.env._("This location no longer exists."))
        # Odoo's own inventory screen offers "Apply" to inventory managers only; the ORM would let a warehouse user
        # through, so this screen keeps the stricter rule rather than widening it.
        if not self.env.user.has_group('stock.group_stock_manager'):
            raise AccessError(self.env._("Only an inventory manager can apply a count. Your count is saved."))
        quants = self._quants(location)
        if not quants:
            raise UserError(self.env._("Nothing has been counted in this location yet."))
        try:
            result = quants.action_apply_inventory()
        except AccessError:
            raise AccessError(self.env._("You are not allowed to apply a count. Your count is saved."))
        pending = isinstance(result, dict)               # the engine asks for a decision (a wizard) first
        return dict(self._count_state(location), ok=True, level='info' if pending else 'success', applied=not pending,
                    action=result if pending else None,
                    message=(self.env._("Odoo needs a decision to finish this operation.") if pending
                             else self.env._("Inventory adjusted.")))

    @api.model
    def nc_barcode_count_state(self, location_id):
        """The pending count of a location (after a decision wizard closed)."""
        location = self.env['stock.location'].browse(self._id(location_id)).exists()
        if not location:
            raise UserError(self.env._("This location no longer exists."))
        location.check_access('read')
        return self._count_state(location)
