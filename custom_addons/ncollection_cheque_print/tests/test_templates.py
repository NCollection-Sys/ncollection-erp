# -*- coding: utf-8 -*-
"""Templates are data: constraints, the three shipped banks, a fourth bank without code, and no real financial data."""
import re
from pathlib import Path

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

MODULE_DIR = Path(__file__).resolve().parents[1]
SHIPPED = ('template_mashreq_neobiz', 'template_adcb', 'template_fab')


@tagged('post_install', '-at_install')
class TestTemplates(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['ncollection.cheque.template']
        cls.Field = cls.env['ncollection.cheque.template.field']

    def _new_template(self, **kw):
        values = {'name': 'Synthetic Bank', 'width_mm': 190.0, 'height_mm': 90.0}
        values.update(kw)
        return self.Template.create(values)

    def test_three_initial_templates_ship_as_data(self):
        for xmlid in SHIPPED:
            tpl = self.env.ref('ncollection_cheque_print.' + xmlid)
            self.assertFalse(tpl.dimensions_confirmed, "Sizes from photographs are provisional until measured")
            self.assertTrue(tpl.reference_image)
            keys = set(tpl.field_ids.mapped('key'))
            self.assertTrue({'payee', 'payment_date', 'amount', 'amount_words'} <= keys)
            self.assertFalse(tpl.include_background, "The reference image is never printed by default")
            for field in tpl.field_ids:
                self.assertLessEqual(field.x_mm + field.width_mm, tpl.width_mm + 0.01)
                self.assertLessEqual(field.y_mm + field.height_mm, tpl.height_mm + 0.01)

    def test_fourth_bank_needs_no_code(self):
        tpl = self._new_template(name='Synthetic Fourth Bank', bank_name='Synthetic', width_mm=210.0, height_mm=85.0)
        for key, x, y, w in (('payee', 30, 20, 80), ('amount', 150, 40, 40), ('amount_words', 20, 40, 125),
                             ('payment_date', 150, 10, 40)):
            self.Field.create({'template_id': tpl.id, 'key': key, 'x_mm': x, 'y_mm': y, 'width_mm': w,
                               'height_mm': 8})
        spec = tpl._spec()
        from ..models import renderer
        pdf, warnings = renderer.render_pdf(spec, self.env['ncollection.cheque.values'].sample())
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(warnings, [])

    def test_millimetres_persist_exactly_and_resize(self):
        tpl = self._new_template()
        field = self.Field.create({'template_id': tpl.id, 'key': 'payee', 'x_mm': 12.34, 'y_mm': 5.5,
                                   'width_mm': 60.25, 'height_mm': 7.0})
        field.invalidate_recordset()
        self.assertEqual((field.x_mm, field.y_mm, field.width_mm, field.height_mm), (12.34, 5.5, 60.25, 7.0))
        field.write({'width_mm': 80.0, 'height_mm': 9.5})
        self.assertEqual((field.width_mm, field.height_mm), (80.0, 9.5))

    def test_field_cannot_leave_the_page(self):
        tpl = self._new_template()
        with self.assertRaises(ValidationError), mute_logger('odoo.sql_db'):
            self.Field.create({'template_id': tpl.id, 'key': 'payee', 'x_mm': 150, 'y_mm': 5, 'width_mm': 60,
                               'height_mm': 7})
        field = self.Field.create({'template_id': tpl.id, 'key': 'payee', 'x_mm': 10, 'y_mm': 5, 'width_mm': 60,
                                   'height_mm': 7})
        with self.assertRaises(ValidationError):
            field.write({'y_mm': 88})
        with self.assertRaises(ValidationError):
            tpl.write({'width_mm': 40.0})            # not a cheque; also strands the field

    def test_field_value_constraints(self):
        tpl = self._new_template()
        base = {'template_id': tpl.id, 'x_mm': 5, 'y_mm': 5, 'width_mm': 30, 'height_mm': 6}
        with self.assertRaises(ValidationError):
            self.Field.create(dict(base, key='static_text'))
        with self.assertRaises(ValidationError):
            self.Field.create(dict(base, key='payee', size_pt=0))
        with self.assertRaises(ValidationError):
            self.Field.create(dict(base, key='payee', max_lines=9))
        self.Field.create(dict(base, key='payee'))
        with self.assertRaises(ValidationError):
            self.Field.create(dict(base, key='payee', y_mm=20))          # one payee per cheque
        self.Field.create(dict(base, key='static_text', static_text='A', y_mm=20))
        self.Field.create(dict(base, key='static_text', static_text='B', y_mm=30))   # static text may repeat

    def test_copy_resets_confirmation_and_keeps_fields(self):
        tpl = self.env.ref('ncollection_cheque_print.template_adcb')
        tpl.dimensions_confirmed = True
        clone = tpl.copy()
        self.assertFalse(clone.dimensions_confirmed)
        self.assertEqual(len(clone.field_ids), len(tpl.field_ids))

    def test_designer_load_payload(self):
        tpl = self.env.ref('ncollection_cheque_print.template_fab')
        data = tpl.designer_load()
        self.assertEqual((data['width_mm'], data['height_mm']), (tpl.width_mm, tpl.height_mm))
        self.assertEqual(len(data['fields']), len(tpl.field_ids))
        self.assertIn('amount_words_ar', data['samples'])
        self.assertTrue(data['options']['keys'])

    def test_printer_profile_limits(self):
        Profile = self.env['ncollection.cheque.printer.profile']
        with self.assertRaises(ValidationError):
            Profile.create({'name': 'bad', 'scale_pct': 50})
        with self.assertRaises(ValidationError):
            Profile.create({'name': 'bad', 'offset_x_mm': 40})
        self.assertTrue(Profile.create({'name': 'ok', 'offset_x_mm': 1.2, 'offset_y_mm': -0.8, 'scale_pct': 99.6}))

    def test_module_ships_no_real_financial_data(self):
        """Pattern scan (not a value list, so the test carries no real value): no IBAN, no long digit run, no QR
        payload."""
        iban = re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{15,30}\b')
        long_digits = re.compile(r'\d{9,}', re.ASCII)
        offenders = []
        for path in MODULE_DIR.rglob('*'):
            if path.suffix not in ('.xml', '.py', '.csv', '.po', '.js', '.scss', '.md'):
                continue
            if path.name.startswith('test_'):
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            for pattern in (iban, long_digits):
                for match in pattern.finditer(text):
                    offenders.append('%s: %s' % (path.name, match.group(0)[:6] + '…'))
        self.assertEqual(offenders, [])
