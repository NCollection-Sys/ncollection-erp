#!/usr/bin/env python3
"""NCollection edge agent: serves VERIFIED custom tenant domains for every SaaS platform on this host.

One agent per host, any number of platforms (Balance Cloud, Atlas Core, ...). Each run (systemd timer, every few
minutes, flock-serialised):

  1. pull each platform's desired state (GET /ncollection/edge/v1/state, HMAC bearer);
  2. refuse a hostname claimed by two platforms (first claim wins, kept in the claim register), or already served by
     another site's nginx file on this host (never hijack a neighbour);
  3. for a domain in SSL Pending: re-check ownership itself (TXT token on two public resolvers) and that it resolves
     ONLY to this edge, serve the ACME challenge, issue a certificate (lego HTTP-01), write the domain's nginx block
     (pins X-NC-Tenant-DB to the tenant database), `nginx -t` (restore on failure), reload, and prove
     https://<domain>/web/health answers before reporting it active;
  4. for an Active domain: keep its block, renew a certificate due within 30 days;
  5. remove the block of a domain no longer desired (disabled / deleted). Certificates are kept, never revoked;
  6. report results to each platform (POST /ncollection/edge/v1/report).

A platform that cannot be reached is skipped: its existing blocks stay as they are (no flapping). Failures back off
(1 h, 6 h, 24 h) and become final after MAX_ATTEMPTS, which respects the CA's rate limits.

Python 3 standard library only; external tools: nginx, systemctl, docker (lego image), dig, curl. All of them can be
replaced through NC_EDGE_* environment variables, which is how the tests run it without touching a real host.
"""
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_CONFIG = '/etc/nc-edge/edge.json'
MANAGED_PREFIX = 'nc-domain-'
MAX_ATTEMPTS = 5
BACKOFF_HOURS = (0, 1, 6, 24, 24)
RENEW_DAYS = 30
_HOST_RE = re.compile(r'^(?=.{4,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$')
_DB_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,62}$')
_LABEL = b'nc-edge-v1:'


def log(msg):
    print('%s %s' % (dt.datetime.now(dt.timezone.utc).strftime('%FT%TZ'), msg), flush=True)


def cmd(name, default):
    return shlex.split(os.environ.get('NC_EDGE_' + name, default))


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=kw.pop('timeout', 300), **kw)


def derive_key(master, db):
    mac = hmac.new(master.encode(), _LABEL + db.encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode()


def read_env_value(path, key):
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip()
    return ''


# ---------------------------------------------------------------- platform channel
def platform_key(platform):
    master = read_env_value(platform['env_file'], 'NC_CONFIG_SYNC_KEY')
    if not master:
        raise RuntimeError('no NC_CONFIG_SYNC_KEY in %s' % platform['env_file'])
    return derive_key(master, platform['database'])


def call(platform, path, body=None):
    req = urllib.request.Request(
        platform['internal_url'].rstrip('/') + path, method='POST' if body is not None else 'GET',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Host': platform['host'], 'Authorization': 'Bearer ' + platform_key(platform),
                 'Content-Type': 'application/json', 'X-NC-Tenant-DB': ''})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed loopback URL from root config
        return json.loads(resp.read(1024 * 1024) or b'{}')


