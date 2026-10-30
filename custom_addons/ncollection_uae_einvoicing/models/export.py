# -*- coding: utf-8 -*-
"""The retention / offboarding evidence export (R46): one company's e-invoicing evidence as ONE verifiable file.

An e-invoicing MANAGER produces it (Configuration > Evidence Exports > Create evidence package); the export record is
append-only and IS the audit trail of who produced which package when (never inside the package, which is
deterministic: lib/export_package.py). The package holds, for every e-invoice of the company: identity, the five
states,
MLS and business state, the payload, every finding, the full history, every submission with its attempts, every
provider event (metadata, never a provider body; the engine never stored one), the TDD / MLS references, and every XML
it ever archived (superseded ones included) with its sha256; and for every received e-invoice: its facts, states,
history and the received XML. A checksum manifest covers every file.

ELEVATION, AND ITS BOUNDARY. The archive models are readable by e-invoicing users and their XML files are guarded
attachments; the package needs all of them, so :meth:`_nc_collect` reads with ``sudo()`` AFTER the manager check and
ONLY for ``company`` - a company of the manager's own allowed companies, checked first. Nothing of another company, and
nothing outside this database (a tenant), can enter a package. No credential is ever included (the secret model is not
read at all)."""

import base64
import hashlib

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..lib import export_package

MANAGER = 'ncollection_uae_einvoicing.group_einvoice_manager'

EINVOICE_FIELDS = (
    'name', 'revision', 'document_kind', 'self_billing', 'type_code', 'spec_version', 'customization_id', 'profile_id',
    'uuid', 'invoice_number', 'seller_tin', 'seller_trn', 'seller_endpoint_scheme', 'seller_endpoint_id', 'buyer_tin',
    'buyer_trn', 'buyer_endpoint_scheme', 'buyer_endpoint_id', 'buyer_endpoint_source', 'asp_participant_id',
    'asp_reference', 'exchange_reference', 'fta_reference', 'transaction_type_code', 'transaction_date',
    'issue_deadline', 'scope_state', 'scope_reason_code', 'local_state', 'submission_state', 'exchange_state',
    'fta_state', 'mls_state', 'business_state', 'locked', 'reconciliation_state', 'xml_sha256', 'retention_class',
    'retention_anchor_date', 'payload_json', 'reconciliation_json', 'create_date',
)
FINDING_FIELDS = ('layer', 'severity', 'rule', 'subject', 'message', 'spec_version')
HISTORY_FIELDS = ('at', 'actor_id', 'dimension', 'from_state', 'to_state', 'event', 'detail', 'response_digest',
                  'attachment_id')
SUBMISSION_FIELDS = ('revision', 'idempotency_key', 'correlation_id', 'state', 'attempt_count', 'asp_document_id',
                     'received_at', 'duplicate_seen', 'error_category', 'error_code', 'error_message', 'http_status',
                     'provider_id', 'create_date')
ATTEMPT_FIELDS = ('at', 'number', 'outcome', 'error_category', 'error_code', 'http_status', 'retry_at')
EVENT_FIELDS = ('source', 'message_id', 'kind', 'status', 'correlation_id', 'asp_document_id', 'occurred_at',
                'metadata_json', 'state', 'note', 'applied_at')
INBOUND_FIELDS = ('message_id', 'asp_document_id', 'correlation_id', 'sender_endpoint', 'receiver_endpoint',
                  'received_at', 'xml_sha256', 'document_kind', 'type_code', 'self_billed', 'invoice_number',
                  'document_uuid', 'issue_date', 'due_date', 'currency_code', 'billing_reference', 'tax_exclusive',
                  'tax_total', 'tax_inclusive', 'payable', 'seller_name', 'seller_trn', 'seller_endpoint',
                  'buyer_trn', 'buyer_endpoint', 'state', 'reason_code', 'reason', 'partner_id', 'bill_id',
                  'duplicate_of_id', 'provider_id', 'create_date')
INBOUND_HISTORY_FIELDS = ('at', 'actor_id', 'from_state', 'to_state', 'event', 'detail')


def _plain(record, names):
    """The listed fields as JSON-safe values; a relation becomes ``[id, display name]`` (ids are stable in a
    database)."""
    out = {}
    for name in names:
        if name not in record._fields:
            continue
        value, field = record[name], record._fields[name]
        if field.type == 'many2one':
            out[name] = [value.id, value.display_name] if value else None
        elif field.type in ('date', 'datetime'):
            out[name] = value.isoformat() if value else None
        elif field.type == 'float':
            out[name] = '%.4f' % value
        else:
            out[name] = value if value not in (None,) else None
    return out


