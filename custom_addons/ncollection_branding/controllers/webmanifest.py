# -*- coding: utf-8 -*-
"""The installable app ("Install app" in the browser) carries the SYSTEM's identity, not Odoo's.

Odoo's web manifest names the app from ``web.web_app_name`` (default "Odoo"), and draws Odoo's icons and Odoo's
purple: that is what the browser shows in its install dialog, the installed app's window and the operating system's
app list. They now come from System Branding (``nc_brand()``): name, short name, primary colour, and square PNG icons
cut from the system's favicon / logo (``/ncollection/brand/app-icon/<size>``). The offline page and the iOS
home-screen icon are branded by templates (views/webclient_templates.xml). The application path (scope, start URL)
is ncollection_core's (``app_url``), which extends the same controller.
"""
import json

from odoo import http
from odoo.http import request

from odoo.addons.web.controllers.webmanifest import WebManifest


class SystemBrandedWebManifest(WebManifest):

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        brand = request.env['ncollection.product.branding'].nc_brand()
        manifest['name'] = brand['name']
        manifest['short_name'] = brand['short_name']
        manifest['background_color'] = manifest['theme_color'] = brand['primary_color']
        manifest['icons'] = [{
            'src': '/ncollection/brand/app-icon/%d?v=%s' % (size, brand['version']),
            'sizes': '%dx%d' % (size, size),
            'type': 'image/png',
        } for size in (192, 512)]
        return manifest

    def _get_scoped_app_icons(self, app_id):
        """A single app installed on its own ("Install" on an app) falls back to Odoo's icon when the app has no SVG of
        its own: that fallback is the system's icon instead."""
        icons = super()._get_scoped_app_icons(app_id)
        if all(icon['src'] == '/' + self._icon_path() for icon in icons):
            brand = request.env['ncollection.product.branding'].nc_brand()
            return [{'src': '/ncollection/brand/app-icon/%d?v=%s' % (size, brand['version']),
                     'sizes': '%dx%d' % (size, size), 'type': 'image/png'} for size in (192, 512)]
        return icons

    @http.route()
    def scoped_app_manifest(self, app_id, path, app_name=''):
        """The scoped app keeps its own name and icon, in the system's colours rather than Odoo's purple."""
        response = super().scoped_app_manifest(app_id, path, app_name=app_name)
        manifest = json.loads(response.get_data())
        manifest['background_color'] = manifest['theme_color'] = \
            request.env['ncollection.product.branding'].nc_brand()['primary_color']
        return request.make_json_response(manifest, {'Content-Type': 'application/manifest+json'})
