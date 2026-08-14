# -*- coding: utf-8 -*-
"""P8-T05: which models are audited, expressed as DATA rather than as clicks.

``auditlog.rule`` is an ordinary editable model. Nothing upstream seeds it — the
manifest ships groups, ACLs, a cron and five view files, and no rule data at
all. So out of the box, auditing is configured by a human opening a form, once
per model, once per database. On a fleet of tenant databases that is not a
configuration story, it is a promise nobody can keep.

WHY THIS IS NOT JUST A ``post_init_hook``
=========================================

REGRESSIONS.md R-014, twice: a hook that grants or seeds something belonging to
a module installed LATER silently does nothing, and nothing goes red. Here the
shape is exactly that — this module depends on ``auditlog`` alone (deliberately;
see the manifest), so at install time ``account`` and ``sale`` may not exist
yet, and a one-shot hook would seed four rules, miss the two that matter most,
and report success.

So the seeder is:

* **idempotent** — it creates only what is missing, and a second run is a no-op.
  Standing Rule 12 asks for that to be *proved*, and a test runs it twice.
* **re-run on every registry load**, via ``_register_hook``. Installing
  ``account`` next week rebuilds the registry, the seeder notices
  ``account.move`` now exists, and the rule appears without anyone remembering.
* **public**, so provisioning can call it explicitly rather than hoping.

WHICH MODELS, AND WHY THIS LIST
===============================

``account.move.line`` is on the list and **is not in the ticket's model list** —
the ticket names ``account.move``, ``res.partner``, ``sale.order``,
``res.users``. It is here because the acceptance criterion demands it and the
ticket's own model list cannot satisfy it. Measured on a scratch database before
any of this was written:

    PROBE amount_total before: 100.0
    PROBE amount_total after : 250.0
    PROBE move logs before/after: 4 4

Changing an invoice line's ``price_unit`` moved ``amount_total`` from 100 to 250
and produced **zero** ``account.move`` audit rows. ``amount_total`` is a stored
computed field; it is written by recompute-flush, which never passes through the
``write()`` that ``auditlog`` patches. A rule on ``account.move`` alone
therefore satisfies "changing an invoice amount produces an audit entry" only in
prose.

``ncollection.*`` models are discovered rather than listed, and the set is
correctly DIFFERENT per database: a tenant database has the tenant-layer models,
the platform database has ``ncollection.tenant``/``ncollection.subscription``.
Discovery keeps that honest without this module having to know which side it is
installed on — which is also why it must never hardcode a platform model name
(Rule 3: the tenant layer has no business naming platform models).

EXCLUDED FIELDS ARE A SIGNAL DECISION BEFORE THEY ARE A COST DECISION. One
invoice create emitted three write logs carrying ``sequence_prefix``,
``invoice_partner_display_name``, ``payment_state`` and friends — churn from
Odoo's own recomputes, not from anything a person did. Left in, the rows that
matter are buried.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Named because the ticket names them — plus account.move.line, which the
# acceptance criterion requires and the ticket's list omits (see the docstring).
CRITICAL_MODELS = (
    'account.move',
    'account.move.line',
    'sale.order',
    # 'res.partner' IS DELIBERATELY ABSENT — see _NC_WITHHELD.
    # 'res.users' IS DELIBERATELY ABSENT, and the ticket names it. See
    # _NC_WITHHELD below — auditing it disables a licence seat limit.
)

# Models the ticket asks for and this module REFUSES to audit, with the reason.
# Written as data so it can be asserted by a test: an omission that is not
# tested reads as an oversight, and the next person quietly "fixes" it.
_NC_WITHHELD = {
    'res.partner':
        "full-mode auditing BREAKS res.partner creation. auditlog's write_full/"
        "create_full read every stored field before and after the write inside "
        "a ThrowAwayCache, which clears the transaction cache; a recompute "
        "queued against that cache then dies with `KeyError` in "
        "`models._flush` -> `field_data[self][record.id]`. Measured: Odoo's own "
        "AccountTestInvoicingCommon.setUpClass fails at "
        "`env['res.partner'].create(...)`, taking out setUpClass across "
        "ncollection_account_reports, ncollection_account_analytics, "
        "ncollection_billing and others. This is upstream's OWN documented "
        "trap — account_asset_management's sibling test_auditlog module carries "
        "test_account_bank_statement_line.py for exactly this failure mode. "
        "Tracked separately; it needs per-model work, not a flag.",
    'res.users':
        "auditing res.users DISABLES the plan seat limit. ncollection_core "
        "enforces max_users by overriding res.users.create "
        "(models/res_users.py); auditlog enforces auditing by monkeypatching "
        "create onto the model class (_patch_method: "
        "`setattr(model_class, method_name, new_method)`). The two collide and "
        "the licence check loses. Measured by bisection: with res.users seeded "
        "ncollection_core fails test_limit_blocks_raw_orm_create and "
        "test_reactivation_counts_against_limit; dropping it alone returns the "
        "suite to 0 failed. That test's own docstring says why it exists — "
        "'Rule 4: the real control is in res.users.create, not just the UI' — "
        "so shipping this would let a tenant create users past the seats they "
        "pay for. Tracked separately; an audit trail must not punch a hole in "
        "billing enforcement to get itself installed.",
}

# Recompute churn, not user intent. Excluded so the trail stays readable.
NOISY_FIELDS = {
    'account.move': (
        'sequence_prefix', 'sequence_number', 'invoice_partner_display_name',
        'commercial_partner_id', 'payment_state', 'amount_residual',
        'amount_residual_signed', 'amount_total_in_currency_signed',
        'invoice_date_due', 'access_token', 'message_ids', 'message_follower_ids',
    ),
    'account.move.line': (
        'cumulated_balance', 'matching_number', 'move_name',
        'company_currency_id', 'date_maturity',
    ),
    'res.users': ('login_date', 'access_token'),
}


class AuditlogRule(models.Model):
    _inherit = 'auditlog.rule'

    # ---- discovery -------------------------------------------------------

    @api.model
    def _nc_audited_models(self):
        """Model names this database should audit, as they exist RIGHT NOW.

        Absent models are skipped, not faked: `sale.order` on a tenant without
        Sales installed is not a gap, and creating a rule against a missing
        model would fail the install for no benefit.
        """
        IrModel = self.env['ir.model'].sudo()
        names = [name for name in CRITICAL_MODELS
                 if IrModel.search_count([('model', '=', name)])]
        # Every persistent ncollection model present here. Transients are
        # wizards — a form somebody opened, not a fact worth keeping — and this
        # module's own models would recurse into auditing the audit trail.
        for record in IrModel.search([('model', '=like', 'ncollection.%')]):
            model = self.env.get(record.model)
            if model is None or model._transient or model._abstract:
                continue
            if record.model.startswith('ncollection.audit'):
                continue
            names.append(record.model)
        return sorted(set(names))

    # ---- seeding ---------------------------------------------------------

    @api.model
    def _nc_excluded_fields(self, model):
        """Fields that must never be copied into the audit trail.

        TWO CLASSES, and the first is a security control rather than tidiness.

        **Group-restricted fields.** `auditlog.get_auditlog_fields()` returns
        every stored field and consults a field's `groups=` attribute not at
        all. `auditlog.log.line` is readable by `auditlog.group_auditlog_user`.
        So auditing a model with a restricted field copies that value, in
        cleartext, into a table a DIFFERENT and lower-trust group can read —
        defeating the restriction the field declared.

        That is not hypothetical. `ncollection.tenant.checkout_token` is
        `fields.Char(..., groups='base.group_system')` and holds a live
        `secrets.token_urlsafe(32)` bearer credential: whoever has it can call
        `GET /nc/checkout/verify/<token>` and complete someone else's tenant
        activation without touching their mailbox. Blanket discovery would have
        published it to every audit viewer. Found in security review.

        The rule is generic ON PURPOSE — the alternative, naming the field in a
        per-model list, protects today's secret and silently exposes the next
        one somebody adds. `groups=` is the ORM's own statement that a value is
        not for general reading, and this honours it.

        **Recompute churn.** The second class is signal, not security: one
        invoice create emitted three write logs carrying `sequence_prefix`,
        `invoice_partner_display_name` and `payment_state`.
        """
        IrModelFields = self.env['ir.model.fields'].sudo()
        model_obj = self.env.get(model.model)
        noisy = set(NOISY_FIELDS.get(model.model, ()))
        excluded = IrModelFields.browse()
        for record in IrModelFields.search([('model_id', '=', model.id)]):
            field = (model_obj._fields.get(record.name)
                     if model_obj is not None else None)
            if (field is not None and field.groups) or record.name in noisy:
                excluded |= record
        return excluded

    @api.model
    def _nc_seed_rules(self):
        """Create any missing audit rule. Idempotent; returns what it created.

        `log_type='full'` is NOT a preference. `write_fast` sets
        `old_vals2 = dict.fromkeys(list(vals2.keys()), False)` — under 'fast'
        every logged old value is a hardcoded False, so the acceptance
        criterion's "old/new values" would be satisfied in shape and false in
        substance. Verified in the vendored source, not assumed.
        """
        IrModel = self.env['ir.model'].sudo()
        created = self.browse()
        for name in self._nc_audited_models():
            model = IrModel.search([('model', '=', name)], limit=1)
            if not model:
                continue
            existing = self.sudo().search([('model_id', '=', model.id)])
            if existing:
                # RE-SYNC, do not skip. The exclusion list was computed from
                # the fields that existed when the rule was seeded; a module
                # installed later adds fields to the same model, and a
                # group-restricted one among them would start being written
                # into the trail with nothing to notice. Measured: seeding at
                # install time left 30 restricted `res.partner` fields
                # unexcluded once mail/account/sale were installed. Same shape
                # as R-014, one level down — it is not enough to re-seed
                # MISSING rules if existing ones go stale.
                wanted = self._nc_excluded_fields(model)
                if set(wanted.ids) != set(existing.fields_to_exclude_ids.ids):
                    existing.sudo().write(
                        {'fields_to_exclude_ids': [(6, 0, wanted.ids)]})
                continue
            excluded = self._nc_excluded_fields(model)
            # SAVEPOINT: `auditlog.rule` carries `unique(model_id)`, and this
            # is a search-then-create with no lock. `_register_hook` runs on
            # every registry load, so a fleet-wide upgrade can reload many
            # workers for one database at once and two of them can both decide
            # the rule is missing. Without the savepoint the constraint
            # violation leaves the cursor in a failed transaction and every
            # later query on it dies — a far bigger blast radius than one
            # unseeded rule.
            with self.env.cr.savepoint():
                rule = self.sudo().create({
                    'name': "NCollection audit: %s" % name,
                    'model_id': model.id,
                    'log_write': True,
                    'log_create': True,
                    'log_unlink': True,
                    # Deliberately NOT log_read: the highest-volume operation
                    # in an ERP and the lowest-value audit signal. A read trail
                    # is a separate decision with a separate cost.
                    'log_read': False,
                    # export_data defaults to True upstream. Its evidence lives
                    # in `res_ids`; the digest now covers that field, but the
                    # export path is out of this ticket's scope, so it stays
                    # off rather than shipping half-attested.
                    'log_export_data': False,
                    'log_type': 'full',
                    'fields_to_exclude_ids': [(6, 0, excluded.ids)],
                })
                rule.set_to_confirmed()
            created |= rule
        if created:
            _logger.info("ncollection_audit: seeded %s audit rule(s): %s",
                         len(created), ", ".join(created.mapped('model_model')))
        return created

    def _register_hook(self):
        """Re-seed on every registry load, so a module installed LATER is
        picked up without anyone remembering to re-run anything.

        This is the R-014 fix, not belt-and-braces: a `post_init_hook` alone
        seeds what exists at install time and then never looks again, which is
        the exact failure that shipped twice. Cheap when there is nothing to do
        — one indexed search per candidate model.
        """
        result = super()._register_hook()
        try:
            self._nc_seed_rules()
        except Exception:                             # pragma: no cover
            # A registry load that dies here takes the whole database down. An
            # audit rule that failed to seed is a gap; a database that will not
            # boot is an outage. Log loudly and let the registry finish.
            _logger.exception(
                "ncollection_audit: seeding audit rules failed; the audit "
                "trail may be incomplete on this database")
        return result
