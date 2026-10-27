# -*- coding: utf-8 -*-
"""Release compatibility gate: no request reaches the application of a database the running code is ahead of.

THE FAILURE THIS ENDS (REGRESSIONS.md R-037, reproduced again on a copy of ``ncollection`` for this change)
-----------------------------------------------------------------------------------------------------------
Odoo builds every SELECT from the fields the CODE declares and changes the schema only on ``-u``; it never compares
the version a database is recorded at with the version on disk. Deploy newer code and the registry of an un-upgraded
database still loads, ``/web/health`` still answers 200, and the first page that reads a changed model dies inside a
controller: ``GET /web/login`` -> QWeb login layout -> ``nc_brand()`` -> ``SELECT ... browser_title ...`` ->
``psycopg2.errors.UndefinedColumn`` -> werkzeug's "Internal Server Error ... the server is overloaded". Detection
(the registry-load log, the preflight) only told an operator afterwards.

WHERE THE GATE SITS, AND WHY THERE
----------------------------------
Every request that names a database goes through ``odoo.http.Request._serve_db``, which is the first place that
loads the registry (``Registry(self.db)``) and then dispatches to a controller. A check inside a model, a controller
or ``ir.http`` runs only after that, i.e. after the point a stale database can already fail. So this module wraps
``_serve_db`` (installed once per process by ``ncollection_core``'s ``post_load``; the module is a server-wide module
so the wrap exists before the first request of any database) and decides BEFORE the original runs:

    READY                -> the original ``_serve_db``, unchanged
    anything else        -> HTTP 503 maintenance response (System Branding, no traceback, no internals),
                            no registry is loaded, no controller runs, nothing is upgraded

nginx stays what it is (coarse host routing, one upstream); it holds no per-tenant state. Odoo's core files are not
modified: this is one wrapper around one method, refusing nothing it does not have to (static files and requests
without a database are untouched). ``/web/health`` is liveness, not the application: it is always answered by Odoo's
own NO-DATABASE dispatch (``Request._serve_nodb``), never through the registry - a server holding ONE database
(monodb) would otherwise resolve every request, ``/web/health`` included, to that database and load its registry
while it is behind the code.

WHAT "READY" MEANS (deterministic, the same comparison the release preflight makes)
-----------------------------------------------------------------------------------
* the RELEASE CONTRACT is the code this process serves: ``{module: version}`` of every module under the configured
  NCollection / OCA addons roots (not Odoo core), read once at start, with a fingerprint (sha256 of the sorted list);
* the DATABASE is read with plain SQL (no registry): ``ir_module_module`` and the hold parameter;
* READY  <=> the module table exists, no module is mid-install/upgrade/removal (``to %``), no installed module of the
  release universe is recorded at a version older than its code, no installed ``ncollection_*`` module is missing from
  the code, no schema gap was found when the registry loaded (``schema_drift``), and no release HOLD is set;
* and no ``ncollection_*`` module the code AUTO-INSTALLS is absent: one whose triggers (``auto_install`` list, or all
  its ``depends``) are installed. Odoo auto-installs only while a trigger is itself being installed, so a module added
  to the code later never reaches a database created before it - an old tenant stayed READY without the Sales /
  Purchase document modules a new tenant gets. Such a module is listed in ``absent``: the exact ``-i`` list.

States (also what the platform mirrors on ``ncollection.tenant``): READY, UPGRADE_REQUIRED (modules behind),
UPGRADING (a module is mid-upgrade, or the release tooling holds it while it upgrades and validates), UPGRADE_FAILED
(held after a failed upgrade), BLOCKED (held by an operator, a module not deployed, or a schema gap), PROVISIONING
(held by the provisioning engine until it has validated the new database).

HOLDS (the explicit half of the state)
--------------------------------------
``ir_config_parameter['ncollection.release.hold']`` = ``provisioning`` | ``upgrading`` | ``upgrade_failed`` |
``blocked`` [``:reason``]. Written only (a) by ``post_init_hook`` when the provisioning engine installs a database
with ``NC_RELEASE_HOLD=provisioning`` in the environment, so a new tenant is held from its first commit, and (b) by
the release endpoints below, which the PLATFORM calls with the tenant's own config-sync bearer. A hold is cleared
(``/open``) only when the database is otherwise compatible. While a database is compatible and held for provisioning
or upgrading, requests that carry that bearer pass, so the platform can validate (HTTP smoke, Company Entry) before
anybody else gets in.

THE PLATFORM'S VIEW (no tenant internals are shown to tenant users)
------------------------------------------------------------------
``GET /ncollection/release/status`` answers the full state (role, the exact modules to upgrade with both versions,
missing modules, fingerprints, hold, last successful release, last failure, when it was read) to an authenticated
caller only; anyone else gets 404. It is answered here, before the registry, with plain SQL, so it works for exactly
the databases whose application cannot run. Two callers, one key derivation (``ncollection_saas`` ``config_sync``):

* a TENANT database: the platform's per-tenant config-sync bearer, checked against the tenant's own stored key hash
  (``res_users_apikeys`` of the config-sync service account, Odoo's own ``KEY_CRYPT_CONTEXT``);
* the PLATFORM database (role ``platform``: ``ncollection_saas`` is installed in it - read from its own module table,
  never a configured name): it has no service account, so the bearer is the same HMAC derivation of the platform
  master ``NC_CONFIG_SYNC_KEY`` for this database's own name, compared in constant time. The master lives only in
  the environment of the server that serves the platform (never in git, never in a database), so only an operator
  holding it - ``scripts/upgrade/release_status.py`` inside that container - can ask.

The platform database is gated exactly like a tenant (no bypass, no "trusted" registry load): the role changes who
may ask about it, never whether stale code may serve it.

THE CACHE CANNOT ALLOW STALE TRAFFIC
------------------------------------
Every gated request reads one token from the database (an md5 of every module row's name/state/version, the latest
module write and the hold), in every worker process. The evaluation is cached against that token, never against time:
a hold, a module entering ``to upgrade`` or a version written by ``-u`` changes the token, so the very next request of
every process re-evaluates. A cached READY can never outlive a release transition.

SCHEMA ERRORS DEGRADE TO THE 503, NOTHING ELSE DOES
---------------------------------------------------
A schema gap the versions cannot show (a column lost at an unchanged version) surfaces as ``UndefinedColumn`` /
``UndefinedTable`` raised out of the original ``_serve_db`` - directly, or as the explicit cause of Odoo's own
``RegistryError`` / ``QWebError`` wrappers. Exactly those are answered with the 503 and the database is held BLOCKED
(in this process, until its module rows change); every other exception propagates untouched to Odoo's handling.

BACKGROUND WORK: THE CRON SEAM (the same decision, before the cron thread loads a registry)
------------------------------------------------------------------------------------------
A serving process with no ``-d`` runs the crons of EVERY database on its server (``cron_database_list()`` falls back
to ``list_dbs(True)``; DESIGN_CRON_AND_QUEUE_TOPOLOGY.md section 5.1), and no request is involved: the HTTP gate never
sees them. Both cron runners (``ThreadedServer.cron_thread`` and the prefork ``WorkerCron``) enter a database through
``IrCron._process_jobs(db_name)``, which loads the registry only inside ``_process_jobs_loop``, after
``_acquire_one_job``. So two static methods of that class are wrapped, from the same ``post_load``:

    _process_jobs(db_name)       READY -> the original; anything else -> returns at once: no cursor beyond the state
                                 read, no registry, no job (PROVISIONING included: a database with no module table)
    _acquire_one_job(cr, job_id) the same decision again before EACH job of a poll, so a hold or an ``-u`` that starts
                                 while one poll is still running its jobs stops the next one before its registry loads

Only READY runs crons - no hold is passable here: nothing validates a release through a cron. A state that cannot
be read runs nothing (fail closed). Rows merely left ``to %`` by a killed install / ``-u`` still reach Odoo's own
``_check_modules_state`` (skip, or its own reset after MAX_FAIL_TIME), never a job until READY. The manual "Run"
button (``include_not_ready=True``) is an HTTP request and was
already gated. A skipped database is logged once per change of its decision, never once per poll.

The third way into a registry without a request is the PRELOAD of a serving process started with ``-d`` (the
provisioning runner runs ``-d ncollection``): ``odoo.service.server.preload_registries`` loads it at boot and runs
its ``_register_hook``. Wrapped the same way: a database that is not READY is left unloaded; a process that
installs, upgrades or tests (``-i`` / ``-u`` / ``--test-enable``) - the release step itself - is never gated.

ALL OF THIS EXISTS ONLY IN A PROCESS THAT LOADS ``ncollection_core`` SERVER-WIDE: ``server_wide_modules`` in
odoo.conf / odoo.prod.conf, and EVERY ``--load=`` on a command line (``--load`` REPLACES that list). The
provisioning runner ran ``--load=base,web,queue_job``: the one process that runs the platform's crons had no
gate at all. ``scripts/ci/invariants.py`` now fails a ``--load=`` without it.

Never: an upgrade from a request (nothing here runs ``-u``), a traceback to a tenant user, a hidden error (every
decision change is logged with its cause), a schema edit (the only writes are the three release parameters).
"""
import ast
import base64
import hashlib
import hmac
import html
import inspect
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

