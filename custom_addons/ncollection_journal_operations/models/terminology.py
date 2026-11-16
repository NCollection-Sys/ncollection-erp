"""User-facing wording on Odoo's own accounting records: "Supplier" for "Vendor", and Customers > Payments is
"Receipts". Odoo's translation importer only loads a module's catalogue for that module's own records, so the names
of ``account.*`` menus and actions are set here, in English and in every ACTIVE language we have a term for. Called
from data on every install / upgrade of this module (views/terminology.xml), so an ``account`` upgrade followed by
the usual matrix restores it. Technical names, xmlids and models are untouched."""
from odoo import api, models

TERMS = {
    'account.menu_action_account_payments_receivable': {'en_US': 'Receipts', 'ar_001': 'المقبوضات'},
    'account.menu_finance_payables': {'en_US': 'Suppliers', 'ar_001': 'الموردون'},
    'account.menu_account_supplier': {'en_US': 'Suppliers', 'ar_001': 'الموردون'},
    'account.action_account_payments': {'en_US': 'Customer Receipts', 'ar_001': 'مقبوضات العملاء'},
    'account.action_account_payments_payable': {'en_US': 'Supplier Payments', 'ar_001': 'مدفوعات الموردين'},
    'account.res_partner_action_supplier': {'en_US': 'Suppliers', 'ar_001': 'الموردون'},
    'account.res_partner_action_supplier_bills': {'en_US': 'Supplier Bills', 'ar_001': 'فواتير الموردين'},
}


class JournalOpsTerminology(models.AbstractModel):
    _name = 'ncollection.journal.ops.terminology'
    _description = 'Journal operations wording'

    @api.model
    def _apply(self):
        active = set(self.env['res.lang'].search([]).mapped('code'))
        for xmlid, names in TERMS.items():
            record = self.env.ref(xmlid, raise_if_not_found=False)
            if not record:
                continue
            record.update_field_translations(
                'name', {lang: name for lang, name in names.items() if lang in active})
