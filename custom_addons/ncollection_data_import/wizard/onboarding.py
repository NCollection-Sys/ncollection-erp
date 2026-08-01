# -*- coding: utf-8 -*-
"""P3-T11: the tenant onboarding data-import wizard.

A thin guide OVER Odoo's native importer — it does NOT re-implement importing.
It (1) serves the starter templates, (2) opens each native import screen in the
right onboarding order, and (3) offers a dry-run "validate" that rephrases
``base_import``'s row errors for a non-technical admin. All the heavy lifting —
CSV parsing, column mapping, create-vs-update, the self-balancing opening move
(``account.account.opening_*``) and inventory adjustment (``stock.quant``) — is
Odoo Community core.
"""
import base64

from odoo import fields, models, tools
from odoo.exceptions import UserError

# Each onboarding entity → the Odoo model its template imports into. Customers
# and suppliers are both res.partner (they differ only by customer/supplier rank
# columns in the template).
ENTITY_MODEL = {
    'customers': 'res.partner',
    'suppliers': 'res.partner',
    'products': 'product.template',
    'opening_stock': 'stock.quant',
    'opening_balances': 'account.account',
}

# stock.quant's inventory_quantity is only writable in inventory mode — the
# context Odoo sets when you open Physical Inventory. Import must carry it too.
ENTITY_CONTEXT = {
    'opening_stock': {'inventory_mode': True},
}

# The recommended import ORDER (dependencies first): accounts + products must
# exist before opening balances / opening stock can reference them.
ENTITY_SEQUENCE = [
    'products', 'customers', 'suppliers', 'opening_stock', 'opening_balances',
]


def nc_friendly_error(message):
    """Rephrase a raw ``base_import`` error into something a non-technical admin
    can act on. Unknown messages pass through unchanged (never hide detail)."""
    if not message:
        return message
    low = message.lower()
    if 'no matching record' in low or 'not found' in low:
        return ("A value in this row doesn't match anything in the system yet "
                "(e.g. a product, account or location name/code that isn't "
                "spelled exactly as it exists). Fix the spelling, or import that "
                "item first. — Details: %s" % message)
    if 'missing required' in low or 'required' in low and 'value' in low:
        return ("A required column is empty in this row. Fill every required "
                "column, then re-validate. — Details: %s" % message)
    if 'already exists' in low or 'duplicate' in low or 'unique' in low:
        return ("This row looks like a duplicate of a record that already "
                "exists. Remove it, or use the External ID column to update "
                "instead of create. — Details: %s" % message)
    return message


