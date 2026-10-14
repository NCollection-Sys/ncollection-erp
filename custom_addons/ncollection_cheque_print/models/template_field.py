# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .field_registry import ALIGN, CASE, DATE_FORMATS, DIGITS, DIRECTION, FIELD_KEYS, FIT, FONT_CHOICES, WORDS_LANG

_TOLERANCE = 0.01          # mm: a field may touch the page edge; rounding must not make it invalid


class ChequeTemplateField(models.Model):
    """One printed item of a template: WHAT (a registry key), WHERE (millimetres from the top-left corner) and HOW
    (font, size, alignment, direction, formatting)."""
    _name = 'ncollection.cheque.template.field'
    _description = 'Cheque template field'
    _order = 'template_id, sequence, id'

    template_id = fields.Many2one('ncollection.cheque.template', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    key = fields.Selection(FIELD_KEYS, string='Field', required=True)
    name = fields.Char(compute='_compute_name')
    static_text = fields.Char(help="The fixed text to print (only for a 'Static Text' field).")
    visible = fields.Boolean(string='Printed', default=True,
                             help="Untick to keep the field in the template but not print it.")
    # WHERE: millimetres, origin at the top-left corner of the cheque
    x_mm = fields.Float(string='X (mm)', digits=(8, 2), required=True, help="Distance from the left edge.")
    y_mm = fields.Float(string='Y (mm)', digits=(8, 2), required=True, help="Distance from the top edge.")
    width_mm = fields.Float(string='Width (mm)', digits=(8, 2), required=True, default=50.0)
    height_mm = fields.Float(string='Height (mm)', digits=(8, 2), required=True, default=7.0)
    # HOW
    font = fields.Selection(FONT_CHOICES, default='dejavu', required=True)
    size_pt = fields.Float(string='Size (pt)', digits=(5, 1), default=11.0, required=True)
    bold = fields.Boolean()
    align = fields.Selection(ALIGN, default='auto', required=True)
    direction = fields.Selection(DIRECTION, default='auto', required=True,
                                 help="Right to left for Arabic text. Automatic decides from the first letter.")
    case = fields.Selection(CASE, default='as_is', required=True)
    digits = fields.Selection(DIGITS, default='western', required=True)
    date_format = fields.Selection(DATE_FORMATS, default='dd/mm/yyyy',
                                   help="Only used by a Payment Date field.")
    words_lang = fields.Selection(WORDS_LANG, default='auto', string='Words language',
                                  help="Only used by an Amount in Words field.")
    prefix = fields.Char(help="Printed before the value, e.g. '**' or '#'. A guard against adding digits.")
    suffix = fields.Char(help="Printed after the value.")
    fit = fields.Selection(FIT, default='shrink', required=True,
                           help="Long text either shrinks onto one line or wraps onto several (up to the maximum).")
    max_lines = fields.Integer(string='Maximum lines', default=1)

    @api.depends('key', 'static_text')
    def _compute_name(self):
        labels = dict(self._fields['key']._description_selection(self.env))     # translated
        for field in self:
            label = labels.get(field.key, '')
            if field.key == 'static_text' and field.static_text:
                label = '%s: %s' % (label, field.static_text)
            field.name = label

    @api.constrains('width_mm', 'height_mm', 'size_pt', 'max_lines', 'static_text', 'key', 'template_id')
    def _check_values(self):
        for field in self:
            if field.width_mm <= 0 or field.height_mm <= 0:
                raise ValidationError(self.env._("A field needs a width and a height greater than zero."))
            if field.size_pt <= 0 or field.size_pt > 72:
                raise ValidationError(self.env._("The font size must be between 0 and 72 points."))
            if not 1 <= field.max_lines <= 6:
                raise ValidationError(self.env._("A field holds between 1 and 6 lines."))
            if field.key == 'static_text' and not (field.static_text or '').strip():
                raise ValidationError(self.env._("A static text field needs its text."))
            if field.key != 'static_text':
                twin = self.search_count([('template_id', '=', field.template_id.id), ('key', '=', field.key),
                                          ('id', '!=', field.id)])
                if twin:
                    raise ValidationError(self.env._(
                        "The field '%(name)s' is already on this template; move it instead of adding it twice.",
                        name=field.name))

    @api.constrains('x_mm', 'y_mm', 'width_mm', 'height_mm')
    def _check_position(self):
        for field in self:
            field._check_inside_page()

    def _check_inside_page(self):
        self.ensure_one()
        template = self.template_id
        if self.x_mm < -_TOLERANCE or self.y_mm < -_TOLERANCE:
            raise ValidationError(self.env._("A field cannot start left of or above the cheque."))
        if (self.x_mm + self.width_mm > template.width_mm + _TOLERANCE
                or self.y_mm + self.height_mm > template.height_mm + _TOLERANCE):
            raise ValidationError(self.env._(
                "The field '%(name)s' extends past the cheque (%(w)s x %(h)s mm). Move or shrink it.",
                name=self.name, w=template.width_mm, h=template.height_mm))

    def _spec(self):
        self.ensure_one()
        return {
            'key': self.key, 'static_text': self.static_text, 'visible': self.visible,
            'x_mm': self.x_mm, 'y_mm': self.y_mm, 'width_mm': self.width_mm, 'height_mm': self.height_mm,
            'font': self.font, 'size_pt': self.size_pt, 'bold': self.bold, 'align': self.align,
            'direction': self.direction, 'case': self.case, 'digits': self.digits, 'date_format': self.date_format,
            'words_lang': self.words_lang, 'prefix': self.prefix, 'suffix': self.suffix, 'fit': self.fit,
            'max_lines': self.max_lines,
        }

    @api.model
    def designer_defaults(self, key):
        """Where and how big a NEW field of this kind starts, for the designer's Add button."""
        from .field_registry import DEFAULT_BOX, SAMPLES
        (width, height), size = DEFAULT_BOX.get(key, ((50.0, 7.0), 10.0))
        return {'width_mm': width, 'height_mm': height, 'size_pt': size, 'sample': SAMPLES.get(key, '')}
