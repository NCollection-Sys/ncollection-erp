# -*- coding: utf-8 -*-
"""Posted ``account.move`` → PINT-AE payload → readiness → reconciliation → XML → official validation.

The accounting document is READ ONLY here: nothing in this file writes to ``account.move`` or its lines, recomputes a
tax, re-rounds a total or edits a value to make a document pass. The XML carries what Odoo booked (see
lib/reconcile.py); when that cannot satisfy the official rules the document stays ``draft`` with the reasons recorded.

Steps (each its own action, each appended to the history):
  ``action_prepare``       snapshot identity + build payload + readiness + reconciliation        → ``ready`` | stays
  ``draft`` ``action_generate_xml``  serialise the SAME payload (refused if the source changed) + archive    →
  ``locally_checked`` ``action_validate``      official Schematron through the isolated worker (fails closed)
  → ``validated`` | ``validation_failed``
"""

import hashlib
import json
import os
from decimal import Decimal

from odoo import fields, models
from odoo.exceptions import UserError

from ..lib import adjustments, emirates, identity, payment_means, readiness, reconcile, scope, spec, units, validator
from ..lib import issues as _issues
from ..lib.money import ZERO, dec, r2
from ..lib.xml_builder import build

VALIDATOR_URL_ENV = 'NC_EINVOICE_VALIDATOR_URL'
VALIDATOR_TOKEN_ENV = 'NC_EINVOICE_VALIDATOR_TOKEN'
# the payload text is built in ONE language, so the same document produces the same payload and XML whoever (in
# whichever UI language) prepares, generates or validates it
PAYLOAD_LANG = 'en_US'


def _d(value):
    return dec(repr(value) if isinstance(value, float) else value)


class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def dumps(obj):
    """Exact, ordered text of any JSON-able value (Decimals as exact text)."""
    return json.dumps(obj, cls=_Encoder, sort_keys=True, ensure_ascii=False)


def canonical(payload):
    """The frozen, comparable text of a payload: Decimals as exact text, keys sorted. The tuple-keyed booked-tax map is
    replaced by its list form and the private readiness views of the parties are left out."""
    clean = {key: value for key, value in payload.items() if key != 'booked_tax_by_group'}
    clean['booked_tax_by_group_list'] = [
        {'category': c, 'rate': r, 'tax': t}
        for (c, r), t in sorted(
            payload.get('booked_tax_by_group', {}).items(), key=lambda kv: (kv[0][0], str(kv[0][1]))
        )
    ]
    for party in ('seller', 'buyer'):
        clean[party] = {key: value for key, value in payload[party].items() if not key.startswith('_')}
    return dumps(clean)


