# -*- coding: utf-8 -*-
"""HTTP smoke of the paths that 500 when a database is behind its code. Standard library only.

    python3 http_smoke.py <base_url> <login> <password> [<entry_key_db> <scratch_db>]

Run INSIDE the odoo container against a throwaway server on a scratch database (``rehearse_upgrade.sh`` starts it).
It is deliberately the request sequence of a person signing in, because that is the sequence that failed:

    GET  /web/health                       liveness (200 even on a broken database: proves nothing alone)
    GET  /web/login                        renders the brand and the login layout   (500: branding / res_users columns)
    POST /web/login                        authenticates and reads res.users/res.partner (500: res_partner.nc_trn, ...)
    GET  /atlas                            the web client shell (the application path; /odoo redirects there)
    POST /web/session/get_session_info     the same payload the web client boots from; must carry the system brand
    GET  /ncollection/brand/logo           the system-branding image route
    GET  /websocket (plain GET)            must be a 4xx, never a 5xx

COMPANY ENTRY (only when the two optional arguments are given, i.e. the database is a tenant): the exact sequence of
Platform > Companies > Enter Company, with a throwaway grant, against the tenant's own server:

    POST /json/2/ncollection.platform.entry/grant_from_platform   the platform's call, with the tenant's derived bearer
    GET  /ncollection/platform/enter       the bridge page
    POST /ncollection/platform/enter       redeems the one-time token, signs in as the passwordless proxy -> 303 /atlas
    GET  /atlas + get_session_info         the tenant Home, in THIS database, as "Platform Admin: ..."
    GET  /ncollection/platform/exit        returns to the address the platform named (never a localhost the grant did
                                           not carry)
    GET  /atlas                            after exit: signed out, nothing leaked
    POST /ncollection/platform/enter       the same token again: refused (single use)

``entry_key_db`` is the database name the bearer is derived from (the tenant's real name: a scratch COPY keeps the
credential of its source); ``scratch_db`` is the database this server is serving. The platform master key is read from
the container environment (``NC_CONFIG_SYNC_KEY``) and never printed.

Rule: ANY response >= 500 fails the smoke, whatever the endpoint. Expected non-5xx codes are checked too.
Prints ``HTTP_SMOKE <step> <code> OK|FAIL`` per step and a final ``HTTP_SMOKE=PASS|FAIL``; exits 0 / 1.
"""
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

