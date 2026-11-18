# -*- coding: utf-8 -*-
"""F2-T06: Native UAE FTA VAT 201 Return & FTA Audit File (FAF) Exporter.

Implements the official UAE Federal Tax Authority (FTA) VAT 201 periodic
tax return report according to Federal Decree-Law No. (8) of 2017 on Value Added Tax.

Boxes computed:
  - Box 1 (1a - 1g): Standard rated supplies by Emirate (5% VAT)
  - Box 2: Tax Refunds provided to tourists
  - Box 3: Supplies subject to the reverse charge provisions
  - Box 4: Zero rated supplies (0%)
  - Box 5: Exempt supplies
  - Box 6: Goods imported into the UAE via UAE Customs
  - Box 7: Adjustments to goods imported into the UAE
  - Box 8: Total value of supplies and output tax (Sum 1..7)
  - Box 9: Standard rated expenses (5% input tax)
  - Box 10: Supplies subject to reverse charge provisions (Input tax)
  - Box 11: Total value of expenses and input tax (Sum 9..10)
  - Box 12: Total due tax for the period (Box 8)
  - Box 13: Total recoverable tax for the period (Box 11)
  - Box 14: Net payable tax / (Net refundable tax) (Box 12 - Box 13)

Built on the F2-T01 native financial reporting engine.
"""
import base64
import csv
import io
import logging

from odoo import models

_logger = logging.getLogger(__name__)

EMIRATES = [
    ('1a', 'Abu Dhabi'),
    ('1b', 'Dubai'),
    ('1c', 'Sharjah'),
    ('1d', 'Ajman'),
    ('1e', 'Umm Al Quwain'),
    ('1f', 'Ras Al Khaimah'),
    ('1g', 'Fujairah'),
]


