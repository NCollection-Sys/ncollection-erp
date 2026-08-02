# -*- coding: utf-8 -*-
"""Tenant Fleet Migration Orchestrator (P3-T14).

Applies an ``odoo -u <modules>`` to EVERY tenant database safely, per
ARCHITECTURE_DATA_PLATFORM §7: inventory from the registry → a canary set with a
verification gate → rolling waves of N → per-DB pre-upgrade snapshot, upgrade,
smoke probe, status → failure isolation (a failed tenant is optionally restored
from its snapshot, flagged, and excluded; the wave continues regardless).

Two-layer safety: this PLATFORM orchestrator never opens a cross-DB ORM cursor.
All per-tenant work runs in isolated ``odoo`` subprocesses (see the line model);
the orchestrator only reads the platform registry and writes its own records.
"""
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Reuse the existing provisioning queue channel (its capacity bounds concurrency).
FLEET_CHANNEL = 'root.provisioning'
# A valid Odoo module technical name (underscores allowed, unlike a DB name).
MODULE_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,62}$')
_PASS_STATES = ('done', 'skipped')
_TERMINAL_STATES = ('done', 'failed', 'restored', 'skipped')


class FleetMigration(models.Model):
    _name = 'ncollection.fleet.migration'
    _description = 'Tenant Fleet Migration'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True, tracking=True,
                       default=lambda self: self._default_name())
    module_names = fields.Char(
        required=True, tracking=True,
        help="Comma-separated technical module names to act on across every "
             "ready tenant.")
    operation = fields.Selection([
        ('upgrade', 'Upgrade (odoo -u)'),
        ('install', 'Install (odoo -i)'),
    ], default='upgrade', required=True, tracking=True,
        help="Upgrade acts on modules the tenant ALREADY has; install adds "
             "modules it does not. They are not interchangeable — see below.")
    wave_size = fields.Integer(
        default=5, required=True,
        help="Tenants per rolling wave after the canary passes.")
    canary_tenant_ids = fields.Many2many(
        'ncollection.tenant', 'ncollection_fleet_migration_canary_rel',
        'migration_id', 'tenant_id', string='Canary Tenants',
        help="Upgraded first; the fleet rolls out only if ALL canaries pass.")
    dry_run = fields.Boolean(
        default=False,
        help="Log the intended upgrade for every tenant without touching any "
             "database.")
    auto_restore = fields.Boolean(
        string='Auto-restore on failure', default=True,
        help="On a tenant upgrade failure, automatically drop and restore that "
             "tenant from its pre-upgrade snapshot (ARCHITECTURE §7.4). Off = "
             "flag it and stop for a manual restore; the snapshot is taken "
             "either way.")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('canary', 'Canary Running'),
        ('canary_failed', 'Canary Failed'),
        ('rolling', 'Rolling Out'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True)
    current_wave = fields.Integer(default=0, readonly=True)
    line_ids = fields.One2many(
        'ncollection.fleet.migration.line', 'migration_id', string='Targets')
    log = fields.Text(readonly=True)
    target_count = fields.Integer(compute='_compute_counts')
    done_count = fields.Integer(compute='_compute_counts')
    failed_count = fields.Integer(compute='_compute_counts')

    @api.model
    def _default_name(self):
        return self.env._("Fleet migration %s", fields.Datetime.now())

    @api.depends('line_ids.state')
    def _compute_counts(self):
        for migration in self:
            lines = migration.line_ids
            migration.target_count = len(lines)
            migration.done_count = len(lines.filtered(lambda line: line.state == 'done'))
            migration.failed_count = len(
                lines.filtered(lambda line: line.state in ('failed', 'restored')))

    # ---- validation ------------------------------------------------------

    def _module_list(self):
        self.ensure_one()
        mods = [m.strip() for m in (self.module_names or '').split(',') if m.strip()]
        if not mods:
            raise ValidationError(self.env._("Specify at least one module to upgrade."))
        for module in mods:
            if not MODULE_NAME_RE.match(module):
                raise ValidationError(self.env._("Invalid module name '%s'.", module))
        return mods

    def _validate(self):
        self.ensure_one()
        self._module_list()
        if self.wave_size < 1:
            raise ValidationError(self.env._("Wave size must be at least 1."))
        if not self.canary_tenant_ids:
            raise ValidationError(self.env._(
                "Select at least one canary tenant — it is the verification gate."))

    # ---- preparation: inventory + wave assignment ------------------------

    def _ready_tenants(self):
        return self.env['ncollection.tenant'].search([
            ('database_status', '=', 'ready'), ('database_name', '!=', False)])

    def _prepare(self):
        """Build one line per ready tenant: canary tenants in wave 0, the rest
        chunked into rolling waves of ``wave_size`` (waves 1..N)."""
        self.ensure_one()
        if self.line_ids:
            return
        self._validate()
        ready = self._ready_tenants()
        canary = self.canary_tenant_ids & ready
        if not canary:
            raise ValidationError(self.env._(
                "None of the selected canary tenants are 'ready'."))
        others = ready - canary
        Line = self.env['ncollection.fleet.migration.line']
        for tenant in canary:
            Line.create({'migration_id': self.id, 'tenant_id': tenant.id, 'wave': 0})
        for index, tenant in enumerate(others):
            Line.create({
                'migration_id': self.id, 'tenant_id': tenant.id,
                'wave': 1 + index // self.wave_size})
        self._log(self.env._(
            "Prepared: %(c)s canary + %(o)s fleet tenant(s).",
            c=len(canary), o=len(others)))

    # ---- wave helpers ----------------------------------------------------

    def _wave_lines(self, wave):
        return self.line_ids.filtered(lambda line: line.wave == wave).sorted('id')

    def _wave_terminal(self, wave):
        lines = self._wave_lines(wave)
        return bool(lines) and all(line.state in _TERMINAL_STATES for line in lines)

    def _canary_passed(self):
        canary = self._wave_lines(0)
        return bool(canary) and all(line.state in _PASS_STATES for line in canary)

    def _max_wave(self):
        return max(self.line_ids.mapped('wave'), default=0)

    # ---- synchronous run (manual button / tests) -------------------------

    def _final_state(self):
        """'failed' if any tenant is still in a failed state at completion,
        else 'done' (a headline signal — per-line counts carry the detail)."""
        self.ensure_one()
        return 'failed' if self.line_ids.filtered(
            lambda line: line.state == 'failed') else 'done'

    def action_run_sync(self):
        """Run the whole migration inline: canary wave → gate → rolling waves.
        Used by the manual button and the tests; production uses action_start."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(self.env._("This migration has already been started."))
        self._prepare()
        self.write({'state': 'canary', 'current_wave': 0})
        for line in self._wave_lines(0):
            line.run_line()
        if not self._canary_passed():
            self.write({'state': 'canary_failed'})
            self._log(self.env._("Canary failed — fleet rollout halted."), post=True)
            return False
        self.write({'state': 'rolling'})
        for wave in range(1, self._max_wave() + 1):
            self.current_wave = wave
            for line in self._wave_lines(wave):
                line.run_line()
        final = self._final_state()
        self.write({'state': final})
        self._log(self.env._("Fleet migration complete (%s).", final), post=True)
        return final == 'done'

    # ---- asynchronous run (production, via queue_job) --------------------

    def action_start(self):
        """Kick off off the HTTP workers: enqueue the canary wave; _cron_advance
        drives the gate and the subsequent waves."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(self.env._("This migration has already been started."))
        self._prepare()
        self.write({'state': 'canary', 'current_wave': 0})
        self._enqueue_wave(0)
        return True

    def _enqueue_wave(self, wave):
        for line in self._wave_lines(wave):
            line.with_delay(
                channel=FLEET_CHANNEL,
                description="Fleet upgrade %s" % line.database_name,
                identity_key='nc-fleet-line-%s' % line.id,
            ).run_line()

    @api.model
    def _cron_advance(self):
        """Advance in-flight migrations whose current wave is fully terminal."""
        for migration in self.search([('state', 'in', ('canary', 'rolling'))]):
            migration._advance()
        return True

    def _advance(self):
        self.ensure_one()
        # Serialise concurrent advances of this migration (the cron singleton +
        # a manual call could still race this read-check-write, which gates a
        # destructive drop+restore) — take an explicit row lock.
        self.env.cr.execute(
            "SELECT id FROM ncollection_fleet_migration WHERE id = %s FOR UPDATE",
            (self.id,))
        if self.state == 'canary':
            if not self._wave_terminal(0):
                return
            if not self._canary_passed():
                self.write({'state': 'canary_failed'})
                self._log(self.env._("Canary failed — fleet rollout halted."), post=True)
                return
            self.write({'state': 'rolling', 'current_wave': 1})
            self._enqueue_wave(1)
        elif self.state == 'rolling':
            if not self._wave_terminal(self.current_wave):
                return
            nxt = self.current_wave + 1
            if self._wave_lines(nxt):
                self.write({'current_wave': nxt})
                self._enqueue_wave(nxt)
            else:
                final = self._final_state()
                self.write({'state': final})
                self._log(self.env._("Fleet migration complete (%s).", final), post=True)

    # ---- cancel + audit --------------------------------------------------

    def action_cancel(self):
        for migration in self:
            if migration.state in ('done', 'failed', 'cancelled'):
                continue
            migration.write({'state': 'cancelled'})
            migration._log(
                self.env._("Migration cancelled by %s.", self.env.user.name), post=True)
        return True

    def _log(self, message, post=False):
        """Append to the run's audit log (and optionally the chatter). Every
        fleet operation is recorded here (acceptance: audit log)."""
        self.ensure_one()
        entry = "%s  %s" % (fields.Datetime.now(), message)
        self.log = (self.log + "\n" + entry) if self.log else entry
        if post:
            self.message_post(body=message)
