# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ChequeTemplate(models.Model):
    """One bank cheque layout: the physical page and the fields printed on it.

    A template is DATA. Adding a fourth bank is: create a template, enter its physical size, upload a reference image,
    drag the fields into place, save. No Python, no new module. The three initial banks are exactly that, shipped as
    records."""
    _name = 'ncollection.cheque.template'
    _description = 'Cheque template'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    name = fields.Char(required=True, tracking=True)
    bank_name = fields.Char(string='Bank', tracking=True)
    description = fields.Text()
    active = fields.Boolean(default=True, tracking=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company', index=True, tracking=True,
        help="Leave empty to make the template available to every company; set it to keep it to one.")
    # THE PHYSICAL PAGE. Millimetres are the canonical unit; the PDF page is exactly this size.
    width_mm = fields.Float(string='Width (mm)', digits=(8, 2), required=True, default=203.0, tracking=True)
    height_mm = fields.Float(string='Height (mm)', digits=(8, 2), required=True, default=94.0, tracking=True)
    orientation = fields.Selection(
        [('landscape', 'Landscape'), ('portrait', 'Portrait')], compute='_compute_orientation')
    dimensions_confirmed = fields.Boolean(
        string='Dimensions confirmed', tracking=True,
        help="Untick means the size and field positions are provisional: print the calibration sheet, measure the "
             "physical cheque, correct the template, then confirm it.")
    currency_id = fields.Many2one(
        'res.currency', string='Cheque currency', tracking=True,
        help="Set it when the cheque paper is pre-printed for one currency (the word 'Dirhams'): a payment in another "
             "currency is then refused. Leave empty for a currency-neutral cheque.")
    reference_image = fields.Binary(
        string='Reference image', attachment=True,
        help="A scan or photo of the cheque, cropped to the paper edges, used only as a background in the designer. "
             "It is NOT printed unless 'Print the reference image' is ticked.")
    include_background = fields.Boolean(
        string='Print the reference image',
        help="Off for a pre-printed cheque (the normal case): only the variable text is printed. Turn it on only to "
             "print a copy on plain paper.")
    printer_profile_id = fields.Many2one('ncollection.cheque.printer.profile', string='Default printer profile')
    field_ids = fields.One2many('ncollection.cheque.template.field', 'template_id', string='Fields', copy=True)
    field_count = fields.Integer(compute='_compute_field_count')
    notes = fields.Text()

    @api.depends('width_mm', 'height_mm')
    def _compute_orientation(self):
        for template in self:
            template.orientation = 'landscape' if template.width_mm >= template.height_mm else 'portrait'

    @api.depends('field_ids')
    def _compute_field_count(self):
        for template in self:
            template.field_count = len(template.field_ids)

    @api.constrains('width_mm', 'height_mm')
    def _check_page(self):
        for template in self:
            if not (50.0 <= template.width_mm <= 400.0 and 50.0 <= template.height_mm <= 400.0):
                raise ValidationError(self.env._(
                    "A cheque is between 50 and 400 mm on each side; check the physical dimensions."))
            for field in template.field_ids:
                field._check_inside_page()

    def copy_data(self, default=None):
        default = dict(default or {})
        default.setdefault('name', self.env._("%s (copy)", self.name))
        default.setdefault('dimensions_confirmed', False)
        return super().copy_data(default)

    # ------------------------------------------------------------------ what the renderer is handed
    def _spec(self):
        """The plain-data layout the renderer draws: no records, so it can be tested and cached freely."""
        self.ensure_one()
        return {
            'name': self.name,
            'width_mm': self.width_mm,
            'height_mm': self.height_mm,
            'include_background': self.include_background,
            'fields': [field._spec() for field in self.field_ids.sorted(lambda f: (f.sequence, f.id))],
        }

    def _background_bytes(self):
        self.ensure_one()
        import base64
        return base64.b64decode(self.reference_image) if self.reference_image else None

    # ------------------------------------------------------------------ the designer
    def designer_load(self):
        """Everything the drag-and-drop designer needs in one call: the page, the fields as plain dicts, the lists to
        offer, and a sample value per field kind. The designer then reads and writes the FIELD records directly."""
        self.ensure_one()
        from .field_registry import DEFAULT_BOX, SAMPLES
        sample = self.env['ncollection.cheque.values'].sample()
        samples = dict(SAMPLES)
        samples['payee'] = sample['payee']
        samples['amount_words'] = sample['amount_words']['en']
        samples['amount_words_ar'] = sample['amount_words']['ar']
        samples['amount_words_auto'] = sample['amount_words'][sample['lang']]     # the user's own language
        names = [
            'key', 'static_text', 'visible', 'x_mm', 'y_mm', 'width_mm', 'height_mm', 'font', 'size_pt', 'bold',
            'align', 'direction', 'case', 'digits', 'date_format', 'words_lang', 'prefix', 'suffix', 'fit',
            'max_lines', 'sequence', 'name']
        field_model = self.env['ncollection.cheque.template.field']

        def pairs(name):
            # the selection as the field itself declares it, translated through the field's own translations
            return [[value, label] for value, label in field_model._fields[name]._description_selection(self.env)]
        return {
            'id': self.id, 'name': self.name, 'width_mm': self.width_mm, 'height_mm': self.height_mm,
            'has_image': bool(self.reference_image), 'stamp': str(self.write_date or ''),
            'confirmed': self.dimensions_confirmed,
            'fields': self.field_ids.sorted(lambda r: (r.sequence, r.id)).read(names),
            'options': {
                'keys': pairs('key'), 'font': pairs('font'), 'align': pairs('align'),
                'direction': pairs('direction'), 'case': pairs('case'), 'digits': pairs('digits'),
                'date_format': pairs('date_format'), 'words_lang': pairs('words_lang'), 'fit': pairs('fit'),
            },
            'defaults': {key: {'width_mm': box[0][0], 'height_mm': box[0][1], 'size_pt': box[1]}
                         for key, box in DEFAULT_BOX.items()},
            'samples': samples,
        }

    # ------------------------------------------------------------------ actions
    def _preview_action(self, mode, **params):
        self.ensure_one()
        query = '&'.join('%s=%s' % (k, v) for k, v in dict(params, template=self.id, mode=mode).items() if v)
        return {'type': 'ir.actions.act_url', 'url': '/ncollection/cheque/preview?%s' % query, 'target': 'new'}

    def action_preview_design(self):
        return self._preview_action('design')

    def action_preview_print(self):
        return self._preview_action('print', profile=self.printer_profile_id.id)

    def action_calibration_sheet(self):
        return self._preview_action('calibration', profile=self.printer_profile_id.id)

    def action_confirm_dimensions(self):
        self.write({'dimensions_confirmed': True})
        return True
