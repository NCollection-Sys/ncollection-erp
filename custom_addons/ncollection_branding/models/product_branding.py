# -*- coding: utf-8 -*-
"""SYSTEM BRANDING: the identity of THE SYSTEM (the SaaS application itself), in ONE place.

Three identities exist and NEVER share a field:

  * SYSTEM BRANDING (this model) = what the application is called and looks like: system name, short name, logo,
    favicon, login / administration logos, browser title, copyright / ownership text and the system's primary and
    secondary colours. It is what a visitor sees before and outside any workspace: the sign-in page, error pages,
    the offline page, e-mail frames, the browser tab.
  * TENANT BRANDING = how one tenant's workspace looks inside the product: the company colours and login background
    on ``res.company`` (``nc_primary_color``, ...), applied by the platform's reseller push as well.
  * COMPANY IDENTITY = who the company legally is: legal name, registration, address, TRN, logo and stamp printed on
    its documents (``res.company``). Printed invoices carry the company's identity, never the system's.

A tenant's colours or logo cannot change what the sign-in page, the error pages or the browser tab say, and a change
here cannot overwrite a tenant's colours or a company's identity: they are different records with different writers.

TECHNICAL NAMES ARE UNCHANGED ON PURPOSE. The user-facing concept was "Product Branding" and is now "System
Branding"; the model (``ncollection.product.branding``), its table, ``nc_brand()``, the QWeb value
``nc_product_brand`` and the session key keep their names. Renaming them would need an ir.model / ir.model.data /
ACL / template / JS migration on every database for no change a person can see, and the persisted row (which holds a
workspace's configured name and images) is preserved exactly as it is.

Every user-visible place that names or draws the product reads it from here, through :meth:`nc_brand`:

  * server templates (title, favicon, login, error pages, offline page, e-mail layouts) call ``nc_product_brand()``,
    a value ``ir.qweb`` adds to every render (``models/ir_qweb.py``), so it works with or without an HTTP request;
  * the web client reads the same dict from ``session_info['nc_product_brand']`` (tab title, error dialogs);
  * the images are served by ``/ncollection/brand/<kind>`` with a version in the URL, so a changed logo or favicon is
    fetched again instead of staying in a browser or CDN cache.

Nothing outside this model hard-codes the system name or its colours. Changing it here changes every surface.

COLOURS use the ONE existing design-token namespace, ``--nc-color-*`` (never a second one): the system colours are
emitted as ``--nc-color-primary`` / ``--nc-color-secondary`` by ``views/branding_theme_templates.xml`` BEFORE the
tenant's own, so a tenant that sets none inherits the system's, and on the system surfaces (sign-in, error pages) the
system's are re-asserted so a tenant's cannot repaint them. Both are validated as strict 6-digit hex on every
write path:
the value is written into a ``<style>`` block, so nothing else may ever be stored.

Fallbacks: ``login_logo`` and ``admin_logo`` fall back to ``logo``; ``logo`` and ``favicon`` fall back to the files the
module ships; ``browser_title`` falls back to the system name; an empty ``copyright_text`` yields
"© <year> <system name>. All rights reserved.". Images are validated by content (PNG / JPEG / ICO / WebP, never SVG:
it can carry script) and size.
"""
import base64
import binascii
import hashlib
import io
import struct

import datetime

from odoo import SUPERUSER_ID, api, fields, models, tools
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import image as image_tools

from .res_company import _HEX_COLOR_RE

DEFAULT_NAME = 'NCollection ERP'
DEFAULT_SHORT_NAME = 'NCollection'
# the NCollection palette: the same values res.company's tenant colours default to, so an existing workspace looks
# exactly as it did before the system colours existed
DEFAULT_PRIMARY_COLOR = '#1F5F8F'
DEFAULT_SECONDARY_COLOR = '#2D7AB7'
COPYRIGHT_MAX = 200
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
# What the platform pushes to every tenant over config-sync (the system identity, never tenant or company branding).
PUSHED_FIELDS = ('name', 'short_name', 'browser_title', 'copyright_text', 'primary_color', 'secondary_color',
                 'logo', 'login_logo', 'admin_logo', 'favicon')


_OTHER_FORMATS = (
    (b'GIF8', 'GIF'), (b'BM', 'BMP'), (b'\x00\x00\x01\x00', 'ICO'),
    (b'II*\x00', 'TIFF'), (b'MM\x00*', 'TIFF'), (b'%PDF', 'PDF'),
    (b'<', 'SVG / XML / HTML'), (b'\xef\xbb\xbf<', 'SVG / XML / HTML'),
)


