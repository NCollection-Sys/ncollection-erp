# -*- coding: utf-8 -*-
"""Product branding: the identity of THE PRODUCT (the SaaS application itself), in ONE place.

PRODUCT BRAND = what the application is called and looks like (name, logo, favicon, login / administration logos).
COMPANY BRAND = what a tenant company is called and looks like (legal name, company logo, colours, stamp:
``res.company``).
They are different things and never share a field: a tenant's logo is not the product's logo, and printed
invoices carry the company's identity, not the product's.

Every user-visible place that names or draws the product reads it from here, through :meth:`nc_brand`:

  * server templates (title, favicon, login, error pages, offline page, e-mail layouts) call ``nc_product_brand()``,
    a value ``ir.qweb`` adds to every render (``models/ir_qweb.py``), so it works with or without an HTTP request;
  * the web client reads the same dict from ``session_info['nc_product_brand']`` (tab title, error dialogs);
  * the images are served by ``/ncollection/brand/<kind>`` with a version in the URL, so a changed logo or favicon is
    fetched again instead of staying in a browser or CDN cache.

Nothing outside this model hard-codes the product name. Changing it here changes every surface.

Fallbacks: ``login_logo`` and ``admin_logo`` fall back to ``logo``; ``logo`` and ``favicon`` fall back to the files the
module ships. Images are validated by content (PNG / JPEG / ICO / WebP, never SVG: it can carry script) and size.
"""
import base64
import binascii
import io

from odoo import api, fields, models, tools
from odoo.exceptions import AccessError, ValidationError

DEFAULT_NAME = 'NCollection ERP'
DEFAULT_SHORT_NAME = 'NCollection'
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_SIDE_PX = 4096
_MAGIC = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'\x00\x00\x01\x00', 'image/x-icon'),
    (b'RIFF', 'image/webp'),
)
_KINDS = ('logo', 'login', 'admin', 'favicon')
_FIELD_OF = {'logo': 'logo', 'login': 'login_logo', 'admin': 'admin_logo', 'favicon': 'favicon'}


def sniff(raw):
    """The image type of ``raw`` from its bytes (never from a name), or None when it is not an allowed image."""
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            if mime == 'image/webp' and raw[8:12] != b'WEBP':
                return None
            return mime
    return None


