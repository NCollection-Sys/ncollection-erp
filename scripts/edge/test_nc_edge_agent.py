#!/usr/bin/env python3
"""Tests for nc_edge_agent.py. No real nginx, DNS, CA or platform: every tool is a fake driven by a JSON file and each
platform is a local HTTP server that checks the HMAC bearer. Run: python3 scripts/edge/test_nc_edge_agent.py"""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nc_edge_agent as agent  # noqa: E402

FAKE = r'''
import json, os, sys
state = json.load(open(os.environ["FAKE_STATE"]))
tool, args = sys.argv[1], sys.argv[2:]
calls = state.setdefault("calls", [])
calls.append([tool] + args)
json.dump(state, open(os.environ["FAKE_STATE"], "w"))
if tool == "dig":
    rtype, name, server = args[-3], args[-2], args[-1]
    for line in state["dns"].get("%s %s" % (rtype, name), []):
        print('"%s"' % line if rtype == "TXT" else line)
elif tool == "nginx":
    n = len([c for c in calls if c[0] == "nginx"])
    sys.exit(1 if state.get("nginx_fail") and n > 1 else 0)
elif tool == "systemctl":
    sys.exit(0)
elif tool == "lego":
    if state.get("lego_fail"):
        print("acme: error: 400 :: urn:ietf:params:acme:error:connection", file=sys.stderr); sys.exit(1)
    path = args[args.index("--path") + 1]; fqdn = args[args.index("-d") + 1]
    os.makedirs(os.path.join(path, "certificates"), exist_ok=True)
    for ext in ("crt", "key"):
        open(os.path.join(path, "certificates", fqdn + "." + ext), "w").write(fqdn)
elif tool == "openssl":
    fqdn = open(args[args.index("-in") + 1]).read().strip()
    print("notAfter=Feb 17 23:36:17 2099 GMT"); print("issuer=C = US, O = Let's Encrypt, CN = YE1")
    print("X509v3 Subject Alternative Name:"); print("    DNS:" + fqdn)
elif tool == "curl":
    print(state.get("https", "200"), end="")
'''

MASTER = 'test-master-key'


