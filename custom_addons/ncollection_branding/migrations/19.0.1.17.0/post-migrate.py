# -*- coding: utf-8 -*-
"""Printed documents stop showing identity the company never had.

Measured on the platform database: its company printed "NCollection / United States / noreply@ncollection.com" under
the system name. The three came from data, not from the templates:

* ``res.company.company_details``, a STORED snapshot of the name and country written when the company was still seeded
  as "NCollection" (Odoo prints it instead of the live address when it is not empty);
* the company contact's email, the old shipped placeholder (ncollection_core cleans it on TENANTS when the platform
  pushes their identity; the platform database is not a tenant, so it kept it);
* the country line (Odoo seeds the main company as United States): no longer printed at all (the layout's address
  lines, models/document_print_template.py), the country itself is left as configured.

Only exactly those stale values are cleared: a details text that still names the old seeded identity while the company
is called something else, and the placeholder email / website. Whatever an administrator entered is kept. Idempotent.
"""
import re

from odoo import SUPERUSER_ID, api

OLD_NAME = 'NCollection'   # the seeded company name (migration compatibility, never printed)
PLACEHOLDERS = {
    'email': ('noreply@ncollection.com',),
    'website': ('https://ncollection.com', 'http://ncollection.com'),
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env['res.company'].search([]):
        text = re.sub(r'<[^>]+>', ' ', company.company_details or '')
        if re.search(r'\b%s\b' % OLD_NAME, text) and OLD_NAME not in (company.name or ''):
            company.company_details = False
        # The report footer Odoo snapshots from the company's email + website when the company is created: on the
        # platform it read "noreply@ncollection.com https://ncollection.com". Cleared only when it holds nothing but
        # those placeholders.
        footer = re.sub(r'<[^>]+>', ' ', company.report_footer or '').split()
        if footer and all(word in PLACEHOLDERS['email'] + PLACEHOLDERS['website'] for word in footer):
            company.report_footer = False
        partner = company.partner_id
        for fname, stale in PLACEHOLDERS.items():
            if (partner[fname] or '').strip() in stale:
                partner[fname] = False