def _format_name(raw):
    """A name for a file this model refuses, for the message only (never a decision): GIF, SVG, AVIF / HEIC..."""
    for magic, name in _OTHER_FORMATS:
        if raw.startswith(magic):
            return name
    if raw[4:8] == b'ftyp':
        return 'AVIF / HEIC'
    if raw.startswith(b'RIFF'):
        return 'RIFF (%s)' % raw[8:12].decode('ascii', 'replace')
    return ''


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
    _description = 'System branding'

    name = fields.Char(string='System name', required=True, default=DEFAULT_NAME, translate=False,
                       help="What the system is called: browser title (unless one is set below), sign-in page, error "
                            "pages, e-mails. NOT the name of a tenant company.")
    short_name = fields.Char(string='Short name', default=DEFAULT_SHORT_NAME,
                             help="The short form where space is tight (offline page, notices).")
    browser_title = fields.Char(string='Browser title',
                                help="Optional. The text of the browser tab. Empty: the system name.")
    copyright_text = fields.Char(string='Copyright / ownership text',
                                 help="Optional. The line under the sign-in page, e.g. '© 2026 Acme Cloud Ltd. All "
                                      "rights reserved.' Empty: '© <year> <system name>. All rights reserved.'")
    primary_color = fields.Char(string='System primary colour', default=DEFAULT_PRIMARY_COLOR,
                                help="The system's main colour: the sign-in page, error pages, e-mail frame and the "
                                     "browser's theme colour, and the workspace colour wherever a tenant has not "
                                     "chosen its own. 6-digit hex, e.g. #1F5F8F.")
    secondary_color = fields.Char(string='System secondary colour', default=DEFAULT_SECONDARY_COLOR,
                                  help="The system's accent colour (links and highlights on the same surfaces). "
                                       "6-digit hex, e.g. #2D7AB7.")
    logo = fields.Binary(string='System logo', attachment=True,
                         help="The system's logo. Also used for the login page and the administration screens "
                              "unless they have their own below. NOT the company logo of a tenant.")
    login_logo = fields.Binary(string='Login logo', attachment=True,
                               help="Optional. The logo on the sign-in and password pages; empty = the system logo.")
    admin_logo = fields.Binary(string='Administration / system logo', attachment=True,
                               help="Optional. The logo on the administration and platform screens; "
                                    "empty = the system logo.")
    favicon = fields.Binary(attachment=True,
                            help="The browser-tab icon (PNG or ICO, ideally square, 32x32 or larger). Empty = the "
                                 "icon shipped with the product.")

    @api.constrains('primary_color', 'secondary_color')
    def _check_colors(self):
        """Only a strict 6-digit hex colour is stored, on every write path (UI, RPC, import, data file): the value is
        emitted into a <style> block, where anything else could break out of the declaration."""
        for record in self:
            for fname in ('primary_color', 'secondary_color'):
                value = record[fname]
                if value and not _HEX_COLOR_RE.fullmatch(value):
                    raise ValidationError(self.env._(
                        "%(value)r is not a valid colour for '%(field)s'. Use a 6-digit hex colour such as #1F5F8F.",
                        value=value, field=self._fields[fname].string))

    @api.constrains('copyright_text', 'browser_title')
    def _check_texts(self):
        for record in self:
            for fname in ('copyright_text', 'browser_title'):
                value = record[fname] or ''
                if len(value) > COPYRIGHT_MAX:
                    raise ValidationError(self.env._(
                        "'%(field)s' is too long (at most %(max)s characters).",
                        field=self._fields[fname].string, max=COPYRIGHT_MAX))
                if any(ord(ch) < 32 for ch in value):
                    raise ValidationError(self.env._("'%s' cannot contain control characters.",
                                                     self._fields[fname].string))

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
        ico_misplaced = mime == 'image/x-icon' and not favicon     # ICO is a favicon format, never a logo
        if not mime or ico_misplaced or (favicon and mime not in ('image/png', 'image/x-icon')):
            found = _format_name(raw)
            raise ValidationError(self.env._(
                "%(label)s must be a %(kinds)s image%(found)s (the file's content is checked, not its name).",
                label=label, kinds='PNG or ICO' if favicon else 'PNG, JPEG or WebP',
                found=self.env._("; this file is %s", found) if found else ''))
        kind = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WebP'}.get(mime, mime)
        if mime == 'image/webp':
            # Odoo never decodes WebP (odoo.tools.image loads only the BMP/GIF/ICO/JPEG/PNG/PPM decoders, and its own
            # image_process leaves WebP alone): the container is checked the way Odoo reads it - RIFF length and the
            # VP8/VP8L/VP8X header that holds the size - and the browser decodes it.
            try:
                width, height = image_tools.get_webp_size(raw) or (0, 0)   # None: a form Odoo does not read
            except (UserError, struct.error, IndexError, ValueError):
                width = height = 0
            if not (width and height) or struct.unpack('<I', raw[4:8])[0] + 8 != len(raw):
                raise ValidationError(self.env._(
                    "%(label)s could not be read as a %(kind)s image: the file is damaged or incomplete.",
                    label=label, kind=kind))
        elif mime in ('image/png', 'image/jpeg'):
            from PIL import Image, UnidentifiedImageError
            try:
                image = Image.open(io.BytesIO(raw))
                image.verify()
                width, height = Image.open(io.BytesIO(raw)).size
            except Image.DecompressionBombError:
                raise ValidationError(self.env._("%(label)s is too large (at most %(px)s pixels on a side).",
                                                 label=label, px=MAX_SIDE_PX))
            except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError, struct.error):
                raise ValidationError(self.env._(
                    "%(label)s could not be read as a %(kind)s image: the file is damaged or incomplete.",
                    label=label, kind=kind))
        if mime != 'image/x-icon':
            if max(width, height) > MAX_SIDE_PX:
                raise ValidationError(self.env._("%(label)s is too large (at most %(px)s pixels on a side).",
                                                 label=label, px=MAX_SIDE_PX))

    # ------------------------------------------------------------------ existing workspaces
    @api.model
    def _nc_adopt_system_defaults(self):
        """Give a branding row that predates the system colours the NCollection palette, and touch NOTHING else.

        A workspace that was configured under the old "Product Branding" screen keeps its name, short name and every
        image exactly as they are; only a colour that is empty or not a valid hex is filled in. Used by the
        19.0.1.11.0 migration (Odoo already fills the new columns' defaults, so this is the belt to that brace) and
        safe to run any number of times: a colour somebody chose is never changed. Returns how many rows changed."""
        changed = 0
        for record in self.sudo().search([], limit=100):   # a singleton: the bound keeps the scan finite
            vals = {}
            if not _HEX_COLOR_RE.fullmatch(record.primary_color or ''):
                vals['primary_color'] = DEFAULT_PRIMARY_COLOR
            if not _HEX_COLOR_RE.fullmatch(record.secondary_color or ''):
                vals['secondary_color'] = DEFAULT_SECONDARY_COLOR
            if vals:
                record.write(vals)
                changed += 1
        return changed

    # ------------------------------------------------------------------ the single record
    @api.model
    def _nc_record(self):
        record = self.sudo().search([], limit=1)
        return record or self.sudo().create({})

    @api.model
    def nc_brand(self):
        """The product identity as a plain dict, for every surface: one search and one indexed attachment query
        (the image version), the dict itself cached per (write stamp, image version)."""
        record = self.sudo().search([], limit=1)
        stamp = int(record.write_date.timestamp() * 1000) if record.write_date else 0
        return self._nc_brand_values(record.id, stamp, self._nc_image_version(record), datetime.date.today().year)

    @api.model
    def _nc_image_version(self, record):
        """The version every image URL carries: the stored images' own checksums (Odoo's ``ir.attachment``
        sha1), so a replaced logo always gets a new URL - also two changes inside one transaction, where
        ``write_date`` (the transaction's start time) would not move - and an unchanged one keeps its URL."""
        if not record:
            return 0
        self.env['ir.attachment'].flush_model(['checksum', 'res_field', 'res_id', 'res_model'])
        self.env.cr.execute("""
            SELECT res_field, checksum FROM ir_attachment
             WHERE res_model = %s AND res_id = %s AND res_field IN %s ORDER BY res_field
        """, [self._name, record.id, tuple(_FIELD_OF.values())])
        digest = hashlib.sha1(repr(self.env.cr.fetchall()).encode()).hexdigest()
        return int(digest[:13], 16)

    @tools.ormcache('self.env.cr.dbname', 'brand_id', 'stamp', 'version', 'year')
    def _nc_brand_values(self, brand_id, stamp, version, year):
        record = self.sudo().browse(brand_id)
        full = record.with_context(bin_size=False)
        has = {kind: bool(record.exists() and full[_FIELD_OF[kind]]) for kind in _KINDS}

        def url(kind):
            return '/ncollection/brand/%s?v=%d' % (kind, version)
        exists = bool(record.exists())
        name = (record.name or DEFAULT_NAME) if exists else DEFAULT_NAME
        primary = record.primary_color if exists and _HEX_COLOR_RE.fullmatch(record.primary_color or '') \
            else DEFAULT_PRIMARY_COLOR
        secondary = record.secondary_color if exists and _HEX_COLOR_RE.fullmatch(record.secondary_color or '') \
            else DEFAULT_SECONDARY_COLOR
        copyright_text = (record.copyright_text or '').strip() if exists else ''
        return {
            'name': name,
            'title': ((record.browser_title or '').strip() if exists else '') or name,
            # the line shown on the sign-in page: configured, else built from the name (and the current year)
            'copyright': copyright_text or '© %d %s. All rights reserved.' % (year, name),
            'primary_color': primary,
            'secondary_color': secondary,
            'short_name': (record.short_name or record.name or DEFAULT_SHORT_NAME) if exists
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

    @api.model
    def _nc_app_icon(self, size):
        """A square PNG of ``size`` px for the installable app (web manifest, iOS home screen, offline page): the
        system's favicon, else its logo, centred on a transparent square. Browsers require square PNG icons of
        fixed sizes, which a configured logo usually is not."""
        record = self.sudo().search([], limit=1)
        kinds = ('favicon', 'logo') if record and record.favicon else ('logo',)
        for kind in kinds:
            raw, _mime = self._nc_image(kind)
            try:
                return image_tools.image_process(raw, size=(size, size), expand=True, output_format='PNG')
            except (ValueError, UserError, OSError):
                continue                            # a favicon PIL cannot open: the logo is the next candidate
        with tools.file_open('ncollection_branding/static/src/img/logo.png', 'rb') as handle:
            return image_tools.image_process(handle.read(), size=(size, size), expand=True, output_format='PNG')

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
        self._nc_require_manager()
        if self.sudo().search_count([]) or len(vals_list) > 1:
            raise ValidationError(self.env._("There is only one system branding."))
        for vals in vals_list:
            self._nc_check_values(vals)
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        self._nc_require_manager()
        self._nc_check_values(vals)
        images = [f for f in ('logo', 'login_logo', 'admin_logo', 'favicon') if f in vals]
        old_short_name = self[:1].short_name if 'short_name' in vals else None
        old_name = self[:1].name if 'name' in vals else None
        Content = self.env['ncollection.vendor.content']
        old_bot_name = Content._nc_bot_name(old_short_name) if old_short_name else None
        result = super(ProductBranding, self.with_context(nc_images=images)).write(vals)
        self.env.registry.clear_cache()
        if 'short_name' in vals:
            Content._nc_sync_bot(old_short_name)
        elif images:
            Content._nc_sync_bot_avatar()
        if old_name or old_bot_name:
            # what the system user already wrote in Discuss now says the new name
            self.env['mail.message']._nc_reword_system_messages(old_name, old_bot_name)
        return result

    # ------------------------------------------------------------------ the platform's identity, pushed to tenants
    @api.model
    def _nc_platform_identity(self):
        """This database's System Branding as the config-sync payload the platform pushes to every tenant: the
        whitelisted fields only, images as base64 text. Read on the PLATFORM (its own model, Rule 3)."""
        record = self.sudo().search([], limit=1).with_context(bin_size=False)
        if not record:
            return {}
        identity = {}
        for fname in PUSHED_FIELDS:
            value = record[fname]
            identity[fname] = (value.decode() if isinstance(value, bytes) else value) or False
        return identity

    @api.model
    def _nc_apply_platform_identity(self, identity):
        """Apply the platform's System Branding in a TENANT (called by ``sync_from_platform``, over the config-sync
        channel the platform alone holds a bearer for). The system identity is the platform's to set, so this is the
        tenant-side write that path is for: only the whitelisted fields, through the same validation as the form
        (image type and size, colours), written as the real superuser because the channel itself is the platform.
        Writes only what differs; returns the changed field names."""
        if not isinstance(identity, dict):
            raise UserError(self.env._("The system branding pushed by the platform must be a dict."))
        rejected = set(identity) - set(PUSHED_FIELDS)
        if rejected:
            raise UserError(self.env._("Refusing to sync system branding field(s): %s", ', '.join(sorted(rejected))))
        record = self.with_user(SUPERUSER_ID)._nc_record().with_context(bin_size=False)
        changes = {}
        for fname, value in identity.items():
            value = value or False
            if fname == 'name' and not value:
                continue                            # required: an empty push never blanks the system's name
            current = record[fname]
            current = (current.decode() if isinstance(current, bytes) else current) or False
            if current != value:
                changes[fname] = value
        if changes:
            record.write(changes)
        return sorted(changes)

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        raise ValidationError(self.env._("The system branding cannot be deleted: clear its fields for the defaults."))

    @api.model
    def _nc_require_manager(self):
        # The REAL superuser only (install, migrations), never ``env.su``: a tenant Owner holds base.group_system and
        # can run ``.sudo()`` from a server action, which keeps the acting user (system_branding_grant.py, same rule).
        if not (self.env.uid == SUPERUSER_ID
                or self.env.user.has_group('ncollection_branding.group_system_branding_manager')):
            raise AccessError(self.env._("Only platform staff can change the system branding."))

    @api.model
    def action_open(self):
        self._nc_require_manager()
        record = self._nc_record()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('System Branding'), 'res_model': self._name,
            'res_id': record.id, 'view_mode': 'form', 'target': 'current',
            'views': [(self.env.ref('ncollection_branding.view_product_branding_form').id, 'form')],
        }