class FakePlatform(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _ok(self):
        # Like production with NC_TENANT_DB_HEADER=1: ANY X-NC-Tenant-DB header (even empty) pins away from the
        # platform database, so a request carrying one never reaches these endpoints.
        if 'X-NC-Tenant-DB' in self.headers:
            return False
        db = self.server.db
        return self.headers.get('Authorization') == 'Bearer ' + agent.derive_key(MASTER, db)

    def do_GET(self):  # noqa: N802
        if not self._ok():
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({'platform': self.server.base, 'domains': self.server.domains}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if not self._ok():
            self.send_response(404)
            self.end_headers()
            return
        self.server.reports.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"applied": 1}')


class EdgeAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.nginx_dir = os.path.join(root, 'sites')
        os.makedirs(self.nginx_dir)
        with open(os.path.join(self.nginx_dir, 'balancecloud.ae.conf'), 'w') as fh:
            fh.write('server {\n    server_name .balancecloud.ae;\n}\n'
                     'server {\n  server_name shop.neighbour.com;\n}\n')
        self.fake_state = os.path.join(root, 'fake.json')
        fake = os.path.join(root, 'fake.py')
        with open(fake, 'w') as fh:
            fh.write(FAKE)
        for tool in ('dig', 'nginx', 'systemctl', 'lego', 'openssl', 'curl'):
            os.environ['NC_EDGE_' + tool.upper()] = '%s %s %s' % (sys.executable, fake, tool)
        os.environ['FAKE_STATE'] = self.fake_state
        self.servers = []
        self.platforms = [self._platform('balancecloud', 'balancecloud.ae', 'bc_'),
                          self._platform('atlascore', 'atlas-core.io', 'ac_')]
        self.conf = {
            'platforms': self.platforms, 'nginx_dir': self.nginx_dir,
            'state_file': os.path.join(root, 'state', 'state.json'), 'cert_dir': os.path.join(root, 'lego'),
            'webroot': os.path.join(root, 'acme'), 'acme_email': 'ops@example.com', 'lego_cmd': 'unused',
            'edge': {'ipv4': '203.0.113.10', 'ipv6': '2001:db8::10'}, 'resolvers': ['1.1.1.1', '8.8.8.8'],
        }
        self.dns({})

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def _platform(self, name, host, zone):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakePlatform)
        server.db, server.base, server.domains, server.reports = name, host, [], []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        env_file = os.path.join(self.tmp.name, name + '.env')
        with open(env_file, 'w') as fh:
            fh.write('OTHER=1\nNC_CONFIG_SYNC_KEY=%s\n' % MASTER)
        return {'name': name, 'database': name, 'host': host, 'env_file': env_file,
                'internal_url': 'http://127.0.0.1:%d' % server.server_address[1], 'upstream': '127.0.0.1:8069',
                'bus': '127.0.0.1:8072', 'snippet_dir': '/etc/nginx/%s/snippets' % name, 'zone_prefix': zone,
                '_server': server}

    def dns(self, records, **flags):
        state = {'dns': records}
        state.update(flags)
        with open(self.fake_state, 'w') as fh:
            json.dump(state, fh)

    def good_dns(self, fqdn, token):
        return {'TXT _nc-verify.%s' % fqdn: ['nc-verify=' + token], 'A %s' % fqdn: ['203.0.113.10'],
                'AAAA %s' % fqdn: []}

    def calls(self, tool):
        with open(self.fake_state) as fh:
            return [c for c in json.load(fh).get('calls', []) if c[0] == tool]

    def vhost(self, fqdn):
        path = os.path.join(self.nginx_dir, 'nc-domain-%s.conf' % fqdn)
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return fh.read()

    def want(self, platform, *items):
        platform['_server'].domains = list(items)

    def item(self, fqdn, db='acme', state='ssl_pending', token='tok-123'):
        return {'fqdn': fqdn, 'database': db, 'state': state, 'token': token if state == 'ssl_pending' else None,
                'primary': False, 'redirect': 'alias'}

    def run_agent(self):
        conf = {k: v for k, v in self.conf.items()}
        conf['platforms'] = [{k: v for k, v in p.items() if k != '_server'} for p in self.platforms]
        return agent.run_once(conf)

    def reports(self, platform):
        return [r for body in platform['_server'].reports for r in body['results']]

    # ------------------------------------------------------------------ tests
    def test_verified_domain_is_issued_pinned_and_reported_active(self):
        self.dns(self.good_dns('erp.customer.com', 'tok-123'))
        self.want(self.platforms[0], self.item('erp.customer.com'))
        self.run_agent()
        block = self.vhost('erp.customer.com')
        self.assertIn('server_name erp.customer.com;', block)
        self.assertIn('proxy_set_header X-NC-Tenant-DB "acme";', block)
        self.assertIn('ssl_certificate', block)
        self.assertIn('limit_req zone=bc_login', block)
        self.assertEqual(len(self.calls('lego')), 1)
        result = self.reports(self.platforms[0])[0]
        self.assertTrue(result['ok'])
        self.assertEqual(result['expiry'], '2099-02-17')
        self.assertTrue(os.path.exists(os.path.join(self.nginx_dir, agent.COMMON_FILE)))

    def test_missing_ownership_token_issues_nothing(self):
        records = self.good_dns('erp.customer.com', 'someone-else')
        self.dns(records)
        self.want(self.platforms[0], self.item('erp.customer.com'))
        self.run_agent()
        self.assertIsNone(self.vhost('erp.customer.com'))
        self.assertEqual(self.calls('lego'), [])
        result = self.reports(self.platforms[0])[0]
        self.assertFalse(result['ok'])
        self.assertIn('ownership', result['error'])

    def test_domain_pointing_elsewhere_or_also_elsewhere_is_refused(self):
        for addresses in (['198.51.100.7'], ['203.0.113.10', '198.51.100.7']):
            self.platforms[0]['_server'].reports = []
            records = self.good_dns('erp.customer.com', 'tok-123')
            records['A erp.customer.com'] = addresses
            self.dns(records)
            os.path.exists(self.conf['state_file']) and os.remove(self.conf['state_file'])
            self.want(self.platforms[0], self.item('erp.customer.com'))
            self.run_agent()
            self.assertIsNone(self.vhost('erp.customer.com'))
            self.assertFalse(self.reports(self.platforms[0])[0]['ok'])
        self.assertEqual(self.calls('lego'), [])

    def test_same_hostname_on_two_platforms_is_refused_on_both(self):
        self.dns(self.good_dns('erp.customer.com', 'tok-123'))
        self.want(self.platforms[0], self.item('erp.customer.com', db='acme'))
        self.want(self.platforms[1], self.item('erp.customer.com', db='other'))
        self.run_agent()
        self.assertIsNone(self.vhost('erp.customer.com'))
        for platform in self.platforms:
            result = self.reports(platform)[0]
            self.assertFalse(result['ok'])
            self.assertTrue(result['final'])
            self.assertIn('another platform', result['error'])

    def test_first_claim_wins_and_a_later_claim_by_another_platform_is_refused(self):
        self.dns(self.good_dns('erp.customer.com', 'tok-123'))
        self.want(self.platforms[0], self.item('erp.customer.com', db='acme'))
        self.run_agent()
        self.assertIn('"acme"', self.vhost('erp.customer.com'))
        self.want(self.platforms[0], self.item('erp.customer.com', db='acme', state='active'))
        self.want(self.platforms[1], self.item('erp.customer.com', db='other'))
        self.run_agent()
        self.assertIn('"acme"', self.vhost('erp.customer.com'))
        self.assertNotIn('"other"', self.vhost('erp.customer.com'))
        self.assertFalse(self.reports(self.platforms[1])[-1]['ok'])

    def test_a_neighbouring_site_or_the_platform_namespace_is_never_taken(self):
        for fqdn in ('shop.neighbour.com', 'evil.balancecloud.ae', 'balancecloud.ae'):
            self.dns(self.good_dns(fqdn, 'tok-123'))
            self.want(self.platforms[1], self.item(fqdn))
            self.run_agent()
            self.assertIsNone(self.vhost(fqdn), fqdn)
            self.assertIn('another site', self.reports(self.platforms[1])[-1]['error'])
        self.assertEqual(self.calls('lego'), [])

    def test_malformed_names_from_a_platform_are_ignored(self):
        self.dns({})
        self.want(self.platforms[0], self.item('erp.customer.com', db='acme"; return 200 "x'),
                  self.item('bad name.com'), self.item('*.wild.com'))
        self.run_agent()
        self.assertEqual([f for f in os.listdir(self.nginx_dir) if f.startswith('nc-domain-')], [])
        self.assertEqual(self.reports(self.platforms[0]), [])

    def test_nginx_rejection_leaves_no_block_and_is_reported(self):
        self.dns(self.good_dns('erp.customer.com', 'tok-123'), nginx_fail=True)
        self.want(self.platforms[0], self.item('erp.customer.com'))
        self.run_agent()
        self.assertIsNone(self.vhost("erp.customer.com"))
        result = self.reports(self.platforms[0])[0]
        self.assertFalse(result["ok"])
        self.assertIn("nginx -t refused", result["error"])
        self.assertTrue(os.path.exists(os.path.join(self.nginx_dir, agent.COMMON_FILE)))

    def test_failed_issuance_backs_off_then_becomes_final(self):
        self.dns(self.good_dns('erp.customer.com', 'tok-123'), lego_fail=True)
        self.want(self.platforms[0], self.item('erp.customer.com'))
        self.run_agent()
        self.run_agent()  # inside the back-off window: no second CA request
        self.assertEqual(len(self.calls('lego')), 1)
        first = self.reports(self.platforms[0])[0]
        self.assertFalse(first['ok'])
        self.assertFalse(first['final'])
        state = agent.load_state(self.conf['state_file'])
        state['attempts']['erp.customer.com']['count'] = agent.MAX_ATTEMPTS - 1
        state['attempts']['erp.customer.com']['last'] = '2000-01-01T00:00:00'
        agent.save_state(self.conf['state_file'], state)
        self.run_agent()
        self.assertTrue(self.reports(self.platforms[0])[-1]['final'])

    def test_https_probe_must_pass_before_active(self):
        self.dns(self.good_dns('erp.customer.com', 'tok-123'), https='502')
        self.want(self.platforms[0], self.item('erp.customer.com'))
        self.run_agent()
        result = self.reports(self.platforms[0])[0]
        self.assertFalse(result['ok'])
        self.assertIn('/web/health', result['error'])

    def test_disabled_domain_is_retired_but_an_unreachable_platform_keeps_its_domains(self):
        self.dns(self.good_dns('erp.customer.com', 'tok-123'))
        self.want(self.platforms[0], self.item('erp.customer.com'))
        self.run_agent()
        self.assertIsNotNone(self.vhost('erp.customer.com'))
        self.platforms[0]['internal_url'] = 'http://127.0.0.1:9'  # platform down
        self.want(self.platforms[0])
        self.run_agent()
        self.assertIsNotNone(self.vhost('erp.customer.com'), 'an outage must never unpublish a domain')
        self.platforms[0]['internal_url'] = 'http://127.0.0.1:%d' % self.servers[0].server_address[1]
        self.run_agent()  # reachable again, domain no longer desired (disabled)
        self.assertIsNone(self.vhost('erp.customer.com'))
        self.assertTrue(os.path.exists(os.path.join(self.conf['cert_dir'], 'certificates', 'erp.customer.com.crt')),
                        'certificates are kept, never revoked')

    def test_wrong_key_is_refused_by_the_platform(self):
        with open(self.platforms[0]['env_file'], 'w') as fh:
            fh.write('NC_CONFIG_SYNC_KEY=not-the-master\n')
        self.want(self.platforms[0], self.item('erp.customer.com'))
        self.run_agent()
        self.assertIsNone(self.vhost('erp.customer.com'))

    def test_collision_helper(self):
        names = {'.balancecloud.ae', 'shop.neighbour.com', '*.wild.com', '_'}
        self.assertEqual(agent.collides('balancecloud.ae', names), '.balancecloud.ae')
        self.assertEqual(agent.collides('x.balancecloud.ae', names), '.balancecloud.ae')
        self.assertEqual(agent.collides('a.wild.com', names), '*.wild.com')
        self.assertIsNone(agent.collides('notbalancecloud.ae', names))
        self.assertIsNone(agent.collides('erp.customer.com', names))


if __name__ == '__main__':
    unittest.main(verbosity=2)
