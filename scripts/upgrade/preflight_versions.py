# -*- coding: utf-8 -*-
"""Release preflight: is any installed module behind the code that is about to serve it? READ-ONLY.

    python3 preflight_versions.py <db> <db_host> <db_user> <db_password>
    python3 preflight_versions.py --validate <module,module,...>

Run INSIDE the odoo container (``preflight_versions.sh`` and ``rehearse_upgrade.sh`` do). It compares, for the database
``db``, the version every INSTALLED module is recorded at (``ir_module_module.latest_version``) with the version its
``__manifest__.py`` has on disk, over the NCollection and OCA addons roots of the stack. Odoo itself never makes that
comparison; the first request that reads a changed model does, and answers HTTP 500 (R-037).

What it does NOT do, on purpose: it opens no registry (a registry load runs hooks that write), it changes nothing
(the session is read-only), and it upgrades nothing. It is the cheap fail-closed gate to run against a REAL database
before traffic is pointed at new code; the schema-level proof (missing columns and tables) is the upgrade rehearsal, on
a copy. Exit codes:

* 0 ``PREFLIGHT=PASS``  - at least one module was checked and none is behind;
* 1 ``PREFLIGHT=FAIL``  - at least one module is behind (each is named with both versions);
* 2 ``PREFLIGHT=ERROR`` - it could not check: no connection, NOTHING checked (an empty or wrong addons root would
  otherwise "pass" every database), or an installed ``ncollection_*`` module has no code on disk. Never a silent pass.

``--validate`` is the ONE definition of what an upgrade may name, shared by the preflight and the rehearsal: plain
module names, never ``all``, never ``base``, and only modules whose code is under the NCollection / OCA roots (so an
Odoo core module is refused, and ``-u`` can never quietly become a whole-database upgrade). Exit 0 ``MODULES=OK``,
exit 2 ``MODULES=REFUSED`` with one ``MODULES_REFUSED=`` line per reason.
"""
import ast
import os
import re
import sys

ADDONS_ROOTS = ('/mnt/extra-addons', '/mnt/oca-addons')
# Never upgraded by this tooling, whatever the roots contain: `-u base` re-loads every installed module.
REFUSED_MODULES = frozenset({'all', 'base'})
_MODULE_NAME = re.compile(r'[a-z0-9_]+')


def _tuple(version):
    """'19.0.1.12.0' -> (19, 0, 1, 12, 0). Non-numeric parts sort as zero (a manifest version is numeric)."""
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r'[.\-]', version or '0'))


def _disk_versions(roots=ADDONS_ROOTS):
    """{module: version} for every manifest under the addons roots (custom addons and each OCA repo)."""
    found = {}
    for root in roots:
        for current, dirs, files in os.walk(root):
            if '__manifest__.py' in files and current != root:
                try:
                    with open(os.path.join(current, '__manifest__.py'), encoding='utf-8') as handle:
                        manifest = ast.literal_eval(handle.read())
                except (OSError, ValueError, SyntaxError):
                    continue
                if isinstance(manifest, dict) and manifest.get('version'):
                    found.setdefault(os.path.basename(current), str(manifest['version']))
                dirs[:] = []          # a module's own sub-directories are not modules
    return found


def _disk_auto_install(roots=ADDONS_ROOTS):
    """{module: triggers} for every ``ncollection_*`` module the code AUTO-INSTALLS (the same rule as the release
    gate, ``ncollection_core/release_gate.py``): its ``auto_install`` list, or all its ``depends``."""
    found = {}
    for root in roots:
        for current, dirs, files in os.walk(root):
            name = os.path.basename(current)
            if '__manifest__.py' in files and current != root:
                dirs[:] = []
                if not name.startswith('ncollection_'):
                    continue
                try:
                    with open(os.path.join(current, '__manifest__.py'), encoding='utf-8') as handle:
                        manifest = ast.literal_eval(handle.read())
                except (OSError, ValueError, SyntaxError):
                    continue
                if (isinstance(manifest, dict) and manifest.get('auto_install')
                        and manifest.get('installable', True)):
                    trigger = manifest['auto_install']
                    found.setdefault(name, tuple(trigger if isinstance(trigger, (list, tuple))
                                                 else manifest.get('depends') or ()))
    return found


def absent_modules(installed_names, auto):
    """The auto-install modules this database should have and does not, to a fixed point."""
    have, absent, changed = set(installed_names), [], True
    while changed:
        changed = False
        for name in sorted(auto):
            if name not in have and all(dep in have for dep in auto[name]):
                have.add(name)
                absent.append(name)
                changed = True
    return sorted(absent)


