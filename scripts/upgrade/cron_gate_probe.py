# -*- coding: utf-8 -*-
"""The cron runner's own entry into each database, against REAL databases, for the upgrade suite (verify_upgrade.sh
arm 9). Run INSIDE the odoo container (stdin), with the odoo package:

    python3 - <odoo option>... -- <db> [<db>...]

It boots the way a serving process does - the configuration file, then the server-wide modules, whose
``ncollection_core`` ``post_load`` installs the release gate - and then, for each database IN ORDER and in this one
process, makes the exact call both Odoo cron runners make (``ThreadedServer.cron_thread`` / ``WorkerCron``):
``IrCron._process_jobs(db)``. No fake, no patch: what runs is Odoo's cron code behind the gate.

The suite gives each fixture an active PROBE CRON (``PROBE_CRON``, not due) whose code sets ``PROBE_KEY``. Right
before the call this process makes it due and clears the key, with plain SQL on its own connection (no registry), so
the shared dev server - which polls every database too - has no window to run it first. After the call it reads the
key back and pushes the cron out again. Prints per database:

    CRON db=<db> gate=<state> registry_before=<0|1> registry_after=<0|1> ran=<0|1|->

``registry_after=1``: this process loaded that database's registry (only a job does: ``_process_jobs_loop``).
``ran=1``: the probe cron executed. ``-``: the database has no cron table (being created). Then one line first:

    CRON_GATE installed=<0|1> cron_seam=<problem or ->
"""
import re
import sys

# It runs a database's REAL due crons: only the upgrade suite's own fixtures, never a real database.
_FIXTURES = re.compile(r'upgr(green|red|core|drift|cronnew)')
PROBE_CRON = 'NC cron gate probe'
PROBE_KEY = 'ncollection.cronprobe.ran'


def _sql(db, statement, params=()):
    from odoo import sql_db  # noqa: PLC0415
    with sql_db.db_connect(db).cursor() as cr:      # commits on a clean exit
        cr.execute("SELECT to_regclass('ir_cron') IS NOT NULL AND to_regclass('ir_config_parameter') IS NOT NULL")
        if not cr.fetchone()[0]:
            return None
        cr.execute(statement, params)
        return cr.fetchone() if cr.description else True


def main(argv):
    split = argv.index('--')
    options, databases = argv[:split], argv[split + 1:]
    refused = [db for db in databases if not _FIXTURES.fullmatch(db)]
    if refused:
        print('REFUSING: not an upgrade-suite fixture: %s' % ', '.join(refused))
        sys.exit(2)
    from odoo.tools import config  # noqa: PLC0415
    config.parse_config(options + ['--max-cron-threads=0', '--log-level=warn'])
    from odoo.service import server  # noqa: PLC0415
    server.load_server_wide_modules()
    from odoo.addons.base.models.ir_cron import IrCron  # noqa: PLC0415
    from odoo.addons.ncollection_core import release_gate  # noqa: PLC0415
    from odoo.modules.registry import Registry  # noqa: PLC0415
    print('CRON_GATE installed=%d cron_seam=%s' % (
        release_gate.cron_installed(), release_gate.cron_seam_problem() or '-'))
    for db in databases:
        before = int(db in Registry.registries)
        state = release_gate.workspace_state(db)['state']
        _sql(db, "DELETE FROM ir_config_parameter WHERE key = %s", [PROBE_KEY])
        _sql(db, "UPDATE ir_cron SET nextcall = (now() AT TIME ZONE 'utc') - interval '1 minute' WHERE cron_name = %s",
             [PROBE_CRON])
        IrCron._process_jobs(db)
        ran = _sql(db, "SELECT count(*) FROM ir_config_parameter WHERE key = %s", [PROBE_KEY])
        _sql(db, "UPDATE ir_cron SET nextcall = '2999-01-01' WHERE cron_name = %s", [PROBE_CRON])
        print('CRON db=%s gate=%s registry_before=%d registry_after=%d ran=%s'
              % (db, state, before, int(db in Registry.registries), '-' if ran is None else int(ran[0] > 0)))
        sys.stdout.flush()


if __name__ == '__main__':
    main(sys.argv[1:])
