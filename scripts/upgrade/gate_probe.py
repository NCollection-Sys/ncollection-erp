# -*- coding: utf-8 -*-
"""One HTTP call against a release-gated Odoo, for the upgrade suite (verify_upgrade.sh arm 8). Run INSIDE the odoo
container (stdin), standard library only:

    python3 - <base_url> <host> <METHOD> <path> [<bearer_db> | -] [<json body> | -]

<host> selects the database (db_filter=^%d$ takes its first label). <bearer_db> adds the tenant's config-sync bearer,
derived from NC_CONFIG_SYNC_KEY in the container's environment exactly as the platform derives it (never printed).
Prints ONE line:

    GATE status=<code> state=<X-NC-Workspace-State or -> traceback=<0|1> internal_error=<0|1> maintenance=<0|1>
         json=<compact body or ->
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request

_KDF_LABEL = b'nc-config-sync:'


def _derive_tenant_key(master, db):
    digest = hmac.new(master.encode(), _KDF_LABEL + db.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main(base, host, method, path, bearer_db='-', body='-'):
    headers = {'Host': host}
    data = None
    if bearer_db and bearer_db != '-':
        headers['Authorization'] = 'Bearer %s' % _derive_tenant_key(os.environ['NC_CONFIG_SYNC_KEY'], bearer_db)
    if body and body != '-':
        data = body.encode()
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base.rstrip('/') + path, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=60)
        status, text, state = response.status, response.read().decode('utf-8', 'replace'), response.headers
    except urllib.error.HTTPError as exc:
        status, text, state = exc.code, exc.read().decode('utf-8', 'replace'), exc.headers
    workspace = state.get('X-NC-Workspace-State') or '-'
    compact = '-'
    if 'json' in (state.get('Content-Type') or ''):
        try:
            compact = json.dumps(json.loads(text), separators=(',', ':'), sort_keys=True)
        except ValueError:
            compact = '-'
    print('GATE status=%s state=%s traceback=%d internal_error=%d maintenance=%d json=%s' % (
        status, workspace, int('Traceback' in text), int('Internal Server Error' in text),
        int('This workspace is being updated' in text), compact))
    return 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:7]))