import psycopg2.errors

import odoo
from odoo import http, sql_db
from odoo.tools import SQL, config, parse_version

_logger = logging.getLogger(__name__)

READY = 'ready'
UPGRADE_REQUIRED = 'upgrade_required'
UPGRADING = 'upgrading'
UPGRADE_FAILED = 'upgrade_failed'
BLOCKED = 'blocked'
PROVISIONING = 'provisioning'
STATES = (READY, UPGRADE_REQUIRED, UPGRADING, UPGRADE_FAILED, BLOCKED, PROVISIONING)

HOLD_KEY = 'ncollection.release.hold'
LAST_OK_KEY = 'ncollection.release.last_ok'          # "<utc iso> release=<fingerprint>", written by /open
LAST_ERROR_KEY = 'ncollection.release.last_error'    # "<utc iso> <reason>", written by an upgrade_failed hold
HOLD_ENV = 'NC_RELEASE_HOLD'
PLATFORM, TENANT = 'platform', 'tenant'
PLATFORM_MODULE = 'ncollection_saas'                 # the control plane is the database that runs provisioning
MASTER_ENV = 'NC_CONFIG_SYNC_KEY'                    # ncollection_saas/models/config_sync.py _SYNC_KEY_ENV
_KDF_LABEL = b'nc-config-sync:'                      # ncollection_saas/models/config_sync.py (pinned by a saas test)
HOLD_STATES = {'provisioning': PROVISIONING, 'upgrading': UPGRADING, 'upgrade_failed': UPGRADE_FAILED,
               'blocked': BLOCKED}
# Holds the platform's validation may pass (only while the database is otherwise compatible).
_PASSABLE_HOLDS = ('provisioning', 'upgrading')

STATUS_PATH = '/ncollection/release/status'
HOLD_PATH = '/ncollection/release/hold'
OPEN_PATH = '/ncollection/release/open'
_RELEASE_PATHS = (STATUS_PATH, HOLD_PATH, OPEN_PATH)
_UNGATED_PATHS = frozenset({'/web/health'})          # server liveness, not the application
STATE_HEADER = 'X-NC-Workspace-State'
SERVICE_LOGIN = 'config-sync@ncollection.internal'   # ncollection_core/models/config_sync_key.py
_REASON_MAX = 200
_LOGO_MAX_BYTES = 256 * 1024
_LOGO_MIMES = ('image/png', 'image/jpeg', 'image/webp')     # what System Branding accepts for a logo
_HEX_COLOR = re.compile(r'#[0-9A-Fa-f]{6}')
_JSON_PREFIXES = ('/json/', '/jsonrpc', '/xmlrpc', '/web/dataset/', '/web/session/', '/api/', '/websocket',
                  '/longpolling', '/web/action/', '/mail/', '/bus/')

_lock = threading.Lock()
_release = None
_cache = {}            # db -> (token, result): reused only while the database's token is unchanged
_schema_gaps = {}      # db -> token at which this process found missing tables / columns (registry load or a request)
_last_logged = {}      # db -> (state, required), so a decision is logged once per change, not once per request
_cron_logged = {}      # the same, for the cron seam: once per change, not once per poll


# ---------------------------------------------------------------------------------------------- the release contract
def _code_roots():
    """The configured addons paths that are NOT Odoo's own (its core addons ship with the odoo package)."""
    core = os.path.realpath(os.path.dirname(odoo.release.__file__))   # ``odoo`` is a namespace package: no __file__
    configured = config.get('addons_path') or ''
    roots = []
    for path in (configured.split(',') if isinstance(configured, str) else configured):
        path = path.strip()
        if path and os.path.isdir(path) and not os.path.realpath(path).startswith(core + os.sep):
            roots.append(path)
    return roots