class EInvoiceExport(models.Model):
    _name = 'ncollection.einvoice.export'
    _description = 'UAE e-invoicing evidence export (append-only)'
    _order = 'id desc'

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    requested_by = fields.Many2one('res.users', required=True, readonly=True)
    requested_at = fields.Datetime(required=True, readonly=True)
    attachment_id = fields.Many2one('ir.attachment', string='Package', readonly=True, ondelete='restrict')
    package_sha256 = fields.Char(readonly=True)
    manifest_sha256 = fields.Char(readonly=True)
    einvoice_count = fields.Integer(readonly=True)
    inbound_count = fields.Integer(readonly=True)
    file_count = fields.Integer(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(self.env._("Evidence exports are created with 'Create evidence package' only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su or set(vals) - {'attachment_id'}:
            raise UserError(self.env._("An evidence export is a record of what was produced; it never changes."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        raise UserError(self.env._("An evidence export is part of the audit trail and is never deleted."))

    # ------------------------------------------------------------------ the action
    @api.model
    def action_create_package(self, company=None):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(self.env._("Only an e-invoicing manager can export the e-invoicing evidence."))
        company = company or self.env.company
        if company not in self.env.user.company_ids:
            raise AccessError(self.env._("You can export only the evidence of your own companies."))
        files, header = self._nc_collect(company)
        package, manifest_sha = export_package.build(files, header)
        digest = hashlib.sha256(package).hexdigest()
        record = self.sudo().create({
            'company_id': company.id, 'requested_by': self.env.uid, 'requested_at': fields.Datetime.now(),
            'package_sha256': digest, 'manifest_sha256': manifest_sha, 'einvoice_count': header['einvoices'],
            'inbound_count': header['inbound'], 'file_count': len(files),
        })
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'einvoice-evidence-%s-%s.zip' % (export_package.safe_part(self.env.cr.dbname), record.id),
            'datas': base64.b64encode(package), 'mimetype': 'application/zip', 'res_model': self._name,
            'res_id': record.id, 'company_id': company.id,
        })
        record.sudo().write({'attachment_id': attachment.id})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': record.id, 'view_mode': 'form'}

    @api.model
    def _nc_collect(self, company):
        """``(files, header)`` for ``company`` only. See the module docstring for the elevation boundary."""
        files = {}
        EInvoice = self.env['ncollection.einvoice'].sudo().with_context(active_test=False)
        einvoices = EInvoice.search([('company_id', '=', company.id)], order='id')
        for record in einvoices:
            folder = 'einvoices/%s-%s' % (export_package.safe_part(record.name), record.id)
            attachments = (record.history_ids.attachment_id | record.xml_attachment_id).sorted('id')
            xmls = []
            for number, attachment in enumerate(attachments, 1):
                raw = attachment.raw or b''
                sha = hashlib.sha256(raw).hexdigest()
                path = '%s/xml-%d-%s.xml' % (folder, number, sha[:12])
                files[path] = raw
                xmls.append({'path': path, 'sha256': sha, 'attachment_id': attachment.id,
                             'current': attachment == record.xml_attachment_id})
            submissions = self.env['ncollection.einvoice.submission'].sudo().search(
                [('einvoice_id', '=', record.id)], order='id')
            data = {
                'einvoice': _plain(record, EINVOICE_FIELDS),
                'accounting_document': [record.move_id.id, record.move_id.name, record.move_id.move_type],
                'self_billing_agreement': (_plain(record.self_billing_agreement_id, ('name', 'partner_id',
                                                                                     'date_from', 'date_to'))
                                           if record.self_billing_agreement_id else None),
                'xml': xmls,
                'findings': [_plain(v, FINDING_FIELDS) for v in record.validation_ids.sorted('id')],
                'history': [_plain(h, HISTORY_FIELDS) for h in record.history_ids.sorted('id')],
                'submissions': [dict(_plain(s, SUBMISSION_FIELDS),
                                     attempts=[_plain(a, ATTEMPT_FIELDS) for a in s.attempt_ids.sorted('id')])
                                for s in submissions],
                'events': [_plain(e, EVENT_FIELDS) for e in self.env['ncollection.einvoice.event'].sudo().search(
                    [('einvoice_id', '=', record.id)], order='id')],
            }
            files['%s/record.json' % folder] = export_package.dumps(data)
        inbound = self.env['ncollection.einvoice.inbound'].sudo().search(
            [('company_id', '=', company.id)], order='id')
        for record in inbound:
            folder = 'inbound/%s' % record.id
            raw = record.xml_attachment_id.raw or b''
            if raw:
                files['%s/received.xml' % folder] = raw
            data = {'inbound': _plain(record, INBOUND_FIELDS),
                    'received_xml_sha256': hashlib.sha256(raw).hexdigest() if raw else None,
                    'history': [_plain(h, INBOUND_HISTORY_FIELDS) for h in record.history_ids.sorted('id')]}
            files['%s/record.json' % folder] = export_package.dumps(data)
        header = {
            'database': self.env.cr.dbname,
            'company': [company.id, company.name],
            'company_trn': company.partner_id.nc_trn or None,
            'einvoices': len(einvoices),
            'inbound': len(inbound),
            'contains': ['identity and states', 'payload', 'findings', 'history', 'archived XML with sha256',
                         'submissions and attempts', 'provider events and TDD / MLS references',
                         'received documents'],
            'excludes': ['provider credentials', 'provider message bodies (never stored)'],
        }
        return files, header