class NcollectionDataImportOnboarding(models.TransientModel):
    _name = 'ncollection.data.import.onboarding'
    _description = 'NCollection Tenant Data Import — Onboarding'

    entity = fields.Selection(
        [('products', 'Products'),
         ('customers', 'Customers'),
         ('suppliers', 'Suppliers'),
         ('opening_stock', 'Opening Stock'),
         ('opening_balances', 'Opening Balances')],
        string='Data set', required=True, default='products')
    data_file = fields.Binary(string='File to validate')
    data_fname = fields.Char(string='File name')
    result_html = fields.Html(string='Validation result', readonly=True, sanitize=False)

    # ---- templates ------------------------------------------------------

    def _template_path(self, entity):
        return 'ncollection_data_import/data/templates/%s.csv' % entity

    def action_download_template(self):
        """Serve the starter CSV for the chosen data set (read from the module,
        never from user input — the path is a fixed allow-list key)."""
        self.ensure_one()
        if self.entity not in ENTITY_MODEL:
            raise UserError(self.env._("Pick a data set first."))
        with tools.file_open(self._template_path(self.entity), 'rb') as fh:
            content = fh.read()
        attachment = self.env['ir.attachment'].create({
            'name': '%s_template.csv' % self.entity,
            'type': 'binary',
            'datas': base64.b64encode(content),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/csv',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    # ---- validate (dry-run over base_import) ----------------------------

    def action_validate(self):
        """Dry-run the uploaded file through base_import (no DB writes) and show
        friendly, row-level results. This is the acceptance's 'validation with
        row-level error reporting a non-technical admin can understand'."""
        self.ensure_one()
        if not self.data_file:
            raise UserError(self.env._("Upload a CSV file to validate first."))
        messages, created = self._dry_run()
        self.result_html = self._format_result(messages, created)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _dry_run(self):
        """Run base_import in dryrun mode; return (messages, created_count)."""
        model = ENTITY_MODEL[self.entity]
        ctx = dict(self.env.context, **ENTITY_CONTEXT.get(self.entity, {}))
        Import = self.env['base_import.import'].with_context(**ctx)
        record = Import.create({
            'res_model': model,
            'file': base64.b64decode(self.data_file),
            'file_type': 'text/csv',
            'file_name': self.data_fname or 'import.csv',
        })
        options = {
            'quoting': '"', 'separator': ',', 'has_headers': True,
            'advanced': True, 'keep_matches': False,
            'float_thousand_separator': ',', 'float_decimal_separator': '.',
        }
        preview = record.parse_preview(options)
        if preview.get('error'):
            return ([{'type': 'error', 'message': preview['error']}], 0)
        headers = preview.get('headers') or []
        columns = self._map_columns(headers, preview.get('matches') or {})
        result = record.execute_import(columns, headers, options, dryrun=True)
        # execute_import returns ids=False (not a list) when rows fail — guard it.
        return (result.get('messages') or [], len(result.get('ids') or []))

    @staticmethod
    def _map_columns(headers, matches):
        """Turn parse_preview's auto-detected ``matches`` into the field list
        execute_import expects. base_import does NOT auto-map the special
        ``id`` / ``.id`` update keys, so map those literally — otherwise an
        update-by-external-id file is mistaken for a create and fails."""
        columns = []
        for idx, header in enumerate(headers):
            match = matches.get(idx) or matches.get(str(idx))
            if match:
                columns.append('/'.join(match))
            elif header in ('id', '.id'):
                columns.append(header)
            else:
                columns.append(False)
        return columns

    def _format_result(self, messages, created):
        errors = [m for m in messages if m.get('type') == 'error']
        if not errors:
            return ('<p class="text-success"><b>✓ Looks good.</b> %s row(s) '
                    'validated with no blocking errors — you can import this '
                    'file now.</p>' % created)
        rows = ''.join(
            '<li>%s</li>' % tools.html_escape(nc_friendly_error(m.get('message') or ''))
            for m in errors[:50])
        more = ('<p>…and %s more.</p>' % (len(errors) - 50)) if len(errors) > 50 else ''
        return ('<p class="text-danger"><b>%s issue(s) found — nothing was '
                'imported.</b> Fix these and validate again:</p><ul>%s</ul>%s'
                % (len(errors), rows, more))

    # ---- open the native importer for this data set ---------------------

    def action_open_import(self):
        """Open the native list where Odoo's Import lives, scoped to this data
        set — the admin clicks Odoo's own Import button there."""
        self.ensure_one()
        model = ENTITY_MODEL[self.entity]
        ctx = dict(ENTITY_CONTEXT.get(self.entity, {}))
        names = {
            'customers': self.env._("Customers"),
            'suppliers': self.env._("Vendors"),
            'products': self.env._("Products"),
            'opening_stock': self.env._("Opening Stock (Physical Inventory)"),
            'opening_balances': self.env._("Opening Balances (Chart of Accounts)"),
        }
        if self.entity == 'customers':
            ctx.update(default_customer_rank=1, search_default_customer=1)
        elif self.entity == 'suppliers':
            ctx.update(default_supplier_rank=1, search_default_supplier=1)
        return {
            'type': 'ir.actions.act_window',
            'name': names[self.entity],
            'res_model': model,
            'view_mode': 'list,form',
            'target': 'current',
            'context': ctx,
        }
