# Issue #116: [F2-T06] Native VAT & Tax Reports (UAE FTA format) — Context & Architecture Blueprint

**Task ID**: F2-T06  
**Role**: DEV-2  
**Milestone**: Financial F2: Native Reports  
**Dependencies**: F2-T01 (#111), #44 (P3-T04 UAE VAT config), F5-T01 (#126) — All CLOSED  

---

## 1. Executive Summary & Objective

Provide native UAE Federal Tax Authority (FTA) compliant tax reporting within `ncollection_account_reports`, consuming UAE tax data from `ncollection_account_localization_uae`.

The report delivers:
1. **Official VAT 201 Return Structure**:
   - **Box 1a–1g**: Standard rated supplies (5%) broken down across the 7 Emirates (Abu Dhabi, Dubai, Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah, Fujairah).
   - **Box 2**: Tax refunds provided to tourists under the Tourist Scheme.
   - **Box 3**: Supplies subject to the reverse charge provisions.
   - **Box 4**: Zero-rated supplies (0%).
   - **Box 5**: Exempt supplies.
   - **Box 6**: Goods imported into the UAE via UAE Customs declarations.
   - **Box 7**: Adjustments to goods imported into the UAE.
   - **Box 8**: Total value of supplies and output tax (Sum of Boxes 1 to 7).
   - **Box 9**: Standard rated expenses (5% Recoverable VAT).
   - **Box 10**: Supplies subject to reverse charge provisions (Input Tax).
   - **Box 11**: Total value of expenses and input tax (Sum of Boxes 9 and 10).
   - **Box 12**: Total due tax for the period (Box 8).
   - **Box 13**: Total recoverable tax for the period (Box 11).
   - **Box 14**: Net Payable Tax / (Net Refund Due) (Box 12 - Box 13).
2. **FTA Audit File (FAF) Exporter**:
   - Generates official FAF CSV/TXT format containing Company Header, General Ledger Summary, Sales Invoice details, and Purchase Bill details.
3. **Multi-Channel Render**:
   - On-screen list view with drilldown to underlying `account.move.line` items.
   - QWeb PDF official declaration layout.
   - Excel (`xlsxwriter`) structured workbook.

---

## 2. Design & Architectural Placement

```
custom_addons/
  ncollection_account_reports/
    models/
      account_report.py                   (AbstractModel base engine)
    wizard/
      account_report_line.py              (Add box, vat_amount fields)
      uae_vat_report.py                   (NEW: NcollectionUaeVatReport wizard + FAF exporter)
    report/
      report_templates.xml                (QWeb template for UAE VAT 201)
    views/
      account_report_views.xml            (Form wizard, list view, menu item under Reporting)
    tests/
      test_uae_vat_report.py              (NEW: Full unit and scenario test suite)
```

---

## 3. UAE Tax Box Calculation Engine

### Box 1 (1a - 1g): Standard Rated Supplies (5%)
- Domain: `account.move.line` where invoice type is out_invoice/out_refund, parent_state='posted', and tax is 5% Sale VAT.
- Emirates mapped by partner state/emirate:
  - Abu Dhabi, Dubai, Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah, Fujairah.
  - Fallback: Company's own state or Dubai.

### Box 4: Zero-Rated Supplies (0%)
- Domain: Sale invoices with 0% UAE export / healthcare / education VAT.

### Box 5: Exempt Supplies
- Domain: Sale invoices with Exempt VAT (e.g. residential lease, local transport).

### Box 9: Standard Rated Expenses (5% Input Tax)
- Domain: `account.move.line` on purchase bills (in_invoice/in_refund) with 5% Purchase VAT.

### Box 14: Net Tax Due / (Refund)
$$\text{Net Tax} = \text{Box 12 (Output Tax)} - \text{Box 13 (Input Tax)}$$
Positive = Payable to FTA; Negative = Refundable.