def _full_version(version):
    """A manifest version as Odoo records it (``19.0.`` prefixed), so it compares with ``latest_version``."""
    version = str(version or '')
    major = odoo.release.major_version
    if version == major or version.startswith(major + '.'):
        return version
    return '%s.%s' % (major, version)


def fingerprint(versions):
    return hashlib.sha256('\n'.join('%s=%s' % item for item in sorted(versions.items())).encode()).hexdigest()


def release_manifest(refresh=False):
    """``{'modules': {name: version}, 'fingerprint': str}`` of the code this process serves. Read once."""
    global _release
    with _lock:
        if _release is None or refresh:
            versions, auto = {}, {}
            for root in _code_roots():
                for name in sorted(os.listdir(root)):
                    path = os.path.join(root, name, '__manifest__.py')
                    if not os.path.isfile(path):
                        continue
                    try:
                        with open(path, encoding='utf-8') as handle:
                            data = ast.literal_eval(handle.read())
                    except (OSError, ValueError, SyntaxError):
                        continue
                    if isinstance(data, dict) and data.get('version') and data.get('installable', True):
                        versions.setdefault(name, _full_version(data['version']))
                        if name.startswith('ncollection_') and data.get('auto_install'):
                            trigger = data['auto_install']
                            auto.setdefault(name, tuple(trigger if isinstance(trigger, (list, tuple))
                                                        else data.get('depends') or ()))
            _release = {'modules': versions, 'fingerprint': fingerprint(versions), 'auto_install': auto}
        return _release


# ---------------------------------------------------------------------------------------------- the database's state
def evaluate(rows, hold_value, schema_gap, release):
    """The state of a database from what was read: ``rows`` = [(module, state, latest_version)] or None when the
    module table does not exist yet. Pure, so the decision is testable without a server."""
    if rows is None:
        return {'state': PROVISIONING, 'role': TENANT, 'compatible': False, 'passable': False, 'behind': [],
                'required': [], 'absent': [], 'missing': [], 'transitional': [], 'schema_gap': False, 'hold': '',
                'hold_reason': '', 'db_fingerprint': '', 'release_fingerprint': release['fingerprint']}
    code = release['modules']
    role = PLATFORM if any(name == PLATFORM_MODULE and state != 'to remove' for name, state, _v in rows) else TENANT
    transitional = sorted(name for name, state, _v in rows if (state or '').startswith('to '))
    installed = {name: version for name, state, version in rows if state == 'installed'}
    behind = [(name, installed[name] or '', code[name]) for name in sorted(installed)
              if name in code and parse_version(code[name]) > parse_version(installed[name] or '0')]
    missing = sorted(name for name in installed if name.startswith('ncollection_') and name not in code)
    absent = _absent(release.get('auto_install') or {}, set(installed), set(transitional))
    hold, _sep, reason = (hold_value or '').partition(':')
    hold = hold.strip()
    compatible = not (transitional or behind or absent or missing or schema_gap)
    if transitional:
        state = UPGRADING
    elif hold in HOLD_STATES:
        state = HOLD_STATES[hold]
    elif behind or absent:
        state = UPGRADE_REQUIRED
    elif missing or schema_gap:
        state = BLOCKED
    else:
        state = READY
    return {
        'state': state, 'role': role, 'compatible': compatible, 'passable': compatible and hold in _PASSABLE_HOLDS,
        # ``required`` is the exact ``-u`` list: every module behind, together, never one of a set that moves together.
        # ``absent``: the exact ``-i`` list (auto-installed by the code, not in this database), applied in the same run
        'behind': behind, 'required': [row[0] for row in behind], 'absent': absent, 'missing': missing,
        'transitional': transitional,
        'schema_gap': bool(schema_gap), 'hold': hold if hold in HOLD_STATES else '',
        'hold_reason': reason.strip()[:_REASON_MAX],
        'db_fingerprint': fingerprint({name: v for name, v in installed.items() if name in code}),
        'release_fingerprint': release['fingerprint'],
    }


def _absent(auto, installed, transitional):
    """The auto-install modules of the code this database should have and does not, to a fixed point (installing one
    can trigger another). A module already mid-install is transitional, not absent."""
    have, absent = set(installed), []
    changed = True
    while changed:
        changed = False
        for name in sorted(auto):
            if name not in have and name not in transitional and all(dep in have for dep in auto[name]):
                have.add(name)
                absent.append(name)
                changed = True
    return sorted(absent)


_TOKEN_SQL = """
    SELECT md5(coalesce(string_agg(name || ':' || state || ':' || coalesce(latest_version, ''), ',' ORDER BY name),
                        '')),
           coalesce(max(write_date)::text, ''),
           (SELECT value FROM ir_config_parameter WHERE key = %s)
      FROM ir_module_module WHERE state NOT IN ('uninstalled', 'uninstallable')
"""


def _tables(cr):
    cr.execute("SELECT to_regclass('ir_module_module') IS NOT NULL, to_regclass('ir_config_parameter') IS NOT NULL")
    return cr.fetchone()


def state_token(cr):
    """What every gated request reads: changes with any module row (name, state, version, write) and the hold. None
    while the database has no module table yet (a database being created: never cached)."""
    has_modules, has_params = _tables(cr)
    if not (has_modules and has_params):
        return None
    cr.execute(_TOKEN_SQL, [HOLD_KEY])
    return tuple(cr.fetchone())


def _schema_gap(db, token):
    return token is not None and _schema_gaps.get(db) == token


def read_state(cr, db, token=None):
    """Read the database with plain SQL (no registry) and evaluate it against this process's release. Also what the
    status call answers: the last successful release, the last failure and when this was read."""
    has_modules, has_params = _tables(cr)
    rows = None
    params = {}
    if has_modules:
        cr.execute("SELECT name, state, latest_version FROM ir_module_module "
                   "WHERE state NOT IN ('uninstalled', 'uninstallable')")
        rows = cr.fetchall()
    if has_params:
        cr.execute("SELECT key, value FROM ir_config_parameter WHERE key IN %s",
                   [(HOLD_KEY, LAST_OK_KEY, LAST_ERROR_KEY)])
        params = dict(cr.fetchall())
    if token is None and has_modules and has_params:
        token = state_token(cr)
    result = evaluate(rows, params.get(HOLD_KEY), _schema_gap(db, token), release_manifest())
    result.update(last_ok=params.get(LAST_OK_KEY) or '', last_error=params.get(LAST_ERROR_KEY) or '',
                  checked_at=_now())
    return result


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def workspace_state(db, cr=None):
    """The gate's decision for ``db``. Reads the token on EVERY call; re-evaluates whenever it changed."""
    if cr is None:
        with sql_db.db_connect(db).cursor() as own:
            return workspace_state(db, own)
    token = state_token(cr)
    hit = _cache.get(db)
    if token is not None and hit and hit[0] == token:
        return hit[1]
    result = read_state(cr, db, token)
    if token is not None:
        _cache[db] = (token, result)
    else:
        _cache.pop(db, None)
    return result