class ProductBranding(models.Model):
    _name = 'ncollection.product.branding'
    _description = 'Product branding'

    name = fields.Char(string='Product name', required=True, default=DEFAULT_NAME, translate=False,
                       help="What the application is called: browser title, login page, error pages, e-mails.")
    short_name = fields.Char(string='Short name', default=DEFAULT_SHORT_NAME,
                             help="The short form where space is tight (offline page, notices).")
    logo = fields.Binary(string='Product logo', attachment=True,
                         help="The application's logo. Also used for the login page and the administration screens "
                              "unless they have their own below. NOT the company logo of a tenant.")
    login_logo = fields.Binary(string='Login logo', attachment=True,
                               help="Optional. The logo on the sign-in and password pages; empty = the product logo.")
    admin_logo = fields.Binary(string='Administration logo', attachment=True,
                               help="Optional. The logo on the administration and platform screens; "
                                    "empty = the product logo.")
    favicon = fields.Binary(attachment=True,
                            help="The browser-tab icon (PNG or ICO, ideally square, 32x32 or larger). Empty = the "
                                 "icon shipped with the product.")

    @api.constrains('logo', 'login_logo', 'admin_logo', 'favicon')
    def _check_images(self):
        # only the images that changed are decoded and checked, not all four on every write of the name
        images = ('logo', 'login_logo', 'admin_logo', 'favicon')
        listed = self.env.context.get('nc_images')
        changed = images if listed is None else [f for f in images if f in listed]
        for record in self:
            for fname in changed:
                value = record.sudo().with_context(bin_size=False)[fname]
                if value:
                    self._nc_check_image(value, record._fields[fname].string, favicon=fname == 'favicon')

    @api.model
    def _nc_check_image(self, value, label, favicon=False):
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise ValidationError(self.env._("%s is not a valid image file.", label))
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValidationError(self.env._("%(label)s is too large (at most %(mb)s MB).",
                                             label=label, mb=MAX_IMAGE_BYTES // (1024 * 1024)))
        mime = sniff(raw)
        if not mime or (favicon and mime not in ('image/png', 'image/x-icon')):
            raise ValidationError(self.env._(
                "%(label)s must be a %(kinds)s image (the file's content is checked, not its name).",
                label=label, kinds='PNG or ICO' if favicon else 'PNG, JPEG or WebP'))
        if mime in ('image/png', 'image/jpeg', 'image/webp'):
            from PIL import Image, UnidentifiedImageError
            try:
                image = Image.open(io.BytesIO(raw))
                image.verify()
                width, height = Image.open(io.BytesIO(raw)).size
            except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
                raise ValidationError(self.env._("%s is not a readable image.", label))
            if max(width, height) > MAX_SIDE_PX:
                raise ValidationError(self.env._("%(label)s is too large (at most %(px)s pixels on a side).",
                                                 label=label, px=MAX_SIDE_PX))

    # ------------------------------------------------------------------ the single record
    @api.model
    def _nc_record(self):
        record = self.sudo().search([], limit=1)
        return record or self.sudo().create({})

    @api.model
    def nc_brand(self):
        """The product identity as a plain dict, for every surface. Cheap: one row, cached per version."""
        record = self.sudo().search([], limit=1)
        # milliseconds: two changes within one second still get two different image URLs
        stamp = int(record.write_date.timestamp() * 1000) if record.write_date else 0
        return self._nc_brand_values(record.id, stamp)

    @tools.ormcache('self.env.cr.dbname', 'brand_id', 'stamp')
    def _nc_brand_values(self, brand_id, stamp):
        record = self.sudo().browse(brand_id)
        full = record.with_context(bin_size=False)
        has = {kind: bool(record.exists() and full[_FIELD_OF[kind]]) for kind in _KINDS}
        version = stamp

        def url(kind):
            return '/ncollection/brand/%s?v=%d' % (kind, version)
        return {
            'name': (record.name or DEFAULT_NAME) if record.exists() else DEFAULT_NAME,
            'short_name': (record.short_name or record.name or DEFAULT_SHORT_NAME) if record.exists()
            else DEFAULT_SHORT_NAME,
            'logo_url': url('logo'),
            'login_logo_url': url('login'),
            'admin_logo_url': url('admin'),
            'favicon_url': url('favicon'),
            'has': has,
            'version': version,
        }

    @api.model
    def _nc_image(self, kind):
        """``(bytes, mime)`` for ``kind`` with its fallbacks; the module's own file when nothing is configured."""
        record = self.sudo().search([], limit=1)
        order = {'logo': ('logo',), 'login': ('login_logo', 'logo'), 'admin': ('admin_logo', 'logo'),
                 'favicon': ('favicon',)}[kind]
        for fname in order:
            value = record.with_context(bin_size=False)[fname] if record else False
            if value:
                raw = base64.b64decode(value)
                mime = sniff(raw)
                if mime:
                    return raw, mime
        default = 'favicon.ico' if kind == 'favicon' else 'logo.png'
        with tools.file_open('ncollection_branding/static/src/img/' + default, 'rb') as handle:
            return handle.read(), ('image/x-icon' if kind == 'favicon' else 'image/png')

    # ------------------------------------------------------------------ writes
    @api.model
    def _nc_check_values(self, vals):
        """Validate an image BEFORE it is stored: the attachment layer decodes the images it stores, so a file that
        claims an enormous size must be refused here, with a message, and never reach it."""
        for fname in ('logo', 'login_logo', 'admin_logo', 'favicon'):
            if vals.get(fname):
                self._nc_check_image(vals[fname], self._fields[fname].string, favicon=fname == 'favicon')

    @api.model_create_multi
    def create(self, vals_list):
        self._nc_require_administrator()
        if self.sudo().search_count([]) or len(vals_list) > 1:
            raise ValidationError(self.env._("There is only one product branding."))
        for vals in vals_list:
            self._nc_check_values(vals)
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        self._nc_require_administrator()
        self._nc_check_values(vals)
        images = [f for f in ('logo', 'login_logo', 'admin_logo', 'favicon') if f in vals]
        old_short_name = self[:1].short_name if 'short_name' in vals else None
        result = super(ProductBranding, self.with_context(nc_images=images)).write(vals)
        self.env.registry.clear_cache()
        if 'short_name' in vals:
            self.env['ncollection.vendor.content']._nc_sync_bot(old_short_name)
        return result

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        raise ValidationError(self.env._("The product branding cannot be deleted: clear its fields for the defaults."))

    @api.model
    def _nc_require_administrator(self):
        if not (self.env.su or self.env.user.has_group('base.group_system')):
            raise AccessError(self.env._("Only an administrator can change the product branding."))

    @api.model
    def action_open(self):
        self._nc_require_administrator()
        record = self._nc_record()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Product Branding'), 'res_model': self._name,
            'res_id': record.id, 'view_mode': 'form', 'target': 'current',
            'views': [(self.env.ref('ncollection_branding.view_product_branding_form').id, 'form')],
        }