class EInvoicePrepare(models.Model):
    _inherit = 'ncollection.einvoice'

    # ------------------------------------------------------------------ entry points
    def _nc_require_usable(self):
        """What every action passes first: the licence, the right to write, and the company's own switch."""
        self._nc_require_licence()
        self.check_access('write')
        for company in self.company_id:
            config = self.env['ncollection.einvoice.config'].sudo().search([('company_id', '=', company.id)], limit=1)
            if not config.enabled:
                raise UserError(self.env._("UAE e-invoicing is not enabled for %s.", company.name))

    def action_prepare(self):
        self._nc_require_usable()
        for record in self:
            record._nc_ensure_editable()
            record._nc_prepare()
        return True

    def action_generate_xml(self):
        self._nc_require_usable()
        for record in self:
            if record.locked:
                raise UserError(self.env._("A submitted e-invoice is locked."))
            if record.local_state == 'not_required' or record.scope_state == 'not_required':
                raise UserError(
                    self.env._(
                        "No e-invoice is required for this document (%(reason)s). No XML is generated.",
                        reason=record.scope_reason or record.scope_reason_code or '',
                    )
                )
            if record.local_state != 'ready':
                raise UserError(self.env._("Only a ready e-invoice can be turned into XML; prepare it first."))
            # the scope is decided again: a party's type or Tax Group may have changed since preparation
            decided, scope_found = record._nc_decide_scope()
            if decided.state == scope.NOT_REQUIRED or _issues.errors(scope_found):
                raise UserError(
                    self.env._(
                        "Whether this document needs an e-invoice changed since it was prepared. Prepare it again."
                    )
                )
            payload, found = record._nc_build_payload()
            if _issues.errors(found) or canonical(payload) != record.payload_json:
                raise UserError(
                    self.env._(
                        "The accounting document changed since this e-invoice was prepared, or it is not ready. "
                        "Prepare it again."
                    )
                )
            try:
                xml = build(payload)
            except ValueError:
                # lxml refuses control characters in text: say which problem it is rather than showing a traceback
                raise UserError(
                    self.env._("The document contains a character that XML cannot carry (a control character).")
                ) from None
            digest = hashlib.sha256(xml).hexdigest()
            attachment = (
                self.env['ir.attachment']
                .sudo()
                .create(
                    {
                        'name': '%s.xml' % record.name.replace('/', '-'),
                        'raw': xml,
                        'mimetype': 'application/xml',
                        'res_model': record._name,
                        'res_id': record.id,
                    }
                )
            )
            record.sudo().write({'xml_attachment_id': attachment.id, 'xml_sha256': digest})
            record._transition(
                'local_state', 'locally_checked', 'xml_generated', 'sha256 %s' % digest, attachment=attachment
            )
        return True

    def action_validate(self):
        self._nc_require_usable()
        unavailable = None
        for record in self:
            if record.local_state not in ('locally_checked', 'validation_failed'):
                raise UserError(self.env._("Generate the XML before validating it."))
            xml = record.xml_attachment_id.raw
            if hashlib.sha256(xml).hexdigest() != record.xml_sha256:
                raise UserError(self.env._("The archived XML no longer matches its checksum."))
            try:
                client = validator.HttpValidatorClient(
                    os.environ.get(VALIDATOR_URL_ENV), token=os.environ.get(VALIDATOR_TOKEN_ENV)
                )
                outcome = client.validate(xml, record.document_kind)
            except validator.ValidatorUnavailable as exc:
                # RECORD AND RETURN, never raise: an exception rolls the transaction back and would erase this history
                # row.
                record._log('validator_unavailable', str(exc))
                unavailable = exc
                continue
            record._nc_store_findings('official', validator.to_issues(outcome))
            # the engine, the release AND the fingerprint of the rule set that produced the answer travel with it
            ran = '%s | release %s | rules %s' % (outcome.engine, outcome.spec_version, outcome.manifest_sha256[:16])
            if outcome.ok:
                if record.local_state == 'validation_failed':
                    record._transition('local_state', 'locally_checked', 'revalidating')
                record._transition('local_state', 'validated', 'official_validation_passed', ran)
            else:
                if record.local_state == 'locally_checked':
                    failed = ', '.join(sorted({f.rule for f in outcome.failures if f.flag != 'warning'}))
                    record._transition(
                        'local_state',
                        'validation_failed',
                        'official_validation_failed',
                        '%s | failed: %s' % (ran, failed),
                    )
        if unavailable is not None:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'warning',
                    'sticky': True,
                    'title': self.env._('Official validation unavailable'),
                    'message': self.env._(
                        "The official validation could not be performed (%s). The e-invoice is NOT validated.",
                        unavailable,
                    ),
                },
            }
        return True

    # ------------------------------------------------------------------ preparation
    def _nc_ensure_editable(self):
        self.ensure_one()
        if self.locked:
            raise UserError(self.env._("A submitted e-invoice is locked; issue a credit note or a new invoice."))

    def _nc_prepare(self):
        self.ensure_one()
        profile = spec.get_profile()
        if self.xml_attachment_id:
            # Not yet submitted: the document may still be corrected, but a generated XML is NEVER destroyed. The file
            # stays in the archive, immutable, and the history keeps the link to it; the record points at the next one.
            superseded = self.xml_attachment_id
            self.sudo().write({'xml_attachment_id': False, 'xml_sha256': False})
            self._log('xml_superseded', 'sha256 %s' % superseded.checksum, attachment=superseded)
        if self.local_state in ('ready', 'locally_checked', 'validated', 'validation_failed', 'not_required'):
            self._transition('local_state', 'draft', 'prepare_again')
        self._nc_sync_lines()
        decided, scope_found = self._nc_decide_scope()
        self._nc_store_findings('scope', scope_found)
        if decided.state == scope.NOT_REQUIRED:
            self.sudo().write({'payload_json': False})
            self._transition(
                'local_state',
                'not_required',
                'not_required',
                '%s | %s%s'
                % (decided.reason_code, decided.source, ' | window %s' % decided.window if decided.window else ''),
            )
            return
        payload, found = self._nc_build_payload()
        reconciliation = reconcile.reconcile(payload, profile)
        readiness_found = list(found)
        # a refused exclusion declaration (scope error) blocks the document like any readiness error
        found = scope_found + found + reconciliation.issues
        values = {
            'spec_version': profile.key,
            'customization_id': profile.customization_id,
            'profile_id': profile.profile_id,
            'invoice_number': self.move_id.name,
            'uuid': self.uuid or self._new_uuid(),
            'type_code': payload['type_code'],
            'payment_means_code': (payload.get('payment') or {}).get('means_code') or False,
            'vat_point_date': payload.get('vat_point_date') or False,
            'seller_tin': payload['seller'].get('tin') or False,
            'seller_trn': payload['seller'].get('trn') or False,
            'seller_endpoint_scheme': payload['seller'].get('endpoint_scheme') or False,
            'seller_endpoint_id': payload['seller'].get('endpoint_id') or False,
            'buyer_tin': payload['buyer'].get('tin') or False,
            'buyer_trn': payload['buyer'].get('trn') or False,
            'buyer_endpoint_scheme': payload['buyer'].get('endpoint_scheme') or False,
            'buyer_endpoint_id': payload['buyer'].get('endpoint_id') or False,
            'buyer_endpoint_source': payload.get('buyer_endpoint_source') or False,
            'reconciliation_state': reconciliation.state,
            'reconciliation_json': dumps(
                {
                    'state': reconciliation.state,
                    'checks': [
                        {
                            'rule': c.rule,
                            'kind': c.kind,
                            'subject': c.subject,
                            'odoo': c.odoo,
                            'pint': c.pint,
                            'delta': c.delta,
                            'ok': c.ok,
                        }
                        for c in reconciliation.checks
                    ],
                }
            ),
        }
        self.sudo().write(values)
        # the payload is rebuilt AFTER the identity snapshot above so the stored copy carries the final uuid
        payload, found_again = self._nc_build_payload()
        self.sudo().write({'payload_json': canonical(payload)})
        self._nc_store_findings('readiness', readiness_found)
        self._nc_store_findings('reconciliation', reconciliation.issues)
        if _issues.errors(found):
            self._log('prepare_blocked', '%d error(s)' % len(_issues.errors(found)))
        else:
            self._transition('local_state', 'ready', 'prepared', 'reconciliation: %s' % reconciliation.state)

    def _nc_transaction_date(self):
        """The Date of Business Transaction: the VAT point (taxable supply) date when the document carries one, else
        the invoice date. Credit notes use their own invoice date."""
        move = self.move_id
        supply = move.taxable_supply_date if 'taxable_supply_date' in move._fields else False
        if supply and move.move_type != 'out_refund':
            return supply
        return move.invoice_date or False

    def _nc_decide_scope(self):
        """Run lib/scope.py on this document and record the decision (engine fields). ``(Scope, issues)``."""
        self.ensure_one()
        move, company = self.move_id, self.company_id
        seller, buyer = company.partner_id, move.commercial_partner_id
        tax_map = {
            m.tax_id.id: m.pint_category
            for m in self.env['ncollection.einvoice.tax.map'].search([('company_id', '=', company.id)])
        }
        categories = {
            tax_map.get(line.tax_ids.id) if len(line.tax_ids) == 1 else None
            for line in move.invoice_line_ids.filtered(lambda ml: ml.display_type == 'product')
        }
        config = self.env['ncollection.einvoice.config'].sudo().search([('company_id', '=', company.id)], limit=1)
        transaction_date = self._nc_transaction_date()
        decided, found = scope.classify(
            transaction_date,
            buyer_party_type=buyer.nc_einvoice_party_type,
            seller_party_type=seller.nc_einvoice_party_type,
            seller_vat_group=seller.nc_vat_group_trn or None,
            buyer_vat_group=buyer.nc_vat_group_trn or None,
            declared_exclusion=self.excluded_transaction or None,
            categories=categories,
            effective_date=config.effective_date or None,
        )
        self.sudo().write(
            {
                'transaction_date': transaction_date,
                'scope_state': decided.state,
                'scope_reason_code': decided.reason_code or False,
                'scope_reason': decided.reason or False,
                'scope_source': decided.source or False,
                'scope_window': decided.window or False,
            }
        )
        return decided, found

    def _nc_sync_lines(self):
        """One toggle line per product line of the document; existing toggles are kept."""
        self.ensure_one()
        existing = {line.move_line_id.id: line for line in self.line_ids}
        sequence = 0
        for move_line in self.move_id.invoice_line_ids.filtered(lambda ml: ml.display_type == 'product'):
            sequence += 1
            if move_line.id not in existing:
                self.env['ncollection.einvoice.line'].sudo().create(
                    {'einvoice_id': self.id, 'move_line_id': move_line.id, 'sequence': sequence}
                )

    def _nc_store_findings(self, layer, found):
        self.ensure_one()
        self.sudo().validation_ids.filtered(lambda v: v.layer == layer).unlink()
        profile = spec.get_profile()
        for issue in found:
            self.env['ncollection.einvoice.validation'].sudo().create(
                {
                    'einvoice_id': self.id,
                    'layer': layer,
                    'severity': issue.severity,
                    'rule': issue.rule,
                    'subject': issue.subject,
                    'message': self.env._(issue.message, **issue.params),
                    'spec_version': profile.key,
                }
            )

    # ------------------------------------------------------------------ the mapper
    def _nc_build_payload(self):
        """``(payload, issues)`` read from the posted document, always in the payload language."""
        return self.with_context(lang=PAYLOAD_LANG)._nc_map_document()

    def _nc_map_document(self):
        """The mapper proper. Pure with respect to the database: nothing is written."""
        self.ensure_one()
        move, company = self.move_id, self.company_id
        found = []
        if company.currency_id.name != 'AED':
            found.append(
                _issues.make(
                    'NCOLLECTION:company-currency-not-aed',
                    'company',
                    "The company currency is %(currency)s. UAE e-invoices are reported in AED, so a company whose "
                    "books are not kept in AED is not supported.",
                    {'currency': company.currency_id.name},
                )
            )
        if move.invoice_cash_rounding_id:
            found.append(
                _issues.make(
                    'NCOLLECTION:cash-rounding-unsupported',
                    'document',
                    "A document with cash rounding cannot be mapped yet: the rounding amount is not carried into the "
                    "e-invoice, so the amount due would not match the official rule.",
                )
            )
        profile = spec.get_profile()
        is_credit = move.move_type == 'out_refund'
        flags = {name: bool(self['flag_' + name]) for name in identity.FLAG_ORDER}
        txn_code = identity.transaction_type_code(flags)
        tax_map = {
            m.tax_id.id: m for m in self.env['ncollection.einvoice.tax.map'].search([('company_id', '=', company.id)])
        }

        # --- lines and per-line toggles
        toggles = {line.move_line_id.id: line for line in self.line_ids}
        raw_lines = []
        counter = 0
        for move_line in move.invoice_line_ids.filtered(lambda ml: ml.display_type == 'product'):
            counter += 1
            toggle = toggles.get(move_line.id)
            tax = move_line.tax_ids
            mapped = tax_map.get(tax.id) if len(tax) == 1 else None
            if len(tax) != 1:
                found.append(
                    _issues.make(
                        'NCOLLECTION:line-tax-count',
                        'line %d' % counter,
                        "Line %(line)s must carry exactly one VAT tax (it has %(n)s).",
                        {'line': counter, 'n': len(tax)},
                    )
                )
            elif mapped is None:
                found.append(
                    _issues.make(
                        'NCOLLECTION:tax-unmapped',
                        'line %d' % counter,
                        "The tax %(tax)s on line %(line)s is not mapped to a PINT-AE VAT category.",
                        {'tax': tax.display_name, 'line': counter},
                    )
                )
            category = mapped.pint_category if mapped else None
            if category == 'AE':
                rate = _d(mapped.pint_rate)
            elif category in ('E', 'O') or mapped is None:
                rate = None
            else:
                rate = _d(tax.amount)
            quantity, gross = _d(move_line.quantity), _d(move_line.price_unit)
            discount = _d(move_line.discount)
            net_price = gross * (Decimal(1) - discount / Decimal(100))
            xmlid = (
                move_line.product_uom_id.get_external_id().get(move_line.product_uom_id.id)
                if move_line.product_uom_id
                else None
            )
            product = move_line.product_id.product_tmpl_id
            net = r2(_d(move_line.price_subtotal))
            line_tax = self._nc_line_tax(move_line)
            company_amount = abs(_d(move_line.balance))
            raw_lines.append(
                {
                    'id': str(counter),
                    'name': (product.name or move_line.name or '').strip() or move_line.name,
                    'description': (move_line.name or '').strip(),
                    'qty': quantity,
                    'unit_code': units.unit_code(xmlid),
                    'price_unit': gross,
                    'price_gross': gross,
                    'price_net': net_price,
                    'price_discount': gross - net_price,
                    'base_qty': Decimal(1),
                    'net': net,
                    'category': category,
                    'rate': rate,
                    'exemption_code': mapped.exemption_code if mapped else None,
                    'goods_type': mapped.rcm_goods_type if mapped and category == 'AE' else None,
                    'item_type': product.nc_item_type or None,
                    'hs': product.nc_hs_code or None,
                    'sac': product.nc_sac_code or None,
                    'gtin': (
                        move_line.product_id.barcode if identity.valid_gtin(move_line.product_id.barcode) else None
                    ),
                    'seller_item_id': move_line.product_id.default_code or None,
                    'odoo_tax': line_tax,
                    'aed_amount': r2(company_amount),
                    'aed_vat': r2(self._nc_to_company(line_tax)),
                    'adjustment': toggle.adjustment if toggle else None,
                    'reason_code': toggle.reason_code if toggle else None,
                    'reason': toggle.reason if toggle else None,
                }
            )
        items, adjs, split_issues = adjustments.split_lines(raw_lines, profile)
        found.extend(split_issues)

        # --- booked tax per (category, rate): the accounting truth, from the tax lines Odoo posted
        booked = {}
        for tax_line in move.line_ids.filtered(lambda ml: ml.tax_line_id):
            mapped = tax_map.get(tax_line.tax_line_id.id)
            if mapped is None:
                continue
            category = mapped.pint_category
            rate = (
                _d(mapped.pint_rate)
                if category == 'AE'
                else None if category in ('E', 'O') else _d(tax_line.tax_line_id.amount)
            )
            booked[(category, rate)] = booked.get((category, rate), ZERO) + abs(_d(tax_line.amount_currency))
        categories = {item['category'] for item in items} | {a['category'] for a in adjs}
        only_exempt = bool(categories) and categories <= {'E', 'O'}
        if is_credit:
            type_code = profile.credit_type_out_of_scope if only_exempt else profile.credit_type_tax
        else:
            type_code = profile.invoice_type_out_of_scope if only_exempt else profile.invoice_type_tax

        # --- parties
        seller = self._nc_party(company.partner_id, seller=True, flags=flags, found=found)
        buyer = self._nc_party(move.commercial_partner_id, seller=False, flags=flags, found=found)

        # --- payment means
        config = self.env['ncollection.einvoice.config'].search([('company_id', '=', company.id)], limit=1)
        method_line = (
            move.preferred_payment_method_line_id if 'preferred_payment_method_line_id' in move._fields else False
        )
        pay_map = {
            m.journal_id.id: m.pint_code
            for m in self.env['ncollection.einvoice.payment.map'].search([('company_id', '=', company.id)])
        }
        mapped_codes = (
            [pay_map[method_line.journal_id.id]] if method_line and method_line.journal_id.id in pay_map else []
        )
        bank = move.partner_bank_id or config.default_bank_account_id
        resolution = payment_means.resolve(
            type_code=type_code,
            transaction_type_code=txn_code,
            document_override=self.payment_means_override or None,
            mapped_codes=mapped_codes,
            company_default=config.default_payment_means_code or None,
            has_payee_account=bool(bank),
            profile=profile,
        )
        found.extend(resolution.issues)

        is_foreign = move.currency_id != company.currency_id
        exchange_rate = None
        aed = None
        if is_foreign:
            rate = move.currency_id._get_conversion_rate(
                move.currency_id, company.currency_id, company, move.invoice_date or fields.Date.context_today(self)
            )
            exchange_rate = Decimal(str(rate)).quantize(Decimal('0.000001'))
            aed = {
                'tax_total': abs(_d(sum(move.line_ids.filtered(lambda ml: ml.tax_line_id).mapped('balance')))),
                'total_incl': abs(_d(move.amount_total_signed)),
            }
        vat_point = move.taxable_supply_date if 'taxable_supply_date' in move._fields else False
        vat_point_date = (
            vat_point.isoformat()
            if vat_point and move.invoice_date and vat_point < move.invoice_date and not is_credit
            else None
        )
        preceding = []
        if is_credit and move.reversed_entry_id:
            preceding = [
                {
                    'number': move.reversed_entry_id.name,
                    'date': (
                        move.reversed_entry_id.invoice_date.isoformat()
                        if move.reversed_entry_id.invoice_date
                        else None
                    ),
                }
            ]
        totals = {
            'untaxed': r2(_d(move.amount_untaxed)),
            'tax': r2(_d(move.amount_tax)),
            'total': r2(_d(move.amount_total)),
        }
        payload = {
            'spec': profile.key,
            'kind': 'credit_note' if is_credit else 'invoice',
            'type_code': type_code,
            'number': move.name,
            'uuid': self.uuid or 'pending',
            'issue_date': move.invoice_date.isoformat() if move.invoice_date else None,
            'due_date': move.invoice_date_due.isoformat() if move.invoice_date_due and not is_credit else None,
            'vat_point_date': vat_point_date,
            'currency': move.currency_id.name,
            'exchange_rate': exchange_rate,
            'flags': flags,
            'transaction_type_code': txn_code,
            'credit_reason': self.credit_reason or None,
            'preceding': preceding,
            'billing_frequency': self.billing_frequency or None,
            'period_start': self.period_start.isoformat() if self.period_start else None,
            'period_end': self.period_end.isoformat() if self.period_end else None,
            'beneficiary_id': self.beneficiary_id or None,
            'principal_id': self.principal_id or None,
            'seller': seller,
            'buyer': buyer,
            'buyer_endpoint_source': buyer.get('endpoint_source'),
            'payment': (
                None
                if resolution.omitted
                else {
                    'means_code': resolution.code,
                    'account_id': bank.acc_number if bank else None,
                    'terms_note': move.invoice_payment_term_id.name or None,
                }
            ),
            'lines': items,
            'adjustments': adjs,
            'odoo_totals': totals,
            'booked_tax_by_group': booked,
            'aed': aed,
        }
        payload['figures'] = reconcile.reconcile(payload, profile).figures
        snapshot = {
            'doc': {
                'kind': payload['kind'],
                'type_code': type_code,
                'flags': flags,
                'transaction_type_code': txn_code,
                'currency': move.currency_id.name,
                'exchange_rate': exchange_rate,
                'issue_date': payload['issue_date'],
                'due_date': payload['due_date'],
                'vat_point_date': vat_point_date,
                'payable': totals['total'],
                'credit_reason': payload['credit_reason'],
                'preceding': preceding,
                'billing_frequency': payload['billing_frequency'],
                'beneficiary_id': payload['beneficiary_id'],
                'principal_id': payload['principal_id'],
                'period_start': payload['period_start'],
                'period_end': payload['period_end'],
            },
            'seller': seller['_ready'],
            'buyer': buyer['_ready'],
            'lines': items,
            'adjustments': adjs,
        }
        found.extend(readiness.check(snapshot, profile))
        return payload, found

    def _nc_line_tax(self, move_line):
        """Tax of ONE line as the company's tax engine computes it for that line (display / AED split only: the tax
        that
        reconciles is the booked tax per group)."""
        taxes = move_line.tax_ids
        if not taxes:
            return ZERO
        result = taxes.compute_all(
            move_line.price_unit * (1 - (move_line.discount or 0.0) / 100.0),
            currency=move_line.currency_id,
            quantity=move_line.quantity,
            product=move_line.product_id,
            partner=move_line.partner_id,
        )
        return r2(sum((_d(t['amount']) for t in result['taxes']), ZERO))

    def _nc_to_company(self, amount):
        move = self.move_id
        if move.currency_id == move.company_id.currency_id:
            return amount
        return _d(
            move.currency_id._convert(
                float(amount),
                move.company_id.currency_id,
                move.company_id,
                move.invoice_date or fields.Date.context_today(self),
            )
        )

    def _nc_party(self, partner, seller, flags, found):
        states = partner._nc_uae_identity_states()
        tin_state, trn_state = states['tin'], states['trn']
        country = partner.country_id.code or ''
        state_code = partner.state_id.code if partner.state_id else None
        party = {
            'name': partner.name,
            'tin': partner.nc_tin or None,
            'trn': partner.nc_trn if trn_state.state == 'valid' else None,
            'legal_id': partner.company_registry or None,
            'legal_type': partner.nc_legal_reg_type or None,
            'legal_authority': partner.nc_legal_reg_authority or None,
            'legal_country': partner.nc_legal_reg_country_id.code or None,
            'street': partner.street,
            'street2': partner.street2 or None,
            'city': partner.city,
            'postal': partner.zip or None,
            'country': country,
            'subdivision': (
                emirates.pint_subdivision(state_code)
                if country == 'AE'
                else (partner.state_id.name if partner.state_id else None)
            ),
        }
        ready = {
            'name': partner.name,
            'tin_state': tin_state.state,
            'tin': partner.nc_tin,
            'trn_state': trn_state.state,
            'trn': partner.nc_trn,
            'trn_advisory': 'OFFICIAL:ibr-132-ae' in trn_state.notes,
            'legal_id': partner.company_registry,
            'legal_type': partner.nc_legal_reg_type,
            'legal_authority': partner.nc_legal_reg_authority,
            'legal_country': partner.nc_legal_reg_country_id.code,
            'street': partner.street,
            'city': partner.city,
            'state_code': state_code,
            'country': country,
            'subdivision_text': partner.state_id.name if partner.state_id else None,
        }
        if seller:
            endpoint, issues_ = identity.seller_endpoint(tin_state.state, partner.nc_tin)
        else:
            eas = partner.peppol_eas if 'peppol_eas' in partner._fields else None
            endpoint_value = partner.peppol_endpoint if 'peppol_endpoint' in partner._fields else None
            ready.update(peppol_eas=eas, peppol_endpoint=endpoint_value, not_onboarded=self.buyer_not_onboarded)
            endpoint, issues_ = identity.buyer_endpoint(
                flags=flags,
                peppol_eas=eas,
                peppol_endpoint=endpoint_value,
                tin_state=tin_state.state,
                tin=partner.nc_tin,
                not_onboarded=self.buyer_not_onboarded,
            )
        if endpoint is not None:
            party.update(
                endpoint_scheme=endpoint.scheme,
                endpoint_id=endpoint.id,
                endpoint_source=endpoint.source,
                report_only=endpoint.report_only,
            )
        else:
            party.update(endpoint_scheme=None, endpoint_id=None, endpoint_source=None, report_only=False)
        party['_ready'] = ready
        return party
