# -*- coding: utf-8 -*-
"""Which databases the TENANT e-invoicing runner serves, and the patch that makes OCA's queue_job runner honour it.

Why: OCA's jobrunner serves ``config['db_name']`` or every database on the server (``list_dbs``), and only discovers
databases at start (its own TODO). The platform's ``provisioning-runner`` is pinned to the platform database with
``-d``, so nothing ran ``einvoice`` jobs in tenant databases (docs/markdown/UAE_EINVOICING.md §9a).

What: ONE shared runner (``einvoice-runner``, docker-compose.einvoice-runner.yml) with this module in ``--load`` and
``NC_EINVOICE_RUNNER=1``. Without that variable nothing here changes anything, so the platform runner and every HTTP
worker keep OCA's behaviour exactly. With it, the runner:

* serves a database only if it is ELIGIBLE (:func:`decide`): a TENANT (never the platform: the release gate's own
  role decision, which keys on ``ncollection_saas``), READY by the release gate (a database being provisioned,
  upgraded, held or behind its code runs nothing), with this module AND queue_job installed, and whose workspace is not
  suspended or expired (the status the platform pushes);
* re-reads the eligible set every ``REFRESH_SECONDS`` and adds / drops databases in place, so a new tenant is served,
  and a suspended or deleted one stops being served, without a restart.

Isolation: a job is executed by queue_job's own ``/queue_job/runjob?db=<db>`` on this container's HTTP server, inside
that database's registry and transaction; a provider credential is decrypted per database, per company, per provider
(models/secret.py). Nothing here reads or writes business data; the probe reads module rows, the release-gate state and
the workspace status only.
"""

import logging
import os
import time

_logger = logging.getLogger(__name__)

ENV_FLAG = 'NC_EINVOICE_RUNNER'
REFRESH_SECONDS = 60
MODULE = 'ncollection_uae_einvoicing'
BLOCKED_STATUSES = frozenset({'suspended', 'expired', 'cancelled', 'canceled', 'terminated', 'deleted'})

# reasons, so the runner's log says why a database is not served (names never leave this process)
OK = 'eligible'


def decide(facts):
    """``(eligible, reason)`` from the facts of one database. Pure."""
    if facts is None:
        return False, 'unreachable'
    if facts.get('role') != 'tenant':
        return False, 'not a tenant database'
    if facts.get('gate_state') != 'ready':
        return False, 'release gate: %s' % (facts.get('gate_state') or 'unknown')
    installed = facts.get('installed') or set()
    if MODULE not in installed or 'queue_job' not in installed:
        return False, 'e-invoicing not installed'
    if (facts.get('subscription_status') or '').strip().lower() in BLOCKED_STATUSES:
        return False, 'workspace %s' % facts['subscription_status']
    return True, OK


def probe(db_name):
    """The facts :func:`decide` needs, read with a short-lived cursor. None when the database cannot be read."""
    from odoo import sql_db
    from odoo.addons.ncollection_core import release_gate
    try:
        with sql_db.db_connect(db_name).cursor() as cr:
            gate = release_gate.workspace_state(db_name, cr)
            cr.execute("SELECT name FROM ir_module_module WHERE state = 'installed' AND name IN %s",
                       ((MODULE, 'queue_job'),))
            installed = {row[0] for row in cr.fetchall()}
            status = None
            cr.execute("SELECT to_regclass('ncollection_workspace_config')")
            if cr.fetchone()[0]:
                cr.execute('SELECT subscription_status FROM ncollection_workspace_config ORDER BY id LIMIT 1')
                row = cr.fetchone()
                status = row and row[0]
            return {'role': gate.get('role'), 'gate_state': gate.get('state'), 'installed': installed,
                    'subscription_status': status}
    except Exception:  # noqa: BLE001 - an unreadable database is simply not served this round
        _logger.info('e-invoicing runner: a database could not be probed; it is not served this round')
        return None


def eligible_databases(candidates, probe_fn=probe):
    """The sorted eligible subset of ``candidates``."""
    chosen = []
    for name in candidates:
        ok, reason = decide(probe_fn(name))
        if ok:
            chosen.append(name)
        else:
            _logger.debug('e-invoicing runner skips a database: %s', reason)
    return sorted(chosen)


def enabled():
    return os.environ.get(ENV_FLAG) == '1'


def install(runner_cls=None, clock=time.monotonic):
    """Patch OCA's runner class in this process. Idempotent; a no-op unless :func:`enabled`."""
    if not enabled():
        return False
    if runner_cls is None:
        from odoo.addons.queue_job.jobrunner.runner import QueueJobRunner as runner_cls
    if getattr(runner_cls, '_nc_einvoice_scoped', False):
        return True
    from odoo.addons.queue_job.jobrunner import runner as runner_mod
    original_process = runner_cls.process_notifications

    def get_db_names(self):
        from odoo.service import db as service_db
        return eligible_databases(service_db.list_dbs(True))

    def refresh_scope(self):
        try:
            wanted = set(self.get_db_names())
        except Exception:  # noqa: BLE001 - the server or its database list is unavailable: keep serving what we serve
            _logger.warning('e-invoicing runner: the eligible set could not be read; the current scope is kept')
            return
        current = set(self.db_by_name)
        for db_name in sorted(current - wanted):
            db = self.db_by_name.pop(db_name)
            self.channel_manager.remove_db(db_name)
            try:
                db.close()
            except Exception:  # noqa: BLE001
                _logger.warning('e-invoicing runner: closing a dropped database failed', exc_info=True)
            _logger.info('e-invoicing runner: a database left the eligible set')
        for db_name in sorted(wanted - current):
            # a database can vanish or still be half-provisioned between the probe and here: it is skipped this
            # round (and retried at the next refresh), never allowed to take the runner loop down
            db = None
            try:
                db = runner_mod.Database(db_name)
                if not db.has_queue_job:
                    db.close()
                    continue
                with db.select_jobs('state in %s', (runner_mod.NOT_DONE,)) as cr:
                    pending = list(cr)
            except Exception:  # noqa: BLE001
                _logger.warning('e-invoicing runner: a database could not be joined; retried at the next refresh')
                if db is not None:
                    try:
                        db.close()
                    except Exception:  # noqa: BLE001
                        pass
                continue
            self.db_by_name[db_name] = db
            for job_data in pending:
                self.channel_manager.notify(db_name, *job_data)
            _logger.info('e-invoicing runner: a database joined the eligible set')

    def process_notifications(self):
        now = clock()
        if now - getattr(self, '_nc_scope_checked', 0) >= REFRESH_SECONDS:
            self._nc_scope_checked = now
            self.refresh_scope()
        return original_process(self)

    runner_cls.get_db_names = get_db_names
    runner_cls.refresh_scope = refresh_scope
    runner_cls.process_notifications = process_notifications
    runner_cls._nc_einvoice_scoped = True
    _logger.info('e-invoicing runner: queue_job runner scoped to eligible tenant databases')
    return True
