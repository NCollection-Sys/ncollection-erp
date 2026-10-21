# -*- coding: utf-8 -*-
"""Serves the product's images: ``/ncollection/brand/<logo|login|admin|favicon>?v=<version>``.

Public on purpose (the sign-in page needs them before anyone is signed in) and narrow: it returns ONLY the four product
branding images, chosen by a fixed list, with their fallbacks; it takes no id and no model name. The image
type is sniffed from the bytes and stored files are validated on write,
so what is served is always an allowed raster image, sent with
`nosniff`. With a version in the URL the answer is cached for a year (a changed image has a new version, hence a
new URL);
without one it is revalidated each time.
"""
from odoo import http
from odoo.http import request

KINDS = ('logo', 'login', 'admin', 'favicon')


class ProductBrand(http.Controller):

    @http.route('/ncollection/brand/<string:kind>', type='http', auth='public', methods=['GET'], readonly=True,
                sitemap=False)
    def image(self, kind, v=None, **_ignored):
        if kind not in KINDS:
            return request.not_found()
        Brand = request.env['ncollection.product.branding']
        raw, mime = Brand._nc_image(kind)
        # `immutable` only for the CURRENT version: a guessed future ?v= must never pin today's image for a year
        current = str(Brand.nc_brand()['version'])
        headers = [('Content-Type', mime), ('Content-Length', str(len(raw))), ('X-Content-Type-Options', 'nosniff'),
                   ('Cache-Control', 'public, max-age=31536000, immutable' if v == current else 'no-cache')]
        return request.make_response(raw, headers=headers)