# ---------------------------------------------------------------- state / claims
def load_state(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {'claims': {}, 'attempts': {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(state, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def foreign_names(nginx_dir):
    """server_name values of every nginx file this agent does NOT manage (exact names and '.suffix' wildcards)."""
    names = set()
    for entry in sorted(os.listdir(nginx_dir)):
        if entry.startswith(MANAGED_PREFIX):
            continue
        try:
            with open(os.path.join(nginx_dir, entry), encoding='utf-8', errors='ignore') as handle:
                text = handle.read()
        except OSError:
            continue
        for match in re.finditer(r'^\s*server_name\s+([^;]+);', text, re.M):
            names.update(n.lower() for n in match.group(1).split())
    return names


def collides(fqdn, names):
    for name in names:
        if name in ('_', ''):
            continue
        if name.startswith('.') and (fqdn == name[1:] or fqdn.endswith(name)):
            return name
        if name.startswith('*.') and fqdn.endswith(name[1:]):
            return name
        if fqdn == name:
            return name
    return None


# ---------------------------------------------------------------- DNS (independent of the platform's own check)
def dig(name, rtype, server):
    out = run(cmd('DIG', 'dig') + ['+short', '+time=3', '+tries=2', rtype, name, '@' + server], timeout=20)
    return [line.strip().strip('"') for line in out.stdout.splitlines() if line.strip()]


def dns_ok(fqdn, token, edge, resolvers):
    for server in resolvers:
        txt = dig('_nc-verify.' + fqdn, 'TXT', server)
        if ('nc-verify=' + token) not in txt:
            return 'ownership TXT not visible on %s' % server
        found = set()
        for rtype in ('A', 'AAAA'):
            found.update(v for v in dig(fqdn, rtype, server) if not v.endswith('.'))
        allowed = {edge.get('ipv4'), edge.get('ipv6')} - {None, ''}
        if not found & allowed:
            return '%s does not resolve to this edge on %s' % (fqdn, server)
        if found - allowed:
            return '%s also resolves to %s (remove those records)' % (fqdn, ', '.join(sorted(found - allowed)))
    return None


# ---------------------------------------------------------------- certificates
def lego(conf, fqdn, action):
    base = cmd('LEGO', conf['lego_cmd'])
    args = base + ['--email', conf['acme_email'], '--accept-tos', '--path', conf['cert_dir'],
                   '--http', '--http.webroot', conf['webroot'], '-d', fqdn]
    if conf.get('acme_server'):
        args += ['--server', conf['acme_server']]
    args += ['run'] if action == 'run' else ['renew', '--days', str(RENEW_DAYS)]
    return run(args, timeout=600)


def cert_paths(conf, fqdn):
    base = os.path.join(conf['cert_dir'], 'certificates', fqdn)
    return base + '.crt', base + '.key'


def cert_info(conf, fqdn):
    crt, key = cert_paths(conf, fqdn)
    if not (os.path.exists(crt) and os.path.exists(key)):
        return None
    out = run(cmd('OPENSSL', 'openssl') + ['x509', '-in', crt, '-noout', '-enddate', '-issuer',
                                           '-ext', 'subjectAltName'],
              timeout=20)
    if out.returncode:
        return None
    end = re.search(r'notAfter=(.+)', out.stdout)
    issuer = re.search(r'issuer=(.+)', out.stdout)
    if not end or ('DNS:' + fqdn) not in out.stdout:
        return None
    expiry = dt.datetime.strptime(end.group(1).strip(), '%b %d %H:%M:%S %Y %Z').date()
    return {'expiry': expiry.isoformat(), 'issuer': (issuer.group(1).strip() if issuer else '')[:120]}


# ---------------------------------------------------------------- nginx
def render(conf, platform, item, with_tls):
    fqdn, db = item['fqdn'], item['database']
    crt, key = cert_paths(conf, fqdn)
    zone = platform.get('zone_prefix', '')
    snippets = platform['snippet_dir']
    acme = ('    location /.well-known/acme-challenge/ {\n        root %s;\n    }\n' % conf['webroot'])
    http_block = ('server {\n    listen 80;\n    listen [::]:80;\n    server_name %s;\n%s'
                  '    location / {\n        return 301 https://$host$request_uri;\n    }\n}\n') % (fqdn, acme)
    if not with_tls:
        header = '# managed by nc_edge_agent (%s -> %s, platform %s): ACME bootstrap\n' % (fqdn, db, platform['name'])
        return header + http_block
    proxy = ('        proxy_set_header Host $host;\n'
             '        proxy_set_header X-Forwarded-Host $host;\n'
             '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
             '        proxy_set_header X-Forwarded-Proto $scheme;\n'
             '        proxy_set_header X-Real-IP $remote_addr;\n'
             '        proxy_set_header Upgrade $http_upgrade;\n'
             '        proxy_set_header Connection $nc_edge_upgrade;\n'
             '        proxy_set_header X-NC-Tenant-DB "%s";\n'
             '        proxy_hide_header X-Frame-Options;\n'
             '        proxy_hide_header X-Content-Type-Options;\n'
             '        proxy_hide_header Content-Security-Policy;\n'
             '        proxy_connect_timeout 720s;\n'
             '        proxy_send_timeout 720s;\n'
             '        proxy_read_timeout 720s;\n'
             ) % db
    login = ''
    if zone:
        login = '        limit_req zone=%slogin burst=10 nodelay;\n        limit_req_status 429;\n' % zone
    return (
        '# managed by nc_edge_agent: %(fqdn)s -> tenant database %(db)s on platform %(platform)s. Do not edit.\n'
        '%(http)s'
        'server {\n'
        '    listen 443 ssl;\n    listen [::]:443 ssl;\n    http2 on;\n'
        '    server_name %(fqdn)s;\n'
        '    ssl_certificate     %(crt)s;\n    ssl_certificate_key %(key)s;\n'
        '    ssl_protocols TLSv1.2 TLSv1.3;\n    ssl_session_tickets off;\n'
        '    client_max_body_size 100M;\n'
        '    include %(snippets)s/security-headers.conf;\n    include %(snippets)s/gzip.conf;\n'
        '    add_header Strict-Transport-Security "max-age=31536000" always;\n'
        '    location /web/database { return 403; }\n'
        '    location = /web/login {\n%(login)s%(proxy)s        proxy_pass http://%(upstream)s;\n    }\n'
        '    location /websocket {\n%(proxy)s        proxy_pass http://%(bus)s;\n    }\n'
        '    location / {\n%(proxy)s        proxy_pass http://%(upstream)s;\n    }\n'
        '}\n') % {'fqdn': fqdn, 'db': db, 'platform': platform['name'], 'http': http_block, 'crt': crt, 'key': key,
                  'snippets': snippets, 'login': login, 'proxy': proxy, 'upstream': platform['upstream'],
                  'bus': platform['bus']}


def vhost_path(conf, fqdn):
    return os.path.join(conf['nginx_dir'], MANAGED_PREFIX + fqdn + '.conf')


def apply_file(conf, path, content):
    """Write ``content`` (None = remove), validate with nginx -t, restore the previous file on failure, reload."""
    previous = None
    if os.path.exists(path):
        with open(path, encoding='utf-8') as handle:
            previous = handle.read()
    if previous == content:
        return None
    if content is None:
        os.remove(path)
    else:
        with open(path + '.tmp', 'w', encoding='utf-8') as handle:
            handle.write(content)
        os.replace(path + '.tmp', path)
    test = run(cmd('NGINX', 'nginx') + ['-t'], timeout=60)
    if test.returncode:
        if previous is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(previous)
        return 'nginx -t refused the configuration: %s' % (test.stderr.strip().splitlines() or ['?'])[-1]
    reload = run(cmd('SYSTEMCTL', 'systemctl') + ['reload', 'nginx'], timeout=60)
    return None if reload.returncode == 0 else 'nginx reload failed'


def https_ok(fqdn):
    out = run(cmd('CURL', 'curl') + ['-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '20',
                                     '--resolve', '%s:443:127.0.0.1' % fqdn, 'https://%s/web/health' % fqdn],
              timeout=40)
    return out.stdout.strip() == '200'


# ---------------------------------------------------------------- one run
def due(state, fqdn, now):
    record = state['attempts'].get(fqdn)
    if not record:
        return True
    if record['count'] >= MAX_ATTEMPTS:
        return False
    wait = BACKOFF_HOURS[min(record['count'], len(BACKOFF_HOURS) - 1)]
    return now >= dt.datetime.fromisoformat(record['last']) + dt.timedelta(hours=wait)


def fail(state, fqdn, now, error):
    record = state['attempts'].setdefault(fqdn, {'count': 0, 'last': now.isoformat()})
    record['count'] += 1
    record['last'] = now.isoformat()
    final = record['count'] >= MAX_ATTEMPTS
    return {'fqdn': fqdn, 'ok': False, 'error': error, 'final': final}


def process(conf, platform, item, state, now):
    fqdn = item['fqdn']
    path = vhost_path(conf, fqdn)
    info = cert_info(conf, fqdn)
    if item['state'] == 'ssl_pending' or info is None:
        if not due(state, fqdn, now):
            return None
        if item['state'] == 'ssl_pending':
            problem = dns_ok(fqdn, item.get('token') or '', conf['edge'], conf['resolvers'])
            if problem:
                return fail(state, fqdn, now, problem)
        os.makedirs(conf['webroot'], exist_ok=True)
        error = apply_file(conf, path, render(conf, platform, item, with_tls=False)) if info is None else None
        if error:
            return fail(state, fqdn, now, error)
        if info is None:
            issued = lego(conf, fqdn, 'run')
            info = cert_info(conf, fqdn) if issued.returncode == 0 else None
            if info is None:
                tail = (issued.stderr or issued.stdout or '').strip().splitlines()[-1:] or ['certificate not issued']
                return fail(state, fqdn, now, 'certificate request failed: %s' % tail[0][:300])
    elif dt.date.fromisoformat(info['expiry']) - now.date() <= dt.timedelta(days=RENEW_DAYS):
        renewed = lego(conf, fqdn, 'renew')
        info = cert_info(conf, fqdn) or info
        if renewed.returncode:
            log('renewal of %s failed; keeping the current certificate (expires %s)' % (fqdn, info['expiry']))
    error = apply_file(conf, path, render(conf, platform, item, with_tls=True))
    if error:
        return fail(state, fqdn, now, error)
    if not https_ok(fqdn):
        return fail(state, fqdn, now, 'https://%s/web/health did not answer 200 through the edge' % fqdn)
    state['attempts'].pop(fqdn, None)
    return {'fqdn': fqdn, 'ok': True, 'expiry': info['expiry'], 'issuer': info['issuer']}


COMMON_FILE = 'nc-edge-common.conf'
COMMON = ('# managed by nc_edge_agent: shared by every custom-domain block (nc-domain-*.conf). Do not edit.\n'
          'map $http_upgrade $nc_edge_upgrade {\n    default upgrade;\n    \'\'      close;\n}\n')


def run_once(conf, now=None):
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    state = load_state(conf['state_file'])
    error = apply_file(conf, os.path.join(conf['nginx_dir'], COMMON_FILE), COMMON)
    if error:
        log('cannot install %s (%s): nothing else is changed this run' % (COMMON_FILE, error))
        return {}
    desired, reached, reports = {}, set(), {}
    for platform in conf['platforms']:
        try:
            answer = call(platform, '/ncollection/edge/v1/state')
        except (OSError, urllib.error.URLError, ValueError, RuntimeError) as exc:
            log('platform %s unreachable (%s): its domains are left as they are' % (platform['name'], exc))
            continue
        reached.add(platform['name'])
        reports[platform['name']] = []
        for item in answer.get('domains') or []:
            fqdn, db = str(item.get('fqdn') or ''), str(item.get('database') or '')
            if not _HOST_RE.fullmatch(fqdn) or not _DB_RE.fullmatch(db):
                continue
            desired.setdefault(fqdn, []).append((platform, item))
    foreign = foreign_names(conf['nginx_dir'])
    for fqdn, claims in sorted(desired.items()):
        owner = state['claims'].get(fqdn)
        names = [p['name'] for p, _ in claims]
        if len(claims) > 1:
            keep = [c for c in claims if c[0]['name'] == owner]
            for platform, _item in claims:
                if not keep or platform['name'] != owner:
                    reports[platform['name']].append({'fqdn': fqdn, 'ok': False, 'final': True,
                                                      'error': 'this hostname is claimed by another platform on the '
                                                               'same edge (%s)' % ', '.join(sorted(names))})
            claims = keep
        elif owner and owner != claims[0][0]['name'] and owner in reached:
            reports[claims[0][0]['name']].append({'fqdn': fqdn, 'ok': False, 'final': True,
                                                  'error': 'this hostname belongs to another platform on this edge'})
            continue
        for platform, item in claims:
            clash = collides(fqdn, foreign)
            if clash:
                error = 'already served by another site on this server (%s)' % clash
                reports[platform['name']].append({'fqdn': fqdn, 'ok': False, 'final': True, 'error': error})
                continue
            state['claims'][fqdn] = platform['name']
            result = process(conf, platform, item, state, now)
            if result:
                reports[platform['name']].append(result)
    # retire blocks nobody wants any more (only for platforms that answered: an outage never unpublishes a domain)
    for entry in sorted(os.listdir(conf['nginx_dir'])):
        if not (entry.startswith(MANAGED_PREFIX) and entry.endswith('.conf')):
            continue
        fqdn = entry[len(MANAGED_PREFIX):-len('.conf')]
        owner = state['claims'].get(fqdn)
        if fqdn not in desired and (owner in reached or owner is None):
            error = apply_file(conf, vhost_path(conf, fqdn), None)
            log('retired %s%s' % (fqdn, (': ' + error) if error else ''))
            if not error:
                state['claims'].pop(fqdn, None)
                state['attempts'].pop(fqdn, None)
    save_state(conf['state_file'], state)
    for platform in conf['platforms']:
        results = reports.get(platform['name'])
        if results:
            try:
                call(platform, '/ncollection/edge/v1/report', {'results': results})
            except (OSError, urllib.error.URLError, ValueError, RuntimeError) as exc:
                log('report to %s failed: %s' % (platform['name'], exc))
        for result in results or []:
            log('%s %s: %s' % (platform['name'], result['fqdn'], 'active' if result['ok'] else result['error']))
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--config', default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    with open(args.config, encoding='utf-8') as handle:
        conf = json.load(handle)
    os.makedirs(os.path.dirname(conf['state_file']), exist_ok=True)
    with open(conf['state_file'] + '.lock', 'w', encoding='utf-8') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log('another run is in progress')
            return 0
        run_once(conf)
    return 0


if __name__ == '__main__':
    sys.exit(main())