def invalidate(db):
    _cache.pop(db, None)


def note_schema_report(db, report, cr=None):
    """Called by the registry-load drift check: a database whose schema lacks what the code declares is not READY,
    for as long as its module rows stay as they were when the gap was found (an ``-u`` changes them)."""
    if report.get('missing_tables') or report.get('missing_columns'):
        token = state_token(cr) if cr is not None else None
        if token is not None:
            _schema_gaps[db] = token
    else:
        _schema_gaps.pop(db, None)
    invalidate(db)


def _set_param(cr, key, value):
    cr.execute("""
        INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date)
        VALUES (%s, %s, 1, now() AT TIME ZONE 'utc', 1, now() AT TIME ZONE 'utc')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, write_date = EXCLUDED.write_date
    """, [key, value])


def set_hold(cr, value):
    _set_param(cr, HOLD_KEY, value)


def clear_hold(cr):
    cr.execute("DELETE FROM ir_config_parameter WHERE key = %s", [HOLD_KEY])


def record_ok(cr):
    _set_param(cr, LAST_OK_KEY, '%s release=%s' % (_now(), release_manifest()['fingerprint']))


def record_error(cr, reason):
    _set_param(cr, LAST_ERROR_KEY, '%s %s' % (_now(), clean_reason(reason) or 'upgrade failed'))


def clean_reason(reason):
    return ''.join(ch for ch in str(reason or '') if ch.isprintable())[:_REASON_MAX]


# ---------------------------------------------------------------------------------------------- the platform's bearer
def service_bearer_ok(cr, authorization):
    """The tenant's own config-sync service key (Odoo's stored hash), presented as ``Bearer <key>``."""
    from odoo.addons.base.models.res_users import INDEX_SIZE, KEY_CRYPT_CONTEXT  # noqa: PLC0415
    scheme, _sep, key = (authorization or '').partition(' ')
    key = key.strip()
    if scheme.lower() != 'bearer' or not (INDEX_SIZE <= len(key) <= 200):
        return False
    cr.execute("""
        SELECT k.key FROM res_users_apikeys k JOIN res_users u ON u.id = k.user_id
         WHERE u.login = %s AND u.active AND k.index = %s AND (k.scope IS NULL OR k.scope = 'rpc')
           AND (k.expiration_date IS NULL OR k.expiration_date >= now() AT TIME ZONE 'utc')
    """, [SERVICE_LOGIN, key[:INDEX_SIZE]])
    return any(KEY_CRYPT_CONTEXT.verify(key, stored) for (stored,) in cr.fetchall())


