# -*- coding: utf-8 -*-
"""Document stamp templates: which stamp, on which printed documents, where on the paper, how big.

A company keeps any number of stamp templates ("Official invoice stamp" on customer invoices and credit notes, "Paid"
on payment receipts, "Approved" on purchase orders...). Each names its image (a PNG; transparency is kept), the
document types it applies to, and where it sits on the PAPER in millimetres, never in screen pixels.

WHICH template stamps a document (``_nc_placements_for``), deterministic and explicit:
  1. only an active template of the document's OWN company, whose document types include this kind of document;
  2. one that names the report's paper format beats one that names none (a more specific rule beats a general one);
  3. then the higher ``priority``; then the older template.
  Exactly ONE template wins, so a document can never carry accidental duplicate stamps.
  4. when nothing matches, the company's single legacy stamp image (``res.company.nc_stamp``) is the default, placed at
     the bottom of the last page, so a workspace that only ever uploaded that image keeps its stamp.

The stamp is drawn on the finished PDF of EACH record (see ``ir_actions_report``), so View, Print, an emailed PDF and a
stored copy are all the same stamped document.
"""
import base64
import io

from PIL import Image

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from . import stamp_overlay

# page_scope
PAGE_SCOPES = [('first', 'First page'), ('last', 'Last page'), ('all', 'Every page')]

# Standard sheet sizes in mm (portrait). Anything else a paper format names falls back to A4.
_SHEETS_MM = {
    'A3': (297.0, 420.0), 'A4': (210.0, 297.0), 'A5': (148.0, 210.0), 'A6': (105.0, 148.0),
    'B4': (250.0, 353.0), 'B5': (176.0, 250.0), 'Letter': (215.9, 279.4), 'Legal': (215.9, 355.6),
    'Tabloid': (279.4, 431.8), 'Ledger': (431.8, 279.4), 'Executive': (184.2, 266.7),
}
_DEFAULT_SHEET = _SHEETS_MM['A4']

# The legacy company-wide stamp: at most this big, this far from the right edge and this far above the bottom edge.
# Who may read the raw stamp image and change a stamp. The image is a company seal: a user who can print a stamped
# document must not be able to fetch the seal itself (/web/image/...), so the binary fields carry this group and the
# server reads the bytes for the overlay only.
STAMP_MANAGER_GROUP = 'ncollection_branding.group_stamp_manager'
LEGACY_MAX_MM = (60.0, 32.0)
LEGACY_RIGHT_MARGIN_MM = 15.0
LEGACY_BOTTOM_MM = 50.0


def sheet_mm(paperformat):
    """``(width, height)`` in mm of the sheet a paper format describes (orientation applied)."""
    if paperformat and paperformat.format == 'custom' and paperformat.page_width and paperformat.page_height:
        width, height = float(paperformat.page_width), float(paperformat.page_height)
    else:
        width, height = _SHEETS_MM.get(paperformat.format if paperformat else 'A4', _DEFAULT_SHEET)
    if paperformat and paperformat.orientation == 'Landscape' and width < height:
        width, height = height, width
    return width, height


def _png_aspect(raw):
    """width / height of a PNG, or 1.0 when it cannot be read."""
    try:
        width, height = Image.open(io.BytesIO(raw)).size
        return width / height if height else 1.0
    except Exception:                                                  # noqa: BLE001 - an unreadable image is just 1:1
        return 1.0