def evaluate(db, installed, disk, auto=None):
    """The verdict for ``installed`` [(name, recorded version)] against ``disk`` {name: version}: (code, lines).
    ``auto`` {module: triggers}: a module the code auto-installs and this database lacks is ``absent`` (the exact
    ``-i`` list, PREFLIGHT_INSTALL) and fails the preflight like a module behind does."""
    behind, unknown = [], []
    for name, recorded in installed:
        if name not in disk:
            unknown.append(name)           # a core Odoo module (not under our roots) or one whose code is gone
            continue
        if _tuple(disk[name]) > _tuple(recorded):
            behind.append((name, recorded or '(none)', disk[name]))
    missing = sorted(name for name in unknown if name.startswith('ncollection_'))
    absent = absent_modules([name for name, _v in installed], auto or {})
    checked = len(installed) - len(unknown)
    lines = ['PREFLIGHT_DB=%s' % db, 'PREFLIGHT_CHECKED=%d' % checked]
    lines += ['PREFLIGHT_BEHIND=%s database %s < code %s' % row for row in behind]
    lines += ['PREFLIGHT_MISSING=%s is installed but has no code under %s' % (name, ', '.join(ADDONS_ROOTS))
              for name in missing]
    lines += ['PREFLIGHT_ABSENT=%s is auto-installed by the code and not installed here' % name for name in absent]
    lines.append('PREFLIGHT_MODULES=%s' % ','.join(name for name, _r, _d in behind))
    lines.append('PREFLIGHT_INSTALL=%s' % ','.join(absent))
    if not checked:
        lines.append('PREFLIGHT_ERROR no installed module was found under the addons roots: nothing was checked')
    if not checked or missing:
        lines.append('PREFLIGHT=ERROR')
        return 2, lines
    if behind or absent:
        lines.append('PREFLIGHT=FAIL')
        return 1, lines
    lines.append('PREFLIGHT=PASS')
    return 0, lines


def validate_modules(spec, disk):
    """Reasons ``spec`` (a comma separated module list) may NOT be upgraded; an empty list means it may."""
    names = [name for name in (spec or '').split(',')]
    if not spec or any(not name for name in names):
        return ['the module list is empty or has an empty entry: %r' % (spec or '')]
    problems = []
    for name in names:
        if not _MODULE_NAME.fullmatch(name):
            problems.append('%r is not a plain module name' % name)
        elif name in REFUSED_MODULES:
            problems.append("%s is never upgraded by this tooling (it would re-load every installed module)" % name)
        elif name not in disk:
            problems.append('%s is not an NCollection or OCA module (not under %s): an Odoo core module, or one '
                            'whose code is not deployed' % (name, ', '.join(ADDONS_ROOTS)))
    return problems


def main(db, host, user, password):
    import psycopg2  # only the database mode needs it

    try:
        connection = psycopg2.connect(dbname=db, host=host, user=user, password=password, connect_timeout=15)
    except psycopg2.Error as exc:
        print('PREFLIGHT_ERROR cannot connect to %r: %s' % (db, str(exc).strip().splitlines()[0]))
        print('PREFLIGHT=ERROR')
        return 2
    connection.set_session(readonly=True, autocommit=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT name, latest_version FROM ir_module_module WHERE state = 'installed' ORDER BY name")
        installed = cursor.fetchall()
    connection.close()
    code, lines = evaluate(db, installed, _disk_versions(), _disk_auto_install())
    print('\n'.join(lines))
    return code


def main_validate(spec):
    problems = validate_modules(spec, _disk_versions())
    for problem in problems:
        print('MODULES_REFUSED=%s' % problem)
    print('MODULES=%s' % ('REFUSED' if problems else 'OK'))
    return 2 if problems else 0


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--validate':
        sys.exit(main_validate(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[2] == '--env':
        # the odoo container's own connection (HOST / USER / PASSWORD): no credential on any command line
        sys.exit(main(sys.argv[1], os.environ.get('HOST'), os.environ.get('USER'), os.environ.get('PASSWORD')))
    if len(sys.argv) != 5:
        print('usage: preflight_versions.py <db> --env | <db> <db_host> <db_user> <db_password> | '
              '--validate <modules>')
        sys.exit(2)
    sys.exit(main(*sys.argv[1:5]))