def platform_bearer_ok(db, role, authorization):
    """The PLATFORM database's operator bearer: the config-sync derivation of the master for this database's own
    name. Only for a database whose role is ``platform``, only when this server holds the master; constant time."""
    master = os.environ.get(MASTER_ENV) or ''
    scheme, _sep, key = (authorization or '').partition(' ')
    if role != PLATFORM or not master or scheme.lower() != 'bearer' or not key.strip():
        return False
    expected = base64.urlsafe_b64encode(
        hmac.new(master.encode(), _KDF_LABEL + db.encode(), hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected.encode(), key.strip().encode())


def caller_ok(cr, db, role, authorization):
    """Who may read or change a database's release state: its own service key, or (platform) the operator bearer."""
    return platform_bearer_ok(db, role, authorization) or service_bearer_ok(cr, authorization)


def _bearer_ok(db, result, httprequest):
    try:
        with sql_db.db_connect(db).cursor() as cr:
            return caller_ok(cr, db, result['role'], httprequest.headers.get('Authorization'))
    except Exception:  # noqa: BLE001 - an unverifiable bearer is simply not accepted
        _logger.warning("Release gate: could not verify the bearer on %s", db, exc_info=True)
        return False


# ---------------------------------------------------------------------------------------------- responses
def _system_identity(db):
    """The system's name, colours and logo, read with plain SQL from THIS database's System Branding (never a
    tenant's colours, never the company's). Every part is optional: a missing column, row or file falls back to the
    shipped defaults, because the page exists for exactly the databases whose schema may be behind."""
    from odoo.addons.ncollection_branding.models.product_branding import (  # noqa: PLC0415
        DEFAULT_NAME, DEFAULT_PRIMARY_COLOR, DEFAULT_SECONDARY_COLOR)
    identity = {'name': DEFAULT_NAME, 'title': '', 'primary': DEFAULT_PRIMARY_COLOR,
                'secondary': DEFAULT_SECONDARY_COLOR, 'logo': ''}
    try:
        with sql_db.db_connect(db).cursor() as cr:
            cr.execute("SELECT column_name FROM information_schema.columns "
                       "WHERE table_schema = current_schema() AND table_name = 'ncollection_product_branding'")
            columns = {row[0] for row in cr.fetchall()}
            wanted = [c for c in ('id', 'name', 'browser_title', 'primary_color', 'secondary_color') if c in columns]
            if 'id' in wanted:
                # Identifiers from the fixed tuple above, quoted by Odoo's own SQL builder.
                cr.execute(SQL('SELECT %s FROM ncollection_product_branding ORDER BY id LIMIT 1',
                               SQL(', ').join(SQL.identifier(column) for column in wanted)))
                row = cr.fetchone()
                values = dict(zip(wanted, row)) if row else {}
                if (values.get('name') or '').strip():
                    identity['name'] = values['name'].strip()[:120]
                identity['title'] = (values.get('browser_title') or '').strip()[:120]
                for key, column in (('primary', 'primary_color'), ('secondary', 'secondary_color')):
                    if _HEX_COLOR.fullmatch(values.get(column) or ''):
                        identity[key] = values[column]
                cr.execute("""
                    SELECT store_fname, mimetype FROM ir_attachment
                     WHERE res_model = 'ncollection.product.branding' AND res_field IN ('login_logo', 'logo')
                       AND store_fname IS NOT NULL
                     ORDER BY (res_field = 'login_logo') DESC, id LIMIT 1
                """)
                logo = cr.fetchone()
                if logo and logo[1] in _LOGO_MIMES:
                    store = os.path.realpath(config.filestore(db))
                    path = os.path.realpath(os.path.join(store, logo[0]))
                    if path.startswith(store + os.sep) and os.path.getsize(path) <= _LOGO_MAX_BYTES:
                        with open(path, 'rb') as handle:
                            encoded = base64.b64encode(handle.read()).decode()
                            identity['logo'] = 'data:%s;base64,%s' % (logo[1], encoded)
    except Exception:  # noqa: BLE001 - the page must render whatever state the database is in
        _logger.info("Release gate: system identity of %s unavailable, using the defaults", db, exc_info=True)
    return identity


_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex"/><meta name="theme-color" content="{primary}"/><title>{title}</title>
<style>
:root {{ --nc-color-primary: {primary}; --nc-color-secondary: {secondary}; }}
body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
       font-family: system-ui, -apple-system, "Segoe UI", Tahoma, sans-serif; background: #f5f7fa; color: #1f2933; }}
main {{ max-width: 34rem; margin: 1rem; padding: 2.5rem 2rem; background: #fff; border-radius: 12px;
       text-align: center;
       border-top: 6px solid var(--nc-color-primary); box-shadow: 0 8px 30px rgba(0, 0, 0, .08); }}
img {{ max-height: 64px; max-width: 220px; margin-bottom: 1rem; }}
h1 {{ font-size: 1.35rem; color: var(--nc-color-primary); margin: .5rem 0 1rem; }}
p {{ line-height: 1.6; margin: .5rem 0; }}
.ar {{ direction: rtl; margin-top: 1.75rem; padding-top: 1.25rem; border-top: 1px solid #e4e7eb; }}
.brand {{ font-weight: 600; color: var(--nc-color-secondary); }}
</style></head>
<body class="o_nc_system_surface"><main>
{logo}<div class="brand">{name}</div>
<h1>This workspace is being updated</h1>
<p>{name} is preparing this workspace for the current release. It is not available for the moment.</p>
<p>Please try again in a few minutes.</p>
<div class="ar" lang="ar"><h1>يجري تحديث مساحة العمل هذه</h1>
<p>يقوم {name} بتجهيز مساحة العمل هذه للإصدار الحالي، وهي غير متاحة في الوقت الحالي.</p>
<p>يرجى المحاولة مرة أخرى بعد بضع دقائق.</p></div>
</main></body></html>
"""


def _wants_json(httprequest):
    path = httprequest.path
    accept = httprequest.headers.get('Accept') or ''
    ctype = httprequest.headers.get('Content-Type') or ''
    return (path.startswith(_JSON_PREFIXES) or 'application/json' in ctype
            or ('application/json' in accept and 'text/html' not in accept))


def maintenance_response(db, result, httprequest):
    """HTTP 503, System Branding, no traceback, no module or database name, no claim that anything succeeded."""
    from werkzeug.wrappers import Response  # noqa: PLC0415
    headers = {'Retry-After': '120', 'Cache-Control': 'no-store', STATE_HEADER: result['state'],
               'X-Robots-Tag': 'noindex'}
    if _wants_json(httprequest):
        body = json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {
            'code': 503, 'message': 'This workspace is being updated. Please try again in a few minutes.',
            'data': {'name': 'ncollection.workspace_unavailable', 'workspace_state': result['state']}}})
        return Response(body, status=503, headers=headers, content_type='application/json; charset=utf-8')
    identity = _system_identity(db)
    name = html.escape(identity['name'])
    logo = '<img src="%s" alt="%s"/>' % (html.escape(identity['logo'], quote=True), name) if identity['logo'] else ''
    page = _PAGE.format(name=name, title=html.escape(identity['title'] or identity['name']), logo=logo,
                        primary=identity['primary'], secondary=identity['secondary'])
    return Response(page, status=503, headers=headers, content_type='text/html; charset=utf-8')


def _json(status, payload):
    from werkzeug.wrappers import Response  # noqa: PLC0415
    return Response(json.dumps(payload), status=status, content_type='application/json; charset=utf-8',
                    headers={'Cache-Control': 'no-store'})


def release_endpoint(db, httprequest):
    """The three release calls. All need an accepted bearer (``caller_ok``); anything else is a plain 404."""
    path = httprequest.path
    method = httprequest.method
    try:
        # The cursor commits when the block exits cleanly (and rolls back on an exception); the cache is invalidated
        # only AFTER that, so no request can re-cache the state from before the change.
        with sql_db.db_connect(db).cursor() as cr:
            status, payload = _release_call(cr, db, path, method, httprequest)
        if status == 200 and path in (HOLD_PATH, OPEN_PATH):
            invalidate(db)
        return _json(status, payload)
    except Exception:  # noqa: BLE001 - the platform gets a clean error, the log keeps the cause
        _logger.exception("Release gate: release endpoint %s failed on %s", path, db)
        return _json(500, {'error': 'release endpoint failed'})


def _release_call(cr, db, path, method, httprequest):
    state = read_state(cr, db)
    if not caller_ok(cr, db, state['role'], httprequest.headers.get('Authorization')):
        return 404, {'error': 'not found'}
    if path == STATUS_PATH and method == 'GET':
        return 200, state
    if path == HOLD_PATH and method == 'POST':
        try:
            payload = json.loads(httprequest.get_data(as_text=True) or '{}')
        except ValueError:
            payload = {}
        hold = (payload.get('hold') or '').strip()
        if hold not in HOLD_STATES:
            return 400, {'error': 'hold must be one of %s' % ', '.join(sorted(HOLD_STATES))}
        reason = clean_reason(payload.get('reason'))
        set_hold(cr, '%s:%s' % (hold, reason) if reason else hold)
        if hold == 'upgrade_failed':
            record_error(cr, reason)
        _logger.warning("Release gate: db=%s role=%s held as %s%s", db, state['role'], hold,
                        ' (%s)' % reason if reason else '')
        return 200, read_state(cr, db)
    if path == OPEN_PATH and method == 'POST':
        if not state['compatible']:
            _logger.warning("Release gate: db=%s role=%s refused to open: still %s, required=%s", db, state['role'],
                            state['state'].upper(), ','.join(state['required']) or '-')
            return 409, state                      # never open a database the code is ahead of
        clear_hold(cr)
        record_ok(cr)
        _logger.info("Release gate: db=%s role=%s opened for traffic, release=%s", db, state['role'],
                     state['release_fingerprint'][:12])
        return 200, read_state(cr, db)
    return 405, {'error': 'method not allowed'}


def _log_decision(db, result, path):
    """One line per change of a database's decision in this process (never per request), with what an operator needs:
    the role, the state, the exact modules to upgrade with both versions, and both fingerprints. Never a credential:
    no header, bearer or connection detail is in ``result``."""
    key = (result['state'], tuple(result['required']), result['schema_gap'], result['hold'])
    previous = _last_logged.get(db)
    if previous == key:
        return
    _last_logged[db] = key
    common = (db, result['role'], (previous[0].upper() if previous else '-'), result['state'].upper(),
              result['release_fingerprint'][:12], (result['db_fingerprint'] or '-')[:12])
    if result['state'] == READY:
        _logger.info(
            "Release gate: db=%s role=%s state=%s->%s serving normal traffic; release=%s database=%s", *common)
        return
    _logger.warning(
        "Release gate: db=%s role=%s state=%s->%s blocking normal traffic with the maintenance response (first: %s); "
        "release=%s database=%s required=%s behind=%s absent=%s missing=%s transitional=%s schema_gap=%s hold=%s%s",
        *common[:4], path, *common[4:], ','.join(result['required']) or '-',
        ','.join('%s %s<%s' % row for row in result['behind']) or '-', ','.join(result.get('absent') or []) or '-',
        ','.join(result['missing']) or '-',
        ','.join(result['transitional']) or '-', result['schema_gap'], result['hold'] or '-',
        ' (%s)' % result['hold_reason'] if result['hold_reason'] else '')


def _wrapped_schema_error(exc):
    """The ``UndefinedColumn`` / ``UndefinedTable`` behind ``exc``: itself, or the explicit ``__cause__`` of Odoo's own
    registry / QWeb wrappers (two levels at most). None for anything else: those propagate untouched."""
    from odoo.addons.base.models.ir_qweb import QWebError  # noqa: PLC0415
    for _depth in range(3):
        if isinstance(exc, (psycopg2.errors.UndefinedColumn, psycopg2.errors.UndefinedTable)):
            return exc
        if not isinstance(exc, (http.RegistryError, QWebError)):
            return None
        exc = exc.__cause__
    return None


def _schema_error_text(exc):
    """The first line of the database's message only (``column x.y does not exist``): never the statement, which can
    carry customer values."""
    diag = getattr(exc, 'diag', None)
    primary = getattr(diag, 'message_primary', None) if diag is not None else None
    return clean_reason((primary or str(exc) or type(exc).__name__).splitlines()[0])


def _serve_compatible(db, result, request, original):
    """The original ``_serve_db`` for a database the gate let through; a schema error degrades to the 503."""
    try:
        return original(request)
    except Exception as exc:
        schema = _wrapped_schema_error(exc)
        if schema is None:
            raise
        token = None
        try:
            with sql_db.db_connect(db).cursor() as cr:
                token = state_token(cr)
        except Exception:  # noqa: BLE001 - the 503 below does not depend on it
            _logger.warning("Release gate: could not read the token of %s", db, exc_info=True)
        if token is not None:
            _schema_gaps[db] = token
        invalidate(db)
        blocked = dict(result, state=BLOCKED, compatible=False, passable=False, schema_gap=True)
        _logger.error(
            "Release gate: db=%s role=%s state=%s->BLOCKED intercepted a schema error on %s (%s: %s): the schema "
            "lacks what the code declares; normal traffic gets the maintenance response until its modules change "
            "(upgrade exactly the affected modules, never -u all); release=%s database=%s", db, result['role'],
            result['state'].upper(), request.httprequest.path, type(schema).__name__, _schema_error_text(schema),
            result['release_fingerprint'][:12], (result['db_fingerprint'] or '-')[:12], exc_info=exc)
        _last_logged[db] = (BLOCKED, tuple(result['required']), True, result['hold'])
        return maintenance_response(db, blocked, request.httprequest)


def gated_serve_db(request, original):
    if config.get('test_enable') or not request.db:
        return original(request)
    return _gate(request, original)


def _gate(request, original):
    """The decision, before the original ``_serve_db`` (hence before the registry and any controller)."""
    db = request.db
    httprequest = request.httprequest
    path = httprequest.path
    if path in _RELEASE_PATHS:
        return release_endpoint(db, httprequest)
    if path in _UNGATED_PATHS:
        # Odoo's own way to serve a request without its database (Application.__call__ on a RegistryError): the
        # dispatcher picks the registry by ``request.db``, so it must be None. /web/health saves no session.
        request.db = None
        return request._serve_nodb()
    try:
        result = workspace_state(db)
    except Exception:  # noqa: BLE001 - unreachable database: Odoo's own handling decides, as before
        # Fail OPEN on purpose (unlike the cron seam): a database whose module table cannot even be read cannot
        # load a registry either, so Odoo's own error is what this request gets - exactly as without the gate.
        if _last_logged.get(db) != 'unreadable':
            _last_logged[db] = 'unreadable'
            _logger.warning("Release gate: could not read the state of %s", db, exc_info=True)
        return original(request)
    _log_decision(db, result, path)
    if result['state'] == READY:
        return _serve_compatible(db, result, request, original)
    if result['passable'] and httprequest.headers.get('Authorization') and _bearer_ok(db, result, httprequest):
        return _serve_compatible(db, result, request, original)
    return maintenance_response(db, result, httprequest)


def seam_problem(serve_db=None, application_call=None):
    """Why the gate's seam is NOT what this code was written against, or None. An Odoo update that renamed
    ``_serve_db``, gave it arguments, moved the registry load out of it, or stopped ``Application`` from calling it
    would otherwise leave a wrapper that never runs - the gate silently off."""
    serve_db = serve_db or getattr(http.Request, '_serve_db', None)
    serve_db = getattr(serve_db, '__wrapped__', serve_db)
    application_call = application_call or http.Application.__call__
    if not callable(serve_db):
        return "odoo.http.Request._serve_db does not exist"
    try:
        parameters = list(inspect.signature(serve_db).parameters)
        serve_source = inspect.getsource(serve_db)
        app_source = inspect.getsource(application_call)
    except (TypeError, OSError, ValueError) as exc:
        return "the Odoo HTTP seam cannot be inspected (%s)" % exc
    if parameters != ['self']:
        return "odoo.http.Request._serve_db takes %s, not (self)" % ', '.join(parameters)
    serve_nodb = getattr(http.Request, '_serve_nodb', None)
    if not callable(serve_nodb) or list(inspect.signature(serve_nodb).parameters) != ['self']:
        return "odoo.http.Request._serve_nodb (which answers /web/health without a registry) is not (self)"
    if 'Registry(' not in serve_source:
        return "odoo.http.Request._serve_db no longer loads the registry"
    if '._serve_db()' not in app_source:
        return "odoo.http.Application no longer calls Request._serve_db()"
    return None


# ---------------------------------------------------------------------------------------------- the cron seam
def cron_allowed(db, cr=None):
    """The cron runner's decision for ``db``: True only when it is READY. Plain SQL, no registry, like the HTTP one.
    A state that cannot be read is not READY: background work fails closed, nobody is waiting on it."""
    try:
        result = workspace_state(db, cr)
    except Exception:  # noqa: BLE001 - logged once per change below, then nothing runs
        if _cron_logged.get(db) != 'unreadable':
            _cron_logged[db] = 'unreadable'
            _logger.warning("Release gate: db=%s crons NOT run: its release state could not be read", db,
                            exc_info=True)
        return False
    _log_cron_decision(db, result)
    return result['state'] == READY


def _log_cron_decision(db, result):
    """One line per change of a database's cron decision in this process. The first READY of a process is not news
    (every database is polled every minute); READY after a skipped period is."""
    key = (result['state'], tuple(result['required']), result['schema_gap'], result['hold'])
    previous = _cron_logged.get(db)
    if previous == key:
        return
    _cron_logged[db] = key
    before = previous[0].upper() if isinstance(previous, tuple) else (previous or '-').upper()
    if result['state'] == READY:
        if previous is not None:
            _logger.info("Release gate: db=%s role=%s state=%s->READY crons run again; release=%s database=%s",
                         db, result['role'], before, result['release_fingerprint'][:12],
                         (result['db_fingerprint'] or '-')[:12])
        return
    _logger.warning(
        "Release gate: db=%s role=%s state=%s->%s crons NOT run (no registry loaded, no job executed) until it is "
        "READY; release=%s database=%s required=%s missing=%s transitional=%s schema_gap=%s hold=%s%s",
        db, result['role'], before, result['state'].upper(), result['release_fingerprint'][:12],
        (result['db_fingerprint'] or '-')[:12], ','.join(result['required']) or '-',
        ','.join(result['missing']) or '-', ','.join(result['transitional']) or '-', result['schema_gap'],
        result['hold'] or '-', ' (%s)' % result['hold_reason'] if result['hold_reason'] else '')


def gated_process_jobs(db_name, original):
    # Odoo's test runner installs modules with the gate off, as for HTTP (a database mid-install is never READY).
    if config.get('test_enable'):
        return original(db_name)
    return _process_jobs_gate(db_name, original)


def _process_jobs_gate(db_name, original):
    """``IrCron._process_jobs``: the cron runner's entry into one database, decided before the original opens it.

    One exception keeps Odoo's own behaviour: a database that is UPGRADING ONLY because module rows were left
    ``to install/upgrade/remove`` (no hold, nothing behind, nothing missing, no schema gap - an install or ``-u``
    that was killed) is handed to the original, whose ``_check_modules_state`` either skips it (plain SQL) or,
    after Odoo's MAX_FAIL_TIME, runs Odoo's own ``reset_modules_state``. No registry loads on that path: the
    per-job gate below re-checks the state before every job, so a job runs only if the database is READY."""
    if cron_allowed(db_name) or _left_mid_transition(db_name):
        return original(db_name)
    return None


def _left_mid_transition(db_name):
    try:
        result = workspace_state(db_name)
    except Exception:  # noqa: BLE001 - cron_allowed already logged it; nothing runs
        return False
    return bool(result['state'] == UPGRADING and result['transitional'] and not (
        result['hold'] or result['behind'] or result.get('absent') or result['missing'] or result['schema_gap']))


def gated_acquire_one_job(cr, job_id, original, include_not_ready=False):
    if config.get('test_enable'):
        return original(cr, job_id, include_not_ready=include_not_ready)
    return _acquire_one_job_gate(cr, job_id, original, include_not_ready)


def _acquire_one_job_gate(cr, job_id, original, include_not_ready=False):
    """``IrCron._acquire_one_job``: the loop's re-check before EACH job, i.e. before that job loads the registry.
    ``include_not_ready`` is the manual "Run" button (``method_direct_trigger``): an HTTP request, already gated."""
    if include_not_ready or cron_allowed(cr.dbname):
        return original(cr, job_id, include_not_ready=include_not_ready)
    return None


_CRON_SEAM = (('_process_jobs', ['db_name']), ('_process_jobs_loop', ['cron_cr', 'job_ids']),
              ('_acquire_one_job', ['cr', 'job_id', 'include_not_ready']))


def cron_seam_problem(ir_cron=None, server_source=None):
    """Why the cron seam is NOT what this code was written against, or None (the twin of ``seam_problem``): an Odoo
    update that renamed these static methods, changed their arguments, loaded the registry before acquiring a job,
    or stopped the server's cron runners from calling ``_process_jobs`` would leave the wrapper silently off."""
    if ir_cron is None:
        from odoo.addons.base.models.ir_cron import IrCron as ir_cron  # noqa: PLC0415
    sources = {}
    try:
        if server_source is None:
            from odoo.service import server  # noqa: PLC0415
            server_source = inspect.getsource(server)
        for name, parameters in _CRON_SEAM:
            raw = inspect.getattr_static(ir_cron, name, None)
            if not isinstance(raw, staticmethod):
                return "IrCron.%s is not a static method" % name
            function = getattr(raw.__func__, '__wrapped__', raw.__func__)
            found = list(inspect.signature(function).parameters)
            if found != parameters:
                return "IrCron.%s takes %s, not (%s)" % (name, ', '.join(found), ', '.join(parameters))
            sources[name] = inspect.getsource(function)
    except (TypeError, OSError, ValueError) as exc:
        return "the Odoo cron seam cannot be inspected (%s)" % exc
    loop = sources['_process_jobs_loop']
    if '_process_jobs_loop(' not in sources['_process_jobs'] or 'Registry(' in sources['_process_jobs']:
        return "IrCron._process_jobs no longer reaches the registry only through _process_jobs_loop"
    if not ('_acquire_one_job(' in loop and 'Registry(' in loop
            and loop.index('_acquire_one_job(') < loop.index('Registry(')):
        return "IrCron._process_jobs_loop no longer acquires each job before loading the registry"
    if 'IrCron._process_jobs(' not in server_source:
        return "odoo.service.server no longer runs crons through IrCron._process_jobs"
    return None


def install_cron():
    """Wrap the two cron static methods once per process (idempotent); refuse LOUDLY when the seam moved."""
    from odoo.addons.base.models.ir_cron import IrCron  # noqa: PLC0415
    if cron_installed():
        return
    problem = cron_seam_problem(IrCron)
    if problem:
        _logger.critical(
            "Release gate NOT INSTALLED on crons: %s. The crons of a database behind the code would load its registry "
            "and run. Adapt ncollection_core/release_gate.py to this Odoo before serving traffic.", problem)
        return
    process_jobs = IrCron._process_jobs
    acquire_one_job = IrCron._acquire_one_job

    def _process_jobs(db_name):
        return gated_process_jobs(db_name, process_jobs)

    def _acquire_one_job(cr, job_id, *, include_not_ready=False):
        return gated_acquire_one_job(cr, job_id, acquire_one_job, include_not_ready=include_not_ready)

    for wrapper, original in ((_process_jobs, process_jobs), (_acquire_one_job, acquire_one_job)):
        wrapper._nc_release_gate = True
        wrapper.__wrapped__ = original
    IrCron._process_jobs = staticmethod(_process_jobs)
    IrCron._acquire_one_job = staticmethod(_acquire_one_job)
    _logger.info("Release gate installed on crons: seam=IrCron._process_jobs + IrCron._acquire_one_job")


def cron_installed():
    from odoo.addons.base.models.ir_cron import IrCron  # noqa: PLC0415
    return all(getattr(getattr(inspect.getattr_static(IrCron, name, None), '__func__', None),
                       '_nc_release_gate', False)
               for name in ('_process_jobs', '_acquire_one_job'))


# ---------------------------------------------------------------------------------------------- the preload seam
def _installing():
    """This process installs, upgrades or tests (``-i`` / ``-u`` / ``--test-enable``): its preload IS the release
    step (or Odoo's test run), never normal work, so it is never gated."""
    return bool(config.get('test_enable') or config.get('init') or config.get('update') or config.get('reinit'))


def gated_preload_registries(dbnames, original):
    """``odoo.service.server.preload_registries``: a serving process started with ``-d`` (the provisioning runner:
    ``-d ncollection``) loads those registries at boot, before any request or cron - their ``_register_hook`` run
    there. A database that is not READY is left unloaded (logged); its requests and crons stay gated and its registry
    loads on demand once it is READY. A state that cannot be read is Odoo's to report, as before."""
    if not dbnames or _installing():
        return original(dbnames)
    allowed = []
    for db in dbnames:
        try:
            result = workspace_state(db)
        except Exception:  # noqa: BLE001 - Odoo's own preload reports an unreachable database
            allowed.append(db)
            continue
        if result['state'] == READY:
            allowed.append(db)
        else:
            _cron_logged[db] = (result['state'], tuple(result['required']), result['schema_gap'], result['hold'])
            _logger.warning(
                "Release gate: db=%s role=%s state=%s registry NOT preloaded at start; its requests and crons stay "
                "gated until it is READY; release=%s database=%s required=%s hold=%s", db, result['role'],
                result['state'].upper(), result['release_fingerprint'][:12], (result['db_fingerprint'] or '-')[:12],
                ','.join(result['required']) or '-', result['hold'] or '-')
    return original(allowed)


def preload_seam_problem(server_module=None):
    """Why the preload seam is NOT what this code was written against, or None."""
    if server_module is None:
        from odoo.service import server as server_module  # noqa: PLC0415
    preload = getattr(server_module, 'preload_registries', None)
    preload = getattr(preload, '__wrapped__', preload)
    if not callable(preload):
        return "odoo.service.server.preload_registries does not exist"
    try:
        parameters = list(inspect.signature(preload).parameters)
        source = inspect.getsource(preload)
        module_source = inspect.getsource(server_module)
    except (TypeError, OSError, ValueError) as exc:
        return "the Odoo preload seam cannot be inspected (%s)" % exc
    if parameters != ['dbnames']:
        return "odoo.service.server.preload_registries takes %s, not (dbnames)" % ', '.join(parameters)
    if 'Registry.new(' not in source:
        return "odoo.service.server.preload_registries no longer loads the registries"
    if module_source.count('rc = preload_registries(preload)') < 2:
        return "the Odoo servers no longer preload through odoo.service.server.preload_registries"
    return None


def install_preload():
    """Wrap ``preload_registries`` once per process (idempotent); refuse LOUDLY when the seam moved."""
    from odoo.service import server  # noqa: PLC0415
    if preload_installed():
        return
    problem = preload_seam_problem(server)
    if problem:
        _logger.critical(
            "Release gate NOT INSTALLED on preload: %s. A process started with -d would load the registry of a "
            "database behind the code at boot. Adapt ncollection_core/release_gate.py to this Odoo.", problem)
        return
    original = server.preload_registries

    def preload_registries(dbnames):
        return gated_preload_registries(dbnames, original)

    preload_registries._nc_release_gate = True
    preload_registries.__wrapped__ = original
    server.preload_registries = preload_registries
    _logger.info("Release gate installed on preload: seam=odoo.service.server.preload_registries")


def preload_installed():
    from odoo.service import server  # noqa: PLC0415
    return bool(getattr(server.preload_registries, '_nc_release_gate', False))


def install():
    """Wrap ``Request._serve_db`` (and the cron seam) once per process (idempotent); refuse LOUDLY when a seam
    moved. Each seam is installed or refused on its own."""
    install_cron()
    install_preload()
    current = http.Request._serve_db
    if getattr(current, '_nc_release_gate', False):
        return
    problem = seam_problem(current)
    if problem:
        _logger.critical(
            "Release gate NOT INSTALLED: %s. Databases behind the code would answer HTTP 500 instead of the "
            "maintenance response. Adapt ncollection_core/release_gate.py to this Odoo before serving traffic.",
            problem)
        return

    def _serve_db(self):
        return gated_serve_db(self, current)

    _serve_db._nc_release_gate = True
    _serve_db.__wrapped__ = current
    http.Request._serve_db = _serve_db
    _logger.info("Release gate installed: release=%s modules=%d seam=odoo.http.Request._serve_db",
                 release_manifest()['fingerprint'][:12], len(release_manifest()['modules']))


def installed():
    return bool(getattr(http.Request._serve_db, '_nc_release_gate', False))
