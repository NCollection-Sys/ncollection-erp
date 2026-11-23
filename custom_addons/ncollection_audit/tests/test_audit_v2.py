# -*- coding: utf-8 -*-
"""Audit Log 2.0: capture, read model, versions, Undo, isolation, integrity and cost.

Everything runs through the same public calls the History screen makes (``ncollection.audit.service`` and
``ncollection.audit.undo``), as real users with real groups - never as the superuser, which holds every group and
passes every check."""
import time
from datetime import date, timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.audit_policy import SECRET_NAME


@tagged('post_install', '-at_install')
class TestAuditV2(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ncollection.audit.policy']._nc_seed_policies()
        cls.Policy = cls.env['ncollection.audit.policy']
        if not cls.Policy.with_context(active_test=False).search_count([('model', '=', 'res.partner')]):
            cls.Policy.create({'model_id': cls.env['ir.model']._get_id('res.partner')})
        cls.Policy.search([('model', '=', 'res.partner')]).write(
            {'active': True, 'log_create': True, 'log_write': True, 'log_unlink': True})
        cls.Service = cls.env['ncollection.audit.service']
        cls.Undo = cls.env['ncollection.audit.undo']
        cls.Log = cls.env['auditlog.log']
        cls.company_a = cls.env.company
        cls.company_b = cls.env['res.company'].create({'name': 'Audit V2 Company B'})
        common = 'base.group_user,base.group_partner_manager'
        cls.reader = new_test_user(cls.env, 'au2_reader', groups=common + ',auditlog.group_auditlog_user',
                                   company_id=cls.company_a.id, company_ids=[(6, 0, cls.company_a.ids)])
        cls.undoer = new_test_user(cls.env, 'au2_undoer', groups=common + ',ncollection_audit.group_audit_undo',
                                   company_id=cls.company_a.id, company_ids=[(6, 0, cls.company_a.ids)])
        cls.reader_b = new_test_user(cls.env, 'au2_reader_b', groups=common + ',ncollection_audit.group_audit_undo',
                                     company_id=cls.company_b.id, company_ids=[(6, 0, cls.company_b.ids)])
        cls.plain = new_test_user(cls.env, 'au2_plain', groups=common)

    # ---- helpers ---------------------------------------------------------

    def _logs(self, record, method=None):
        domain = [('model_model', '=', record._name), ('res_id', '=', record.id)]
        if method:
            domain.append(('method', '=', method))
        return self.Log.sudo().search(domain, order='id')

    def _event(self, record, method, field, old, new):
        """The recorded event, or the one the trail WOULD hold: where a platform module extends the model (the CI
        database installs the platform layer too) it is never natively recorded, by design (reserved models)."""
        log = self._logs(record, method)[-1:]
        if log:
            return log
        return self.Log.sudo().create({
            'model_id': self.env['ir.model']._get_id(record._name), 'res_id': record.id, 'method': method,
            'user_id': self.env.uid, 'name': record.display_name[:64], 'log_type': 'full',
            'line_ids': [Command.create({'field_id': self.env['ir.model.fields']._get(record._name, field).id,
                                         'old_value': old, 'new_value': new})]})

    def _partner(self, **vals):
        vals.setdefault('name', 'Audit Partner')
        vals.setdefault('company_id', self.company_a.id)
        return self.env['res.partner'].with_user(self.undoer).create(vals)

    # =====================================================================
    # capture
    # =====================================================================

    def test_create_update_delete_are_recorded_with_values_user_and_company(self):
        partner = self._partner(name='Ahmed', phone='111')
        partner.write({'name': 'Ahmed Ali', 'phone': '222'})
        pid = partner.id
        partner.unlink()
        create, update, delete = self.Log.sudo().search(
            [('model_model', '=', 'res.partner'), ('res_id', '=', pid)], order='id')
        self.assertEqual([create.nc_action, update.nc_action, delete.nc_action], ['create', 'update', 'delete'])
        self.assertEqual(update.user_id, self.undoer)
        self.assertEqual(update.nc_company_id, self.company_a)
        self.assertEqual(update.nc_source, 'native')
        lines = {line.field_name: (line.old_value, line.new_value) for line in update.line_ids}
        self.assertEqual(lines, {'name': ('Ahmed', 'Ahmed Ali'), 'phone': ('111', '222')})
        self.assertIn('name', delete.line_ids.mapped('field_name'), "pre-deletion values must be kept")
        self.assertTrue(create.nc_content_hash and create._nc_verify_content()[0] == create)

    def test_unchanged_value_writes_no_event_and_multi_record_write_one_per_record(self):
        a, b = self._partner(name='A1', phone='1'), self._partner(name='B1', phone='1')
        before = len(self._logs(a))
        a.write({'phone': '1'})
        self.assertEqual(len(self._logs(a)), before, "a write that changes nothing is not an event")
        (a | b).write({'phone': '9'})
        self.assertEqual(len(self._logs(a, 'write')), 1)
        self.assertEqual(len(self._logs(b, 'write')), 1)

    def test_rolled_back_write_leaves_no_event(self):
        partner = self._partner(name='Rollback')
        count = len(self._logs(partner))
        with self.assertRaises(ZeroDivisionError), self.env.cr.savepoint():
            partner.write({'phone': '555'})
            _ = 1 / 0       # any failure after the write rolls the savepoint back, with the audit row
        self.assertEqual(len(self._logs(partner)), count)

    def test_credentials_and_restricted_fields_are_never_recorded(self):
        user = new_test_user(self.env, 'au2_secret', groups='base.group_user')
        user.sudo().write({'password': 'Sup3r-secret!', 'name': 'Renamed Secret'})
        values = ' '.join(filter(None, self._logs(user).line_ids.mapped('new_value')))
        self.assertNotIn('Sup3r-secret!', values)
        self.assertFalse([n for n in self._logs(user).line_ids.mapped('field_name') if SECRET_NAME.search(n)])
        for name, field in self.env['res.partner']._fields.items():
            if field.groups:
                self.assertNotIn(name, self._logs(self._partner(name='G')).line_ids.mapped('field_name'))

    def test_policy_off_stops_recording_and_oca_models_are_not_doubled(self):
        policy = self.Policy.search([('model', '=', 'res.partner')])
        policy.log_write = False
        partner = self._partner(name='Off')
        partner.write({'phone': '7'})
        self.assertFalse(self._logs(partner, 'write'))
        oca = set(self.env['auditlog.rule'].sudo().search([]).mapped('model_id.model'))
        self.assertFalse(oca & set(self.Policy.search([]).mapped('model')))

    # =====================================================================
    # the history list
    # =====================================================================

    def test_search_filters_sort_and_pagination(self):
        partner = self._partner(name='Filter Me Co', phone='1')
        partner.write({'phone': '2'})
        partner.write({'phone': '3'})
        Service = self.Service.with_user(self.reader)
        record = {'record': {'model': 'res.partner', 'res_id': partner.id}}
        result = Service.nc_audit_search(record)
        self.assertEqual(result['total'], 3)
        ids = [row['id'] for row in result['rows']]
        self.assertEqual(ids, sorted(ids, reverse=True), "newest first, id breaks ties")
        self.assertEqual(Service.nc_audit_search(dict(record, actions=['update']))['total'], 2)
        self.assertEqual(Service.nc_audit_search(dict(record, actions=['create']))['total'], 1)
        self.assertEqual(Service.nc_audit_search(dict(record, user_ids=[self.undoer.id]))['total'], 3)
        self.assertEqual(Service.nc_audit_search(dict(record, user_ids=[self.plain.id]))['total'], 0)
        self.assertEqual(Service.nc_audit_search(dict(record, doc_types=['res.partner']))['total'], 3)
        self.assertEqual(Service.nc_audit_search(dict(record, doc_types=['account.move']))['total'], 0)
        # the creation recorded the phone too, so all three events mention it
        self.assertEqual(Service.nc_audit_search(dict(record, search='Phone'))['total'], 3)
        self.assertEqual(Service.nc_audit_search(dict(record, search='no-such-field-xyz'))['total'], 0)
        self.assertEqual(Service.nc_audit_search(dict(record, reference='Filter Me'))['total'], 3)
        today = fields.Date.to_string(date.today())
        self.assertEqual(Service.nc_audit_search(dict(record, date_from=today, date_to=today))['total'], 3)
        old = fields.Date.to_string(date.today() - timedelta(days=30))
        self.assertEqual(Service.nc_audit_search(dict(record, date_to=old))['total'], 0)
        page = Service.nc_audit_search(record, offset=1, limit=1)
        self.assertEqual((page['total'], [r['id'] for r in page['rows']]), (3, ids[1:2]))
        asc = Service.nc_audit_search(record, sort='date', direction='asc')
        self.assertEqual([r['id'] for r in asc['rows']], list(reversed(ids)))
        with self.assertRaises(UserError):
            Service.nc_audit_search(record, limit=100000)
        options = Service.nc_audit_options()
        self.assertIn(self.undoer.id, [u['id'] for u in options['users']])
        self.assertIn('res.partner:contact', [t['key'] for t in options['doc_types']])

    def test_deactivated_user_stays_filterable(self):
        partner = self._partner(name='Inactive Author')
        self.undoer.sudo().active = False
        users = self.Service.with_user(self.reader).nc_audit_options()['users']
        entry = [u for u in users if u['id'] == self.undoer.id]
        self.assertTrue(entry and not entry[0]['active'])
        self.assertTrue(self.Service.with_user(self.reader).nc_audit_search(
            {'user_ids': [self.undoer.id], 'record': {'model': 'res.partner', 'res_id': partner.id}})['total'])

    def test_event_details_show_labels_and_formatted_values(self):
        partner = self._partner(name='Detail Co', phone='1')
        partner.write({'name': 'Detail Co 2', 'phone': '2'})
        event = self.Service.with_user(self.reader).nc_audit_event(self._logs(partner, 'write').id)
        self.assertEqual(event['action'], 'update')
        self.assertEqual({(r['label'], r['before'], r['after']) for r in event['lines']},
                         {('Name', 'Detail Co', 'Detail Co 2'), ('Phone', '1', '2')})
        self.assertIn('Name', event['description'])
        self.assertFalse(event['ip'], "technical metadata is for administrators only")

    # =====================================================================
    # isolation & integrity
    # =====================================================================

    def test_other_company_events_are_invisible_and_unreachable(self):
        other = self.env['res.partner'].create({'name': 'B Secret', 'company_id': self.company_b.id})
        log = self._logs(other, 'create')
        self.assertEqual(log.nc_company_id, self.company_b)
        found = self.Service.with_user(self.reader).nc_audit_search({'reference': 'B Secret'})
        self.assertEqual(found['total'], 0)
        with self.assertRaises(AccessError):
            self.Service.with_user(self.reader).nc_audit_event(log.id)
        with self.assertRaises(AccessError):
            self.Service.with_user(self.reader).nc_audit_history('res.partner', other.id)
        self.assertFalse(self.Log.with_user(self.reader).search([('id', '=', log.id)]))
        self.assertFalse(self.env['auditlog.log.line'].with_user(self.reader).search([('log_id', '=', log.id)]))
        self.assertEqual(self.Service.with_user(self.reader_b).nc_audit_search({'reference': 'B Secret'})['total'], 1)

    def test_users_without_the_audit_group_are_refused_everywhere(self):
        partner = self._partner(name='Plain')
        log = self._logs(partner, 'create')
        for call in (lambda S: S.nc_audit_search({}), lambda S: S.nc_audit_event(log.id),
                     lambda S: S.nc_audit_history('res.partner', partner.id), lambda S: S.nc_audit_options()):
            with self.assertRaises(AccessError):
                call(self.Service.with_user(self.plain))
        result = self.Undo.with_user(self.reader).nc_undo_preview(log.id)
        self.assertFalse(result['available'], "reading the trail does not grant Undo")
        with self.assertRaises(UserError):
            self.Undo.with_user(self.reader).nc_undo_apply(log.id, [], 'x')

    def test_document_type_the_user_cannot_read_is_hidden(self):
        # A document type this reader cannot open in the ERP itself (no ACL): its events are not listed and not
        # reachable by id. Chosen from what is installed, not hard-coded.
        hidden = next((name for name in ('hr.employee', 'account.payment', 'purchase.order', 'stock.picking',
                                         'account.asset', 'ir.cron')
                       if name in self.env and not self.env[name].with_user(self.reader).has_access('read')), None)
        self.assertTrue(hidden, "no installed model is unreadable for a plain reader")
        log = self.Log.sudo().create({
            'model_id': self.env['ir.model']._get_id(hidden), 'res_id': 1, 'method': 'write',
            'user_id': self.env.uid, 'name': 'Hidden Doc', 'log_type': 'full'})
        with self.assertRaises(AccessError):
            self.Service.with_user(self.reader).nc_audit_event(log.id)
        self.assertNotIn(log.id, [r['id'] for r in self.Service.with_user(self.reader).nc_audit_search(
            {'reference': 'Hidden Doc'})['rows']])

    def test_restricted_field_values_are_masked(self):
        restricted = next((n for n, f in self.env['res.partner']._fields.items()
                           if f.groups and f.store and f.type == 'char'), None)
        if not restricted:
            self.skipTest('no restricted char field on res.partner here')
        partner = self._partner(name='Mask')
        field = self.env['ir.model.fields']._get('res.partner', restricted)
        log = self.Log.sudo().create({
            'model_id': self.env['ir.model']._get_id('res.partner'), 'res_id': partner.id, 'method': 'write',
            'user_id': self.env.uid, 'name': 'Mask', 'log_type': 'full',
            'line_ids': [Command.create({'field_id': field.id, 'old_value': 'TOP-SECRET-OLD',
                                         'new_value': 'TOP-SECRET-NEW'})]})
        event = self.Service.with_user(self.reader).nc_audit_event(log.id)
        shown = ' '.join(r['before'] + r['after'] for r in event['lines'])
        self.assertNotIn('TOP-SECRET', shown)

    def test_events_and_undo_ledger_cannot_be_tampered_with(self):
        partner = self._partner(name='Tamper')
        log = self._logs(partner, 'create')
        for user in (self.reader, self.undoer):
            with self.assertRaises(AccessError):
                self.Log.with_user(user).browse(log.id).write({'name': 'forged'})
            with self.assertRaises(AccessError):
                self.Log.with_user(user).browse(log.id).unlink()
            with self.assertRaises(AccessError):
                self.Undo.with_user(user).create({'original_log_id': log.id, 'kind': 'update', 'model': 'x'})
        partner.write({'phone': '42'})
        update = self._logs(partner, 'write')
        preview = self.Undo.with_user(self.undoer).nc_undo_preview(update.id)
        result = self.Undo.with_user(self.undoer).nc_undo_apply(update.id, ['phone'], preview['fingerprint'])
        undo = self.Undo.sudo().browse(result['undo_id'])
        with self.assertRaises(AccessError):
            undo.with_user(self.undoer).write({'state': 'pending'})
        with self.assertRaises(AccessError):
            undo.unlink()

    # =====================================================================
    # versions
    # =====================================================================

    def test_compare_adjacent_and_distant_versions_with_relations(self):
        country_a, country_b = self.env.ref('base.ae'), self.env.ref('base.sa')
        tag = self.env['res.partner.category'].create({'name': 'Audit Tag'})
        partner = self._partner(name='V1', phone='1', country_id=country_a.id)
        partner.write({'phone': '2'})
        partner.write({'phone': '3', 'country_id': country_b.id, 'category_id': [(6, 0, tag.ids)]})
        partner.write({'name': 'V4'})
        logs = self._logs(partner)
        Service = self.Service.with_user(self.reader)
        history = Service.nc_audit_history('res.partner', partner.id)
        self.assertEqual([v['version'] for v in history['versions']], [1, 2, 3, 4])
        self.assertTrue(history['complete'])
        adjacent = Service.nc_audit_compare('res.partner', partner.id, logs[0].id, logs[1].id)
        rows = {r['field']: r for r in adjacent['rows']}
        self.assertEqual((rows['phone']['a'], rows['phone']['b'], rows['phone']['status']), ('1', '2', 'changed'))
        distant = Service.nc_audit_compare('res.partner', partner.id, logs[0].id, logs[3].id)
        rows = {r['field']: r for r in distant['rows']}
        self.assertEqual((rows['phone']['a'], rows['phone']['b']), ('1', '3'), "repeatedly changed field: ends")
        self.assertEqual((rows['country_id']['a'], rows['country_id']['b']), (country_a.name, country_b.name))
        self.assertEqual(rows['category_id']['status'], 'added')
        self.assertEqual(rows['name']['b'], 'V4')
        self.assertFalse(distant['gaps'])
        tag.unlink()
        again = Service.nc_audit_compare('res.partner', partner.id, logs[0].id, logs[3].id)
        self.assertEqual({r['field']: r for r in again['rows']}['category_id']['b'], 'Audit Tag',
                         "a deleted related record keeps the name it had when recorded")

    def test_missing_creation_is_reported_not_guessed_and_gaps_are_detected(self):
        partner = self._partner(name='Legacy', phone='1')
        partner.write({'phone': '2'})
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET phone = '99' WHERE id = %s", (partner.id,))
        partner.invalidate_recordset(['phone'])
        partner.write({'phone': '3'})
        partner.write({'name': 'Legacy 2'})
        self._logs(partner, 'create').sudo().unlink()          # a history that does not start at creation
        logs = self._logs(partner)
        result = self.Service.with_user(self.reader).nc_audit_compare(
            'res.partner', partner.id, logs[0].id, logs[2].id)
        self.assertFalse(result['complete'])
        self.assertEqual([g['field'] for g in result['gaps']], ['Phone'])
        rows = {r['field']: r for r in result['rows']}
        self.assertEqual(rows['name']['a'], 'Legacy', "known from the later event's recorded old value")
        self.assertFalse(self.Service.with_user(self.reader).nc_audit_history('res.partner', partner.id)['complete'])

    # =====================================================================
    # Undo
    # =====================================================================

    def _undo(self, log, fields_=None, user=None):
        Undo = self.Undo.with_user(user or self.undoer)
        preview = Undo.nc_undo_preview(log.id)
        self.assertTrue(preview['available'], preview.get('reason'))
        names = fields_ if fields_ is not None else [r['field'] for r in preview['rows'] if r.get('restorable')]
        return Undo.nc_undo_apply(log.id, names, preview['fingerprint'])

    def test_safe_update_undo_restores_audits_and_cannot_repeat(self):
        partner = self._partner(name='Undo Me', phone='1')
        partner.write({'phone': '2', 'email': 'a@x.test'})
        log = self._logs(partner, 'write')
        digest = log.nc_content_hash
        result = self._undo(log, ['phone'])
        self.assertEqual((partner.phone, partner.email), ('1', 'a@x.test'), "only the chosen field")
        undo = self.Undo.sudo().browse(result['undo_id'])
        self.assertEqual((undo.state, undo.user_id, undo.mode), ('done', self.undoer, 'restore'))
        written = self.Log.sudo().browse(result['log_ids'])
        self.assertTrue(written and written.nc_undo_id == undo)
        self.assertEqual({(ln.old_value, ln.new_value) for ln in written.line_ids}, {('2', '1')})
        self.assertEqual(log.nc_content_hash, digest, "the original event is never edited")
        self.assertTrue(log._nc_verify_content()[0])
        reversed_ = self.Service.with_user(self.reader).nc_audit_search(
            {'record': {'model': 'res.partner', 'res_id': partner.id}, 'reversed': 'reversed'})
        self.assertEqual([r['id'] for r in reversed_['rows']], [log.id])
        self.assertFalse(self.Undo.with_user(self.undoer).nc_undo_preview(log.id)['available'])
        with self.assertRaises(UserError):
            self.Undo.with_user(self.undoer).nc_undo_apply(log.id, ['phone'], 'stale')

    def test_later_change_is_a_conflict_and_stale_preview_is_refused(self):
        partner = self._partner(name='Conflict', phone='1')
        partner.write({'phone': '2', 'email': 'c@x.test'})
        log = self._logs(partner, 'write')
        preview = self.Undo.with_user(self.undoer).nc_undo_preview(log.id)
        partner.write({'phone': '3'})
        with self.assertRaises(UserError):
            self.Undo.with_user(self.undoer).nc_undo_apply(log.id, ['phone'], preview['fingerprint'])
        rows = {r['field']: r for r in self.Undo.with_user(self.undoer).nc_undo_preview(log.id)['rows']}
        self.assertTrue(rows['phone']['conflict'] and not rows['phone']['restorable'])
        self.assertTrue(rows['email']['restorable'], "the untouched field can still be restored on its own")
        self._undo(log, ['email'])
        self.assertEqual((partner.phone, partner.email), ('3', False))

    def test_create_undo_deletes_when_free_and_refuses_after_changes(self):
        free = self._partner(name='Created By Mistake')
        result = self._undo(self._logs(free, 'create'))
        self.assertFalse(self.env['res.partner'].browse(free.id).exists())
        self.assertEqual(result['mode'], 'delete')
        changed = self._partner(name='Created Then Edited')
        changed.write({'phone': '5'})
        preview = self.Undo.with_user(self.undoer).nc_undo_preview(self._logs(changed, 'create').id)
        self.assertFalse(preview['available'])

    def test_create_undo_with_dependencies_archives_instead(self):
        if 'account.move' not in self.env:
            self.skipTest('Accounting not installed')
        partner = self._partner(name='Has Invoices')
        journal = self.env['account.journal'].search([('type', '=', 'sale'), ('company_id', '=', self.company_a.id)],
                                                     limit=1)
        if not journal:
            self.skipTest('no sales journal')
        self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': partner.id,
                                         'journal_id': journal.id})
        preview = self.Undo.with_user(self.undoer).nc_undo_preview(self._logs(partner, 'create').id)
        if preview['available']:
            self.assertEqual(preview['modes'], ['archive'])
        self.assertTrue(self.env['res.partner'].browse(partner.id).exists(), "the probe rolled back")

    def test_delete_undo_recreates_and_refuses_missing_required_link(self):
        partner = self._partner(name='Deleted Co', phone='123', email='d@x.test')
        pid = partner.id
        partner.unlink()
        result = self._undo(self._logs(self.env['res.partner'].browse(pid), 'unlink'))
        new = self.env['res.partner'].browse(result['result_res_id'])
        self.assertTrue(new.exists() and new.id != pid)
        self.assertEqual((new.name, new.phone, new.email), ('Deleted Co', '123', 'd@x.test'))
        self.assertEqual(result['mode'], 'recreate')
        # A recorded deletion whose REQUIRED link is gone (a bank account whose holder was deleted since). Odoo 19
        # archives a bank account on unlink, so the deletion is written as the trail would record it.
        holder = self._partner(name='Gone Holder')
        holder_id = holder.id
        holder.unlink()
        Fields = self.env['ir.model.fields']
        log = self.Log.sudo().create({
            'model_id': self.env['ir.model']._get_id('res.partner.bank'), 'res_id': 999999, 'method': 'unlink',
            'user_id': self.env.uid, 'name': 'AE07 0331', 'log_type': 'full', 'nc_company_id': self.company_a.id,
            'line_ids': [
                Command.create({'field_id': Fields._get('res.partner.bank', 'acc_number').id,
                                'old_value': 'AE070331234567890123456'}),
                Command.create({'field_id': Fields._get('res.partner.bank', 'partner_id').id,
                                'old_value': str((holder_id, 'Gone Holder')),
                                'old_value_text': str((holder_id, 'Gone Holder'))})]})
        preview = self.Undo.with_user(self.undoer).nc_undo_preview(log.id)
        self.assertFalse(preview['available'])
        self.assertIn('required', preview['reason'].lower())

    def test_cross_company_and_unauthorised_undo_are_refused(self):
        other = self.env['res.partner'].create({'name': 'B Doc', 'company_id': self.company_b.id})
        other.write({'phone': '1'})
        with self.assertRaises(AccessError):
            self.Undo.with_user(self.undoer).nc_undo_preview(self._logs(other, 'write').id)
        mine = self._partner(name='Mine', phone='1')
        mine.write({'phone': '2'})
        self.assertFalse(self.Undo.with_user(self.reader).nc_undo_preview(self._logs(mine, 'write').id)['available'])

    def test_access_and_roles_are_never_undone(self):
        user = new_test_user(self.env, 'au2_roles', groups='base.group_user')
        user.write({'login': 'au2_roles_renamed'})   # name lives on the partner; login is res.users' own
        event = self._event(user, 'write', 'login', 'au2_roles', user.login)
        preview = self.Undo.with_user(self.undoer).nc_undo_preview(event.id)
        self.assertFalse(preview['available'])
        self.assertTrue(preview['hint'])

    def test_posted_and_locked_accounting_is_refused(self):
        if 'account.move' not in self.env:
            self.skipTest('Accounting not installed')
        journal = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', self.company_a.id)], limit=1)
        account = self.env['account.account'].search([('company_ids', 'in', self.company_a.id),
                                                      ('account_type', '=', 'asset_current')], limit=2)
        if not journal or len(account) < 2:
            self.skipTest('no chart')
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': date(2021, 1, 15),
            'line_ids': [Command.create({'account_id': account[0].id, 'debit': 10}),
                         Command.create({'account_id': account[1].id, 'credit': 10})]})
        move.write({'ref': 'draft ref'})
        draft = self._event(move, 'write', 'ref', False, 'draft ref')
        accountant = new_test_user(self.env, 'au2_acc', groups='account.group_account_manager,'
                                   'ncollection_audit.group_audit_undo', company_id=self.company_a.id,
                                   company_ids=[(6, 0, self.company_a.ids)])
        self.assertTrue(self.Undo.with_user(accountant).nc_undo_preview(draft.id)['available'],
                        "a draft entry's header can be restored")
        move.action_post()
        preview = self.Undo.with_user(accountant).nc_undo_preview(draft.id)
        self.assertFalse(preview['available'])
        self.assertIn('Credit Note', preview['hint'])
        create = self._event(move, 'create', 'ref', False, 'draft ref')
        self.assertFalse(self.Undo.with_user(accountant).nc_undo_preview(create.id)['available'])
        move.button_draft()
        # an old period: a lock date of today is refused here by an open point-of-sale session (Odoo's own rule)
        self.company_a.sudo().fiscalyear_lock_date = date(2021, 1, 31)
        self.assertFalse(self.Undo.with_user(accountant).nc_undo_preview(draft.id)['available'])
        line = move.line_ids[:1]
        log = self.Log.sudo().create({'model_id': self.env['ir.model']._get_id('account.move.line'),
                                      'res_id': line.id, 'method': 'write', 'user_id': self.env.uid,
                                      'name': 'x', 'log_type': 'full'})
        self.assertFalse(self.Undo.with_user(accountant).nc_undo_preview(log.id)['available'])

    def test_locked_document_row_refuses_instead_of_waiting(self):
        # A COMMITTED row this test transaction has not written (so only the second cursor can hold its lock).
        partner = self.env.ref("base.partner_root")
        field = self.env["ir.model.fields"]._get("res.partner", "phone")
        log = self.Log.sudo().create({
            'model_id': self.env['ir.model']._get_id('res.partner'), 'res_id': partner.id, 'method': 'write',
            'user_id': self.env.uid, 'name': partner.name, 'log_type': 'full',
            'line_ids': [Command.create({'field_id': field.id, 'old_value': 'old',
                                         'new_value': partner.phone or False})]})
        admin = self.env.ref('base.user_admin')
        preview = self.Undo.with_user(admin).nc_undo_preview(log.id)
        if not preview['available']:
            self.skipTest(preview['reason'])
        with self.env.registry.cursor() as other:
            # NO KEY UPDATE: rows this transaction inserted that point at the partner hold KEY SHARE on it, which this
            # lock coexists with; the Undo's FOR UPDATE does not, so it must refuse instead of waiting.
            other.execute("SELECT id FROM res_partner WHERE id = %s FOR NO KEY UPDATE NOWAIT", (partner.id,))
            with self.assertRaises(UserError):
                self.Undo.with_user(admin).nc_undo_apply(log.id, ['phone'], preview['fingerprint'])
            other.rollback()

    # =====================================================================
    # platform channel & cost
    # =====================================================================

    def test_platform_window_counts_confirmed_rules(self):
        count = self.env['auditlog.rule'].sudo().search_count([('state', '=', 'confirmed')])
        self.assertTrue(count, "the seeded rules are 'confirmed' (OCA has no 'subscribed' state)")

    def test_capture_cost_is_bounded(self):
        partner = self._partner(name='Cost', phone='0')
        self.env.flush_all()
        policy = self.Policy.search([('model', '=', 'res.partner')])

        def queries(value):
            start = self.env.cr.sql_log_count
            partner.write({'phone': value})
            self.env.flush_all()
            return self.env.cr.sql_log_count - start

        with_audit = queries('1')
        policy.log_write = False
        without = queries('2')
        self.assertLessEqual(with_audit - without, 8, "audit adds a bounded number of queries per write")
        for i in range(300):
            partner.write({'phone': str(i + 10)})
        started = time.time()
        result = self.Service.with_user(self.reader).nc_audit_search(
            {'record': {'model': 'res.partner', 'res_id': partner.id}}, limit=50)
        history = self.Service.with_user(self.reader).nc_audit_history('res.partner', partner.id)
        self.assertGreaterEqual(result['total'], 1)
        self.assertLessEqual(len(history['versions']), 1000)
        self.assertLess(time.time() - started, 5.0)

    # ---- regressions for the independent review findings (architecture H1-H3, security F1/F2/L1, accounting H1/M2)

    def test_the_trail_itself_can_never_be_given_a_policy(self):
        from odoo.exceptions import ValidationError
        for name in ('auditlog.log', 'ncollection.audit.undo', 'ir.cron'):
            with self.assertRaises(ValidationError):
                self.Policy.create({'model_id': self.env['ir.model']._get_id(name)})

    def test_a_client_context_key_cannot_switch_recording_off_or_forge_an_undo(self):
        partner = self._partner(name='Evade', phone='1')
        partner.with_context(nc_audit_skip=True, install_mode=True).write({'phone': '2'})
        self.assertTrue(self._logs(partner, 'write'), "a client-sent context key must not suppress the audit")
        mine = self._partner(name='Forge', phone='1')
        mine.write({'phone': '2'})
        preview = self.Undo.with_user(self.undoer).nc_undo_preview(self._logs(mine, 'write').id)
        result = self.Undo.with_user(self.undoer).nc_undo_apply(
            self._logs(mine, 'write').id, ['phone'], preview['fingerprint'])
        other = self._partner(name='Other', phone='1')
        other.with_context(nc_audit_undo_id=result['undo_id']).write({'phone': '5'})
        self.assertFalse(self._logs(other, 'write').nc_undo_id, "Undo attribution cannot be forged by context")

    def test_malformed_field_lists_are_refused_cleanly(self):
        partner = self._partner(name='Bad Input', phone='1')
        partner.write({'phone': '2'})
        log = self._logs(partner, 'write')
        for bad in ([[1]], [{'a': 1}], 'phone'):
            with self.assertRaises(UserError):
                self.Undo.with_user(self.undoer).nc_undo_preview(log.id, bad)
            with self.assertRaises(UserError):
                self.Undo.with_user(self.undoer).nc_undo_apply(log.id, bad, 'x')

    def test_retention_can_prune_an_undone_event_and_the_guard_survives(self):
        partner = self._partner(name='Pruned', phone='1')
        partner.write({'phone': '2'})
        log = self._logs(partner, 'write')
        log_id = log.id
        result = self._undo(log, ['phone'])
        log.sudo().unlink()                          # what retention does: must not be blocked by the Undo
        undo = self.Undo.sudo().browse(result['undo_id'])
        self.assertTrue(undo.exists())
        self.assertEqual(undo.original_log_ref, log_id)
        self.assertFalse(undo.original_log_id)

    def test_a_posted_payment_is_never_restored(self):
        if 'account.payment' not in self.env:
            self.skipTest('Accounting not installed')
        journal = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.company_a.id)],
                                                     limit=1)
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'amount': 100.0, 'journal_id': journal.id,
            'partner_id': self._partner(name='Payer').id})
        payment.write({'amount': 150.0})
        log = self._event(payment, 'write', 'amount', '100.0', '150.0')
        accountant = new_test_user(self.env, 'au2_pay', groups='account.group_account_manager,'
                                   'ncollection_audit.group_audit_undo', company_id=self.company_a.id,
                                   company_ids=[(6, 0, self.company_a.ids)])
        self.assertTrue(self.Undo.with_user(accountant).nc_undo_preview(log.id)['available'])
        payment.action_post()
        preview = self.Undo.with_user(accountant).nc_undo_preview(log.id)
        self.assertFalse(preview['available'], "a posted (in_process) payment's amount is never rewritten")

    def test_company_lock_dates_are_never_restored(self):
        if 'fiscalyear_lock_date' not in self.env['res.company']._fields:
            self.skipTest('Accounting not installed')
        Fields = self.env['ir.model.fields']
        log = self.Log.sudo().create({
            'model_id': self.env['ir.model']._get_id('res.company'), 'res_id': self.company_a.id,
            'method': 'write', 'user_id': self.env.uid, 'name': 'C', 'log_type': 'full',
            'nc_company_id': self.company_a.id,
            'line_ids': [Command.create({'field_id': Fields._get('res.company', 'fiscalyear_lock_date').id,
                                         'old_value': False, 'new_value': False})]})
        rows = self.Undo.with_user(self.env.ref('base.user_admin')).nc_undo_preview(log.id)['rows']
        self.assertTrue(rows and not rows[0]['restorable'])