class NcollectionUaeVatReport(models.TransientModel):
    _name = 'ncollection.account.report.uae.vat'
    _inherit = ['ncollection.account.report']
    _description = 'UAE FTA VAT 201 Return'

    def _nc_report_title(self):
        return self.env._("UAE FTA VAT 201 Return")

    def _nc_list_view_ref(self):
        return 'ncollection_account_reports.view_report_line_uae_vat_list'

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_uae_vat'

    def _nc_columns(self):
        return [
            {'key': 'box', 'label': self.env._("Box"), 'type': 'char'},
            {'key': 'label', 'label': self.env._("Description / Supply Category"), 'type': 'char'},
            {'key': 'balance', 'label': self.env._("Amount (AED)"), 'type': 'monetary'},
            {'key': 'vat_amount', 'label': self.env._("VAT Amount (AED)"), 'type': 'monetary'},
            {'key': 'recoverable_vat', 'label': self.env._("Recoverable VAT (AED)"), 'type': 'monetary'},
        ]

    def _nc_vat_kind(self, tax, booked=False):
        """The VAT 201 box family of a tax, by the SAME rules the return has always used for the net amounts (name
        keywords, then the rate), so the net and the booked VAT of a tax always land in the same box."""
        rate = tax.amount
        tax_name = (tax.name or '').lower()
        if booked and 'tourist' in tax_name:      # the tourist refund box reads the booked tax only
            return 'tourist'
        if 'reverse' in tax_name:
            return 'reverse'
        if 'zero' in tax_name or rate == 0.0:
            return 'zero'
        if 'exempt' in tax_name:
            return 'exempt'
        if 'customs' in tax_name:
            return 'customs'
        if rate == 5.0 or '5%' in tax_name or 'standard' in tax_name:
            return 'standard'
        return False

    def _get_partner_emirate(self, partner):
        """Map partner to one of the 7 Emirates based on state name or code."""
        if not partner:
            return 'Dubai'
        state_name = (partner.state_id.name or '').strip().lower()
        for _, emirate_name in EMIRATES:
            if emirate_name.lower() in state_name:
                return emirate_name
        # Fallback to Dubai
        return 'Dubai'

    def _nc_compute_lines(self):
        """Compute the official FTA VAT 201 return rows from posted move lines."""
        self.ensure_one()
        AML = self.env['account.move.line']
        domain_base = self._nc_filter_domain() + [
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]

        # Fetch lines within the period
        lines = AML.search(domain_base)

        # Buckets for Box 1a - 1g (Standard rated 5% supplies by Emirate)
        emirate_sales = {name: {'net': 0.0, 'vat': 0.0} for _, name in EMIRATES}

        # Buckets for other output boxes
        box2_net, box2_vat = 0.0, 0.0  # Tourist refunds
        box3_net, box3_vat = 0.0, 0.0  # Reverse charge sales
        box4_net = 0.0  # Zero rated sales (0%)
        box5_net = 0.0  # Exempt sales
        box6_net, box6_vat = 0.0, 0.0  # Customs imports
        box7_net, box7_vat = 0.0, 0.0  # Customs adjustments

        # Buckets for input boxes
        box9_net, box9_vat = 0.0, 0.0   # Standard rated expenses (5%)
        box10_net, box10_vat = 0.0, 0.0  # Reverse charge expenses

        # Process sales invoice lines
        sale_lines = lines.filtered(lambda move_line: move_line.move_id.is_sale_document())
        for line in sale_lines:
            if line.display_type in ('line_section', 'line_note'):
                continue
            taxes = line.tax_ids
            # Net amount in company currency (AED)
            net_amt = line.credit - line.debit
            if not taxes:
                continue

            for tax in taxes:
                kind = self._nc_vat_kind(tax)
                if kind == 'reverse':
                    box3_net += net_amt
                elif kind == 'zero':
                    box4_net += net_amt
                elif kind == 'exempt':
                    box5_net += net_amt
                elif kind == 'customs':
                    box6_net += net_amt
                elif kind == 'standard':
                    emirate = self._get_partner_emirate(line.partner_id)
                    emirate_sales[emirate]['net'] += net_amt

        # Process purchase / bill lines
        purchase_lines = lines.filtered(lambda move_line: move_line.move_id.is_purchase_document())
        for line in purchase_lines:
            if line.display_type in ('line_section', 'line_note'):
                continue
            taxes = line.tax_ids
            net_amt = line.debit - line.credit
            if not taxes:
                continue

            for tax in taxes:
                kind = self._nc_vat_kind(tax)
                if kind == 'reverse':
                    box10_net += net_amt
                elif kind == 'standard':
                    box9_net += net_amt

        # The VAT itself is the VAT BOOKED on the posted tax lines (tax_line_id), never net x rate: an invoice
        # rounds its tax per line or per total, a tax can be edited by hand, and the return must equal the tax
        # accounts (audit 2026-11-18: net x 5% printed 242.775 where the ledger holds 242.78). Reverse charge books
        # a +100% and a -100% repartition line: the positive one is the tax of the box.
        for line in lines.filtered(lambda move_line: bool(move_line.tax_line_id)):
            tax = line.tax_line_id
            kind = self._nc_vat_kind(tax, booked=True)
            repartition = line.tax_repartition_line_id
            if repartition and repartition.factor_percent < 0:
                continue
            move = line.move_id
            if kind == 'tourist':
                box2_vat += (line.debit - line.credit)
            elif move.is_sale_document():
                vat = line.credit - line.debit
                if kind == 'reverse':
                    box3_vat += vat
                elif kind == 'customs':
                    box6_vat += vat
                elif kind == 'standard':
                    emirate_sales[self._get_partner_emirate(line.partner_id)]['vat'] += vat
            elif move.is_purchase_document():
                vat = line.debit - line.credit
                if kind == 'reverse':
                    box10_vat += vat
                elif kind == 'standard':
                    box9_vat += vat

        rows = []

        # SECTION 1: VAT on Sales and all other Outputs
        rows.append({
            'box': '',
            'label': self.env._("VAT ON SALES AND ALL OTHER OUTPUTS"),
            'balance': 0.0,
            'vat_amount': 0.0,
            'recoverable_vat': 0.0,
            'level': 0,
        })

        total_box1_net = 0.0
        total_box1_vat = 0.0
        for code, name in EMIRATES:
            net = emirate_sales[name]['net']
            vat = emirate_sales[name]['vat']
            total_box1_net += net
            total_box1_vat += vat
            rows.append({
                'box': code,
                'label': self.env._("Standard rated supplies in %s (5%%)", name),
                'balance': net,
                'vat_amount': vat,
                'recoverable_vat': 0.0,
                'level': 2,
            })

        # Box 2 to 7
        rows.append({
            'box': '2',
            'label': self.env._("Tax Refunds provided to Tourists under the Tourist Scheme"),
            'balance': box2_net,
            'vat_amount': box2_vat,
            'recoverable_vat': 0.0,
            'level': 1,
        })
        rows.append({
            'box': '3',
            'label': self.env._("Supplies subject to the reverse charge provisions"),
            'balance': box3_net,
            'vat_amount': box3_vat,
            'recoverable_vat': 0.0,
            'level': 1,
        })
        rows.append({
            'box': '4',
            'label': self.env._("Zero rated supplies (0%)"),
            'balance': box4_net,
            'vat_amount': 0.0,
            'recoverable_vat': 0.0,
            'level': 1,
        })
        rows.append({
            'box': '5',
            'label': self.env._("Exempt supplies"),
            'balance': box5_net,
            'vat_amount': 0.0,
            'recoverable_vat': 0.0,
            'level': 1,
        })
        rows.append({
            'box': '6',
            'label': self.env._("Goods imported into the UAE via UAE Customs"),
            'balance': box6_net,
            'vat_amount': box6_vat,
            'recoverable_vat': 0.0,
            'level': 1,
        })
        rows.append({
            'box': '7',
            'label': self.env._("Adjustments to goods imported into the UAE"),
            'balance': box7_net,
            'vat_amount': box7_vat,
            'recoverable_vat': 0.0,
            'level': 1,
        })

        # Box 8: Total value of supplies and output tax
        box8_net = total_box1_net + box2_net + box3_net + box4_net + box5_net + box6_net + box7_net
        box8_vat = total_box1_vat + box2_vat + box3_vat + box6_vat + box7_vat
        rows.append({
            'box': '8',
            'label': self.env._("Totals: Total value of supplies and output tax"),
            'balance': box8_net,
            'vat_amount': box8_vat,
            'recoverable_vat': 0.0,
            'level': 0,
        })

        # SECTION 2: VAT on Expenses and all other Inputs
        rows.append({
            'box': '',
            'label': self.env._("VAT ON EXPENSES AND ALL OTHER INPUTS"),
            'balance': 0.0,
            'vat_amount': 0.0,
            'recoverable_vat': 0.0,
            'level': 0,
        })

        rows.append({
            'box': '9',
            'label': self.env._("Standard rated expenses (5%)"),
            'balance': box9_net,
            'vat_amount': box9_vat,
            'recoverable_vat': box9_vat,
            'level': 1,
        })
        rows.append({
            'box': '10',
            'label': self.env._("Supplies subject to reverse charge provisions (Input Tax)"),
            'balance': box10_net,
            'vat_amount': box10_vat,
            'recoverable_vat': box10_vat,
            'level': 1,
        })

        # Box 11: Total value of expenses and input tax
        box11_net = box9_net + box10_net
        box11_vat = box9_vat + box10_vat
        rows.append({
            'box': '11',
            'label': self.env._("Totals: Total value of expenses and input tax"),
            'balance': box11_net,
            'vat_amount': box11_vat,
            'recoverable_vat': box11_vat,
            'level': 0,
        })

        # SECTION 3: Net VAT Payable or Reclaimable
        rows.append({
            'box': '',
            'label': self.env._("NET VAT DUE / (REFUNDABLE)"),
            'balance': 0.0,
            'vat_amount': 0.0,
            'recoverable_vat': 0.0,
            'level': 0,
        })

        box12_due = box8_vat
        box13_recoverable = box11_vat
        box14_net = box12_due - box13_recoverable

        rows.append({
            'box': '12',
            'label': self.env._("Total value of due tax for the period"),
            'balance': 0.0,
            'vat_amount': box12_due,
            'recoverable_vat': 0.0,
            'level': 1,
        })
        rows.append({
            'box': '13',
            'label': self.env._("Total value of recoverable tax for the period"),
            'balance': 0.0,
            'vat_amount': 0.0,
            'recoverable_vat': box13_recoverable,
            'level': 1,
        })
        rows.append({
            'box': '14',
            'label': self.env._("Payable tax for the period (or reclaimable if negative)"),
            'balance': 0.0,
            'vat_amount': box14_net,
            'recoverable_vat': 0.0,
            'level': 0,
        })

        return rows

    def action_export_faf(self):
        """Export the official UAE Federal Tax Authority Audit File (FAF)."""
        self.ensure_one()
        company = self.company_id
        AML = self.env['account.move.line']
        domain = self._nc_filter_domain() + [
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]
        lines = AML.search(domain)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=',', quoting=csv.QUOTE_MINIMAL)

        # 1. Company Information Header
        writer.writerow(['FAF_SECTION', 'COMPANY_INFO'])
        writer.writerow(['Company Name', company.name])
        writer.writerow(['TRN', company.vat or ''])
        writer.writerow(['Tax Period From', str(self.date_from)])
        writer.writerow(['Tax Period To', str(self.date_to)])
        writer.writerow(['FAF Version', 'FAF v1.0.0'])
        writer.writerow([])

        # 2. General Ledger Summary
        writer.writerow(['FAF_SECTION', 'GENERAL_LEDGER'])
        writer.writerow(['Account Code', 'Account Name', 'Debit (AED)', 'Credit (AED)', 'Balance (AED)'])
        for account, debit, credit in AML._read_group(
                domain, groupby=['account_id'], aggregates=['debit:sum', 'credit:sum']):
            if account:
                writer.writerow([
                    account.code or '',
                    account.name or '',
                    f"{debit:.2f}",
                    f"{credit:.2f}",
                    f"{(debit - credit):.2f}",
                ])
        writer.writerow([])

        # 3. Sales Invoices
        writer.writerow(['FAF_SECTION', 'SALES_TRANSACTIONS'])
        writer.writerow([
            'Invoice No', 'Invoice Date', 'Customer Name', 'Customer TRN',
            'Line Description', 'Line Net Amount (AED)', 'Tax Rate %', 'VAT Amount (AED)', 'Emirate',
        ])
        for line in lines.filtered(lambda move_line: move_line.move_id.is_sale_document()):
            if line.display_type in ('line_section', 'line_note'):
                continue
            taxes = line.tax_ids
            tax_rate = sum(taxes.mapped('amount')) if taxes else 0.0
            net = line.credit - line.debit
            vat = net * (tax_rate / 100.0) if tax_rate else 0.0
            emirate = self._get_partner_emirate(line.partner_id)
            writer.writerow([
                line.move_id.name or '',
                str(line.date or ''),
                line.partner_id.name or '',
                line.partner_id.vat or '',
                line.name or '',
                f"{net:.2f}",
                f"{tax_rate:.1f}",
                f"{vat:.2f}",
                emirate,
            ])
        writer.writerow([])

        # 4. Purchase Bills
        writer.writerow(['FAF_SECTION', 'PURCHASE_TRANSACTIONS'])
        writer.writerow([
            'Bill No', 'Bill Date', 'Supplier Name', 'Supplier TRN',
            'Line Description', 'Line Net Amount (AED)', 'Tax Rate %', 'VAT Amount (AED)',
        ])
        for line in lines.filtered(lambda move_line: move_line.move_id.is_purchase_document()):
            if line.display_type in ('line_section', 'line_note'):
                continue
            taxes = line.tax_ids
            tax_rate = sum(taxes.mapped('amount')) if taxes else 0.0
            net = line.debit - line.credit
            vat = net * (tax_rate / 100.0) if tax_rate else 0.0
            writer.writerow([
                line.move_id.name or '',
                str(line.date or ''),
                line.partner_id.name or '',
                line.partner_id.vat or '',
                line.name or '',
                f"{net:.2f}",
                f"{tax_rate:.1f}",
                f"{vat:.2f}",
            ])

        csv_data = output.getvalue().encode('utf-8')
        output.close()

        filename = f"FAF_{company.vat or 'UAE'}_{self.date_from}_{self.date_to}.csv"
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(csv_data),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/csv',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{attachment.id}?download=true",
            'target': 'self',
        }
