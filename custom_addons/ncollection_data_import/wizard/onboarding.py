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
import csv
import io

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

# base_import options shared by the wizard's dry-run and the tests (one source
# of truth so they can't drift). CSV, comma-separated, first row is the header.
IMPORT_OPTS = {
    'quoting': '"', 'separator': ',', 'has_headers': True, 'advanced': True,
    'keep_matches': False, 'float_thousand_separator': ',',
    'float_decimal_separator': '.',
}

# The placeholder text shipped in opening_balances.csv's External-ID column — the
# admin must replace it from a Chart of Accounts export. Flagged pre-flight so a
# left-in placeholder never reaches base_import (where identical ids silently
# collapse rows onto one account).
ID_PLACEHOLDER = 'REPLACE_WITH'

# Guard against a huge accidental upload tying up a web worker on the synchronous
# validation dry-run (the real import screen batches; this is a pre-check).
MAX_VALIDATE_ROWS = 5000


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
    if 'missing required' in low or ('required' in low and 'value' in low):
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
        raw = base64.b64decode(self.data_file)
        model = ENTITY_MODEL[self.entity]
        ctx = dict(self.env.context, **ENTITY_CONTEXT.get(self.entity, {}))
        Import = self.env['base_import.import'].with_context(**ctx)
        record = Import.create({
            'res_model': model, 'file': raw, 'file_type': 'text/csv',
            'file_name': self.data_fname or 'import.csv',
        })
        preview = record.parse_preview(IMPORT_OPTS)
        if preview.get('error'):
            return ([{'type': 'error', 'message': preview['error']}], 0)
        headers = preview.get('headers') or []
        columns = self._map_columns(headers, preview.get('matches') or {})
        # Pre-flight checks base_import itself does NOT make on a dry run.
        preflight = self._preflight(raw, columns)
        if any(m['type'] == 'error' for m in preflight):
            # A duplicate/oversize file would corrupt or hang the real import —
            # stop here and report it rather than green-lighting the file.
            return (preflight, 0)
        result = record.execute_import(columns, headers, IMPORT_OPTS, dryrun=True)
        # execute_import returns ids=False (not a list) when rows fail — guard it.
        return (preflight + (result.get('messages') or []),
                len(result.get('ids') or []))

    def _preflight(self, raw, columns):
        """Row-level checks base_import misses on a dry run: an oversize file, a
        left-in id placeholder, and a DUPLICATED id/.id upsert key — the last
        silently merges rows onto one record (data loss) with no error."""
        messages = []
        try:
            rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))[1:]
        except (UnicodeDecodeError, csv.Error):
            return messages  # let base_import report a malformed file
        if len(rows) > MAX_VALIDATE_ROWS:
            return [{'type': 'error', 'message': self.env._(
                "This file has %(n)s rows — too many to validate here. Import it "
                "on Odoo's own import screen, which loads large files in batches.",
                n=len(rows))}]
        key_col = next((i for i, c in enumerate(columns) if c in ('id', '.id')), None)
        if key_col is None:
            return messages
        keys = [r[key_col].strip() for r in rows
                if len(r) > key_col and r[key_col].strip()]
        if any(ID_PLACEHOLDER in k for k in keys):
            messages.append({'type': 'error', 'message': self.env._(
                "The External ID column still contains the placeholder text. "
                "Export your Chart of Accounts first to get each account's real "
                "External ID, then paste them in (one distinct ID per row).")})
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            messages.append({'type': 'error', 'message': self.env._(
                "Some rows share the same External ID (%(ids)s). Each row must "
                "point to a DIFFERENT record — rows with the same ID overwrite "
                "each other instead of creating/updating separately.",
                ids=', '.join(dupes))})
        return messages

    @staticmethod
    def _map_columns(headers, matches):
        """Turn parse_preview's auto-detected ``matches`` into the field list
        execute_import expects. base_import auto-maps a plain ``id`` header, but
        NOT the database-id key ``.id`` — map that literally, else an
        update-by-database-id file is mistaken for a create and fails."""
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
