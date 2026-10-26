# -*- coding: utf-8 -*-
"""The operator's release channel for ONE database - the platform database included, while its application cannot
run. Run INSIDE the container that serves it (stdin), standard library only:

    python3 - <base_url> <db> status
    python3 - <base_url> <db> hold <upgrading|upgrade_failed|blocked> [reason]
    python3 - <base_url> <db> open

It calls the release gate's endpoints (``/ncollection/release/status|hold|open``), which answer BEFORE Odoo loads the
database's registry, with plain SQL: no controller, no ORM, no ``web.login_layout``. The bearer is the config-sync
derivation of ``NC_CONFIG_SYNC_KEY`` (the platform master, in this container's environment) for ``<db>``: the gate
accepts it on the platform database (role ``platform``) and on any tenant holding that derived key. It is never
printed, logged or passed on the command line. Without an accepted bearer the gate answers 404, so this is not a
database-information endpoint for anyone else.

Prints the gate's JSON answer (state, role, required modules with both versions, fingerprints, hold, last successful
release, last failure, checked_at) and ``RELEASE_STATUS_HTTP=<code>`` / ``RELEASE_STATE=<state>`` markers.
Exit 0 on HTTP 200, 1 otherwise.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.request

_KDF_LABEL = b'nc-config-sync:'      # ncollection_saas/models/config_sync.py derive_tenant_key
_PATHS = {'status': ('GET', '/ncollection/release/status'), 'hold': ('POST', '/ncollection/release/hold'),
          'open': ('POST', '/ncollection/release/open')}
_HOLDS = ('upgrading', 'upgrade_failed', 'blocked')


def _bearer(master, db):
    digest = hmac.new(master.encode(), _KDF_LABEL + db.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode()


def main(base, db, action, *rest):
    if not re.fullmatch(r'[a-z0-9]+', db or ''):
        print("REFUSING: '%s' is not a plain lowercase alphanumeric database name" % db, file=sys.stderr)
        return 2
    if action not in _PATHS:
        print("usage: <base_url> <db> status | hold <%s> [reason] | open" % '|'.join(_HOLDS), file=sys.stderr)
        return 2
    master = os.environ.get('NC_CONFIG_SYNC_KEY') or ''
    if not master:
        print("REFUSING: NC_CONFIG_SYNC_KEY is not set in this container (run it where the platform runs)",
              file=sys.stderr)
        return 2
    body = None
    if action == 'hold':
        if not rest or rest[0] not in _HOLDS:
            print("hold needs one of: %s" % ', '.join(_HOLDS), file=sys.stderr)
            return 2
        body = json.dumps({'hold': rest[0], 'reason': ' '.join(rest[1:])[:200]})
    elif action == 'open':
        body = '{}'
    method, path = _PATHS[action]
    # db_filter=^%d$ takes the Host's first label: <db>.localhost selects <db> on this server's own loopback.
    headers = {'Host': '%s.localhost' % db, 'Authorization': 'Bearer %s' % _bearer(master, db),
               'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base.rstrip('/') + path, data=body.encode() if body else None, method=method,
                                     headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
        status, text = response.status, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode('utf-8', 'replace')
    except urllib.error.URLError as exc:
        print("RELEASE_STATUS_HTTP=unreachable (%s)" % exc.reason)
        return 1
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {'error': 'not a JSON answer (is the release gate installed on this server?)'}
    print(json.dumps(payload, indent=2, sort_keys=True))
    if status == 404:
        print("(404: the bearer was not accepted - this server holds no matching key for '%s')" % db, file=sys.stderr)
    print('RELEASE_STATUS_HTTP=%s' % status)
    print('RELEASE_STATE=%s' % (payload.get('state') or '-'))
    print('RELEASE_ROLE=%s' % (payload.get('role') or '-'))
    print('RELEASE_REQUIRED=%s' % (','.join(payload.get('required') or []) or '-'))
    print('RELEASE_TRANSITIONAL=%s' % (','.join(payload.get('transitional') or []) or '-'))
    print('RELEASE_COMPATIBLE=%s' % ('1' if payload.get('compatible') else '0'))
    print('RELEASE_FINGERPRINT=%s' % (payload.get('release_fingerprint') or '-'))
    print('RELEASE_DB_FINGERPRINT=%s' % (payload.get('db_fingerprint') or '-'))
    print('RELEASE_LAST_OK=%s' % (payload.get('last_ok') or '-'))
    print('RELEASE_LAST_ERROR=%s' % (payload.get('last_error') or '-'))
    return 0 if status == 200 else 1


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