class NcDocumentStamp(models.Model):
    _name = 'ncollection.document.stamp'
    _description = 'Document stamp template'
    _order = 'company_id, priority desc, id'

    name = fields.Char(string='Stamp', required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    image = fields.Binary(
        string='Stamp image', attachment=True, required=True, copy=False, groups=STAMP_MANAGER_GROUP,
        help="A PNG (transparency is kept, the proportions are never changed for you), up to 2 MB and 4000 pixels on "
             "a side. It is stored exactly as uploaded.")
    document_type_ids = fields.Many2many(
        'ncollection.document.type', 'ncollection_document_stamp_type_rel', 'stamp_id', 'type_id',
        string='Applies to', required=True,
        help="The kinds of document this stamp is printed on. A stamp applies to nothing until it names at least one.")
    paper_format_ids = fields.Many2many(
        'report.paperformat', 'ncollection_document_stamp_paperformat_rel', 'stamp_id', 'paperformat_id',
        string='Only on paper format',
        help="Optional. Leave empty for any paper. Name a paper format and this stamp applies only to documents "
             "printed on it, and wins over a stamp that names none.")
    priority = fields.Integer(
        default=10, help="When several stamps could apply to a document, the HIGHER priority is the one printed.")
    page_scope = fields.Selection(PAGE_SCOPES, string='Printed on', default='last', required=True)
    x_mm = fields.Float(string='From the left (mm)', default=150.0, help="Distance from the paper's left edge to the "
                        "stamp's left edge.")
    y_mm = fields.Float(string='From the top (mm)', default=225.0, help="Distance from the paper's top edge to the "
                        "stamp's top edge.")
    width_mm = fields.Float(string='Width (mm)', default=40.0)
    height_mm = fields.Float(string='Height (mm)', default=40.0)
    keep_ratio = fields.Boolean(
        string='Keep proportions', default=True,
        help="Changing the width (or the height) changes the other so the image is never stretched.")
    aspect_ratio = fields.Float(string='Image ratio', compute='_compute_aspect_ratio', digits=(12, 4))
    sheet_width_mm = fields.Float(compute='_compute_sheet', string='Paper width (mm)')
    sheet_height_mm = fields.Float(compute='_compute_sheet', string='Paper height (mm)')

    # ------------------------------------------------------------------ validation
    @api.constrains('x_mm', 'y_mm', 'width_mm', 'height_mm')
    def _check_geometry(self):
        for stamp in self:
            if not (0 <= stamp.x_mm <= 2000 and 0 <= stamp.y_mm <= 2000):
                raise ValidationError(self.env._("The position of %(name)s must be inside the paper, in millimetres.",
                                                 name=stamp.name))
            if not (1.0 <= stamp.width_mm <= 400.0 and 1.0 <= stamp.height_mm <= 400.0):
                raise ValidationError(self.env._("The size of %(name)s must be between 1 and 400 millimetres.",
                                                 name=stamp.name))

    @api.constrains('document_type_ids')
    def _check_document_types(self):
        """The ORM does not enforce ``required`` on a many2many, and a stamp that names no document applies to nothing
        (never to "everything"): refuse it on every write path, not only in the form."""
        for stamp in self:
            if not stamp.document_type_ids:
                raise ValidationError(self.env._(
                    "Choose at least one kind of document for %(name)s.", name=stamp.name))

    def _nc_check_company_allowed(self, company_id):
        """A stamp may only be put in a company the user belongs to: the company record rule judges the row as it is,
        so without this a manager could move a stamp into a company they cannot see and have it win there."""
        if self.env.su or not company_id:
            return
        if company_id not in self.env.user.company_ids.ids:
            raise AccessError(self.env._("You are not allowed to put a stamp in that company."))

    def _nc_check_kinds_allowed(self):
        """A user who is not a Settings administrator may only work on stamps whose kinds ALL name a group they hold
        (``ncollection.document.type.manager_group_ids``: accounting kinds -> the Accounting Adviser). This is the
        authorisation; the form's domain only narrows the choices. It looks at the stored kinds, so a crafted
        payload, a many2many command, a copy or a context key cannot reach a kind the user is not allowed to stamp."""
        user = self.env.user
        if self.env.su or user.has_group('base.group_system'):
            return
        mine = user.all_group_ids
        for stamp in self:
            refused = stamp.document_type_ids.filtered(lambda kind: not (kind.manager_group_ids & mine))
            if refused:
                raise AccessError(self.env._(
                    "You are not allowed to configure a stamp for: %(kinds)s.",
                    kinds=', '.join(refused.mapped('name'))))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._nc_check_company_allowed(vals.get('company_id') or self.env.company.id)
            self.env['res.company']._nc_validate_stamp(vals.get('image'))
        records = super().create(vals_list)
        records._nc_check_kinds_allowed()
        return records

    def write(self, vals):
        if 'company_id' in vals:
            self._nc_check_company_allowed(vals['company_id'])
        if 'image' in vals:
            self.env['res.company']._nc_validate_stamp(vals['image'])
        self._nc_check_kinds_allowed()                  # what is there now
        result = super().write(vals)
        self._nc_check_kinds_allowed()                  # what it has become
        return result

    def unlink(self):
        self._nc_check_kinds_allowed()
        return super().unlink()

    # ------------------------------------------------------------------ computed
    @api.depends('image')
    def _compute_aspect_ratio(self):
        for stamp in self:
            # the web client reads a binary as its size text; the bytes are read by the server for THIS
            # computation only
            image = stamp.sudo().with_context(bin_size=False).image
            raw = base64.b64decode(image) if image else b''
            stamp.aspect_ratio = _png_aspect(raw) if raw else 1.0

    @api.depends('document_type_ids.report_ids', 'company_id', 'paper_format_ids')
    def _compute_sheet(self):
        for stamp in self:
            width, height = sheet_mm(stamp._nc_paperformat())
            stamp.sheet_width_mm, stamp.sheet_height_mm = width, height

    def _nc_paperformat(self):
        """The paper format this stamp is designed against: the one it names, else that of the first report of its
        first document type, else the company's."""
        self.ensure_one()
        if self.paper_format_ids:
            return self.paper_format_ids[:1]
        report = self.document_type_ids.report_ids[:1]
        return report.paperformat_id or self.company_id.paperformat_id or self.env['report.paperformat']

    @api.onchange('image')
    def _onchange_image_ratio(self):
        for stamp in self:
            if stamp.image and stamp.keep_ratio and stamp.aspect_ratio:
                stamp.height_mm = round(stamp.width_mm / stamp.aspect_ratio, 2)

    @api.onchange('width_mm')
    def _onchange_width(self):
        for stamp in self:
            if stamp.keep_ratio and stamp.aspect_ratio and stamp.width_mm:
                stamp.height_mm = round(stamp.width_mm / stamp.aspect_ratio, 2)

    @api.onchange('height_mm')
    def _onchange_height(self):
        for stamp in self:
            if stamp.keep_ratio and stamp.aspect_ratio and stamp.height_mm:
                stamp.width_mm = round(stamp.height_mm * stamp.aspect_ratio, 2)

    # ------------------------------------------------------------------ which stamp, where
    def _nc_placement(self, override=None):
        """This template as the overlay engine wants it. ``override`` (preview only) replaces the geometry."""
        self.ensure_one()
        values = {
            'x_mm': self.x_mm, 'y_mm': self.y_mm, 'width_mm': self.width_mm, 'height_mm': self.height_mm,
            'pages': self.page_scope,
        }
        for key in values:
            if override and override.get(key) is not None:
                values[key] = override[key]
        # The ONE read of the raw image besides the ratio: the server-side overlay needs the bytes to draw the
        # stamp on a
        # document the user may print, but the user may not fetch them (the field is group-restricted).
        values['image'] = base64.b64decode(self.sudo().with_context(bin_size=False).image)
        values['stamp_id'] = self.id
        return values

    @api.model
    def _nc_record_company(self, record):
        company = record.company_id if 'company_id' in record._fields and record.company_id else self.env.company
        return company

    @api.model
    def _nc_winner(self, report, record):
        """The ONE template that stamps ``record`` printed by ``report``, or an empty recordset."""
        company = self._nc_record_company(record)
        kinds = self.env['ncollection.document.type']._nc_types_for(report, record)
        if not kinds:
            return self.browse()
        paperformat = report.paperformat_id or company.paperformat_id
        candidates = self.search([('company_id', '=', company.id)]).filtered(
            lambda stamp: stamp.document_type_ids & kinds
            and stamp.sudo().with_context(bin_size=True).image            # a stamp with no image stamps nothing
            and (not stamp.paper_format_ids or paperformat in stamp.paper_format_ids))
        if not candidates:
            return self.browse()
        return candidates.sorted(lambda stamp: (not stamp.paper_format_ids, -stamp.priority, stamp.id))[:1]

    @api.model
    def _nc_may_preview(self, preview, report=None, record=None):
        """The preview context is honoured only for someone who may edit that stamp (the designer's user, for a kind
        they may stamp) and only on a document of that stamp's own kind:
        a client can put any key in a report context, and
        must not be able to print an official stamp on another kind of document with it."""
        stamp_id = preview.get('stamp_id') if isinstance(preview, dict) else None
        if isinstance(stamp_id, bool) or not isinstance(stamp_id, int):
            return False
        if not self.env.context.get('report_pdf_no_attachment'):
            return False           # only the designer's render (it stores no copy); a download must never store a forged PDF
        stamp = self.browse(stamp_id).exists()
        try:
            stamp.check_access('write')
            stamp._nc_check_kinds_allowed()
        except AccessError:
            return False
        if stamp and report is not None and record is not None:
            kinds = self.env['ncollection.document.type']._nc_types_for(report, record)
            return bool(stamp.document_type_ids & kinds)
        return bool(stamp)

    @api.model
    def _nc_apply_to_pdf(self, report, record, pdf_bytes):
        """``pdf_bytes`` with the right stamp drawn on it; the very same bytes when there is none."""
        preview = self.env.context.get('nc_stamp_preview')
        # the designer's real preview: the named stamp, as dragged
        if preview and self._nc_may_preview(preview, report, record):
            stamp = self.browse(preview['stamp_id']).exists()
            return stamp_overlay.overlay_stamps(pdf_bytes, [stamp._nc_placement(preview)]) if stamp else pdf_bytes
        stamp = self._nc_winner(report, record)
        if stamp:
            return stamp_overlay.overlay_stamps(pdf_bytes, [stamp._nc_placement()])
        legacy = self._nc_legacy_placement(report, record, pdf_bytes)
        return stamp_overlay.overlay_stamps(pdf_bytes, [legacy]) if legacy else pdf_bytes

    @api.model
    def _nc_legacy_placement(self, report, record, pdf_bytes):
        """The company's single stamp image, for a document no template covers: bottom of the last page, never wider
        than 60 mm or taller than 32 mm, proportions kept."""
        company = self._nc_record_company(record)
        seal = company.sudo().with_context(bin_size=False).nc_stamp  # restricted field: server read only
        if not seal or not self.env['ncollection.document.type']._nc_types_for(report, record):
            return None
        raw = base64.b64decode(seal)
        page_width, page_height = stamp_overlay.page_size_mm(pdf_bytes, -1)
        aspect = _png_aspect(raw)
        width = min(LEGACY_MAX_MM[0], LEGACY_MAX_MM[1] * aspect)
        height = width / aspect
        return {'image': raw, 'x_mm': page_width - LEGACY_RIGHT_MARGIN_MM - width,
                'y_mm': page_height - LEGACY_BOTTOM_MM - height, 'width_mm': width, 'height_mm': height,
                'pages': 'last'}
