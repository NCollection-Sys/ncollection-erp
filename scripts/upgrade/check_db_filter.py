"""Prove the upgrade-window ``--db-filter`` expression with Odoo's own ``db_filter``.

Run inside the odoo image, no network, no database (it only evaluates the regular expression):

    docker run --rm --network none -v <this file>:/c.py:ro -v <names.txt>:/dbs.txt:ro odoo:19 python3 -I /c.py

``names.txt`` is one database name per line (``psql -l`` of the shared server). Exit status 1 when a target
database is not reachable through its ``<db>.localhost`` host or when any other database would be allowed.
"""
import sys

from odoo.http import db_filter
from odoo.tools import config

EXPR = r'^(?=(?:ncollection|ncollection_demo|awtar)$)%d$'
TARGETS = {'ncollection', 'awtar', 'ncollection_demo'}


def main(path='/dbs.txt'):
    with open(path, encoding='utf-8') as handle:
        names = [line.strip() for line in handle if line.strip() and line.strip() != 'postgres']
    config['dbfilter'] = EXPR
    bad = 0
    for name in names:
        for host in ('%s.localhost' % name, '%s.localhost:8069' % name, 'www.%s.localhost' % name):
            got = db_filter(names, host=host)
            want = [name] if name in TARGETS else []
            if got != want:
                bad += 1
                print('BAD', host, '->', got)
    for host in ('localhost', 'localhost:8069', '', '127.0.0.1', 'evil.localhost', 'xncollection.localhost'):
        got = db_filter(names, host=host)
        if got:
            bad += 1
            print('BAD', repr(host), '->', got)
    print('%d databases checked, %d targets, %d violations' % (len(names), len(TARGETS & set(names)), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