# the platform's own derivation (ncollection_saas config_sync.derive_tenant_key)
_KDF_LABEL = b'nc-config-sync:'
ENTRY_RETURN_URL = 'https://platform.invalid/odoo'     # an address no network resolves: proves what the grant carried


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _derive_tenant_key(master, db):
    mac = hmac.new(master.encode(), _KDF_LABEL + db.encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode()


APP_PATH = '/atlas'   # the public application path (NC_APP_PATH, default /atlas): what the web client answers on


def _caller(base, results):
    """A request function with its OWN cookie jar: two callers never share a session."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPCookieProcessor(jar))

    def call(step, method, path, expected, data=None, headers=None):
        body = data.encode() if isinstance(data, str) else data
        request = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
        location = ''
        try:
            with opener.open(request, timeout=90) as response:
                code, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            code, raw, location = exc.code, exc.read(), exc.headers.get('Location', '')
        except OSError as exc:  # connection refused / timeout: the server did not answer at all
            code, raw = 0, str(exc).encode()
        ok = code in expected and code < 500
        results.append(ok)
        print('HTTP_SMOKE %-26s %3s %s' % (step, code, 'OK' if ok else 'FAIL (expected %s)' % sorted(expected)))
        call.location = location
        return code, raw

    call.location = ''
    return call


def _check(results, label, condition, detail=''):
    results.append(bool(condition))
    print('HTTP_SMOKE %-26s %3s %s' % (label, '-', 'OK' if condition else 'FAIL %s' % detail))


def _session_info(call, step):
    code, raw = call(step, 'POST', '/web/session/get_session_info', {200}, data=json.dumps(
        {'jsonrpc': '2.0', 'method': 'call', 'params': {}}), headers={'Content-Type': 'application/json'})
    try:
        return json.loads(raw).get('result', {}) or {}
    except ValueError:
        return {}


def company_entry(base, entry_key_db, scratch_db, results):
    """Platform > Companies > Enter Company, end to end, on this database. See the module docstring."""
    master = os.environ.get('NC_CONFIG_SYNC_KEY')
    if not master:
        _check(results, 'company entry key', False, '(NC_CONFIG_SYNC_KEY is not set in this container)')
        return
    token = secrets.token_urlsafe(32)
    grant = {'token_hash': hashlib.sha256(token.encode()).hexdigest(), 'platform_ref': 'u424242',
             'platform_name': 'Smoke Platform Admin', 'return_url': ENTRY_RETURN_URL, 'ttl': 60}
    platform = _caller(base, results)       # the platform's server-to-server call: no cookies, no browser
    code, raw = platform('entry: grant', 'POST', '/json/2/ncollection.platform.entry/grant_from_platform', {200},
                         data=json.dumps({'vals': grant}), headers={
                             'Authorization': 'Bearer %s' % _derive_tenant_key(master, entry_key_db),
                             'X-Odoo-Database': scratch_db, 'Content-Type': 'application/json'})
    try:
        granted = bool((json.loads(raw) or {}).get('ok'))
    except ValueError:
        granted = False
    _check(results, 'entry: grant accepted', granted, '(the tenant refused or could not register the grant)')
    if not granted:
        return
    browser = _caller(base, results)        # the administrator's browser: its own session
    browser('entry: bridge page', 'GET', '/ncollection/platform/enter', {200})
    form_headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    browser('entry: redeem token', 'POST', '/ncollection/platform/enter', {303},
            data=urllib.parse.urlencode({'token': token}), headers=form_headers)
    _check(results, 'entry: lands on Home', browser.location == APP_PATH, '(went to %r)' % browser.location)
    browser('entry: tenant Home', 'GET', APP_PATH, {200})
    info = _session_info(browser, 'entry: session info')
    _check(results, 'entry: right database', info.get('db') == scratch_db, '(session db %r)' % info.get('db'))
    _check(results, 'entry: proxy identity', str(info.get('name', '')).startswith('Platform Admin: ')
           and str(info.get('username', '')).startswith('platform.'), '(%r)' % info.get('username'))
    _check(results, 'entry: system brand', bool((info.get('nc_product_brand') or {}).get('name')), '(missing)')
    browser('entry: exit', 'GET', '/ncollection/platform/exit', {303})
    _check(results, 'entry: returns to platform', browser.location == ENTRY_RETURN_URL,
           '(went to %r)' % browser.location)
    browser('entry: after exit', 'GET', APP_PATH, {303})
    _check(results, 'entry: signed out', '/web/login' in browser.location, '(went to %r)' % browser.location)
    replay = _caller(base, results)
    replay('entry: token replay', 'POST', '/ncollection/platform/enter', {403},
           data=urllib.parse.urlencode({'token': token}), headers=form_headers)


def main(base, login, password, entry_key_db=None, scratch_db=None):
    results = []
    call = _caller(base, results)

    call('health', 'GET', '/web/health', {200})
    code, raw = call('login page', 'GET', '/web/login', {200})
    csrf = re.search(rb'name="csrf_token" value="([^"]+)"', raw)
    form = urllib.parse.urlencode(
        {'csrf_token': csrf.group(1).decode() if csrf else '', 'login': login,
         'password': password}
    )
    call('login (POST)', 'POST', '/web/login', {303}, data=form,
         headers={'Content-Type': 'application/x-www-form-urlencoded'})
    # the application path (ncollection_core/app_url.py): login lands on /atlas, never /odoo
    _check(results, 'login lands on the app path', urllib.parse.urlsplit(call.location).path == APP_PATH,
           '(went to %r)' % call.location)
    call('web client ' + APP_PATH, 'GET', APP_PATH, {200})
    call('legacy /odoo link', 'GET', '/odoo', {302})
    _check(results, '/odoo redirects to app path', urllib.parse.urlsplit(call.location).path == APP_PATH,
           '(went to %r)' % call.location)
    brand = _session_info(call, 'session info').get('nc_product_brand')
    has_brand = bool(brand and brand.get('name'))
    results.append(has_brand)
    print('HTTP_SMOKE %-26s %3s %s' % ('system brand in session', '-', 'OK' if has_brand else 'FAIL (missing)'))
    if has_brand:   # evidence that the configured identity survived (a name, never a secret)
        print('HTTP_SMOKE system name                      = %r' % brand['name'])
    call('brand logo', 'GET', '/ncollection/brand/logo', {200})
    call('websocket (plain GET)', 'GET', '/websocket?version=19.0-2', {400, 404, 426})
    if entry_key_db and scratch_db:
        company_entry(base, entry_key_db, scratch_db, results)
    verdict = all(results)
    print('HTTP_SMOKE=%s' % ('PASS' if verdict else 'FAIL'))
    return 0 if verdict else 1


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:6]))
