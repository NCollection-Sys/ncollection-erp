# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ChequePrinterProfile(models.Model):
    """What one physical printer does to a page: a small shift and a small scale error.

    Real printers feed paper a millimetre off and scale a page by a fraction of a percent. The profile corrects the
    printed overlay (offsets and scale, from the top-left corner) so a template stays correct in millimetres and only
    the printer needs adjusting. The calibration sheet of a template is how the numbers are measured."""
    _name = 'ncollection.cheque.printer.profile'
    _description = 'Cheque printer profile'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, index=True,
                                 help="Leave empty for a profile every company may use.")
    offset_x_mm = fields.Float(
        string='Horizontal offset (mm)', digits=(6, 2),
        help="Positive moves everything to the right. Measure it with the calibration sheet.")
    offset_y_mm = fields.Float(
        string='Vertical offset (mm)', digits=(6, 2),
        help="Positive moves everything down. Measure it with the calibration sheet.")
    scale_pct = fields.Float(
        string='Scale (%)', digits=(6, 2), default=100.0,
        help="100 prints at actual size. Use a value like 99.6 only if the calibration sheet measures short.")
    notes = fields.Text()

    @api.constrains('offset_x_mm', 'offset_y_mm', 'scale_pct')
    def _check_ranges(self):
        for profile in self:
            if abs(profile.offset_x_mm) > 25 or abs(profile.offset_y_mm) > 25:
                raise ValidationError(self.env._(
                    "An offset larger than 25 mm is not a printer shift; check the page."))
            if not 90.0 <= profile.scale_pct <= 110.0:
                raise ValidationError(self.env._("The scale must be between 90 and 110 percent."))

    def _as_dict(self):
        self.ensure_one()
        return {'offset_x_mm': self.offset_x_mm, 'offset_y_mm': self.offset_y_mm, 'scale_pct': self.scale_pct}
