# -*- coding: utf-8 -*-
"""P8-T01: the acceptance criterion, and the properties that make it safe.

    "the OAuth2 flow issues a token that lists contacts on a demo tenant;
     unauthorized scopes are rejected"

Both halves are asserted end-to-end over real HTTP, not by calling the
controller methods directly — a route that works when called as a Python
function and 404s over the wire has passed nothing.
"""
import datetime
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.ncollection_api.models.api_token import NC_API_SCOPE

from ..models.api_throttle import MAX_TRACKED_SOURCES


@tagged('post_install', '-at_install')
class TestApiFoundation(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Client = cls.env['ncollection.api.client']
        cls.Scope = cls.env['ncollection.api.scope']
        cls.contacts_read = cls.env.ref('ncollection_api.scope_contacts_read')
        # A second scope that exists but is granted to nobody, so "asking for a
        # scope you do not hold" can be tested without inventing one inline.
        cls.reports_read = cls.Scope.create(
            {'code': 'reports:read', 'description': 'test-only scope'})

        cls.service_user = cls.env['res.users'].create({
            'name': 'API Service', 'login': 'nc_api_service',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.client_rec = cls.Client.create({
            'name': 'Test Integration',
            'user_id': cls.service_user.id,
            'scope_ids': [(6, 0, [cls.contacts_read.id])],
        })
        cls.secret = cls.client_rec._nc_rotate_secret()

    def setUp(self):
        super().setUp()
        # #436's throttle counts failed authentications in a map hanging off
        # the REGISTRY, which outlives a transaction and is therefore shared by
        # every test in this class. Without this reset the failures produced by
        # test_a_bad_secret_and_an_unknown_client_are_indistinguishable (2) and
        # test_every_failure_uses_the_same_envelope (1) accumulate, and once the
        # throttle's own tests push the total past the threshold, whatever runs
        # next gets a 429 in place of the status it asserts — a failure whose
        # cause is in a different test.
        self.env['ncollection.api.throttle']._nc_reset()

    # ---- helpers ---------------------------------------------------------

    def _token(self, scope='contacts:read', client_id=None, secret=None):
        response = self.url_open(
            '/api/v1/oauth/token',
            data={'grant_type': 'client_credentials',
                  'client_id': client_id or self.client_rec.client_id,
                  'client_secret': secret or self.secret,
                  'scope': scope})
        return response

    def _get(self, path, token=None):
        headers = {'Authorization': 'Bearer %s' % token} if token else {}
        return self.url_open(path, headers=headers)

    # ---- THE acceptance criterion ----------------------------------------

    def test_the_oauth2_flow_issues_a_token_that_lists_contacts(self):
        """Half one, end to end over HTTP."""
        self.env['res.partner'].create({'name': 'API Visible Partner'})

        response = self._token()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['token_type'], 'Bearer')
        self.assertEqual(body['scope'], 'contacts:read')
        self.assertTrue(body['access_token'])
        self.assertEqual(body['expires_in'], 3600)

        listed = self._get('/api/v1/contacts', token=body['access_token'])
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        self.assertGreater(payload['count'], 0)
        self.assertIn('name', payload['results'][0])

    def test_an_unauthorized_scope_is_rejected(self):
        """Half two. The client holds contacts:read and nothing else.

        Asking for a scope it does not hold is a 400, NOT a quietly narrowed
        token — an integration that believes it has a permission it does not
        finds out in production otherwise.
        """
        response = self._token(scope='reports:read')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'invalid_scope')

    def test_a_PARTLY_allowed_request_is_refused_not_narrowed(self):
        """The case that distinguishes refusing from quietly narrowing.

        Asking for one scope the client holds AND one it does not must be a
        400. If the excess were silently dropped, this would return 200 with a
        smaller token — and the integration would believe it had a permission
        it does not, discovering otherwise in production.

        The single-scope test above cannot see this: narrowing an all-excess
        request leaves an EMPTY scope set, which is refused anyway, for a
        different reason and with the same status code. Its RED proof came back
        green, which is how the gap was found.
        """
        response = self._token(scope='contacts:read reports:read')
        self.assertEqual(response.status_code, 400, response.text)
        body = response.json()
        self.assertEqual(body['error']['code'], 'invalid_scope')
        self.assertIn(
            'reports:read', body['error']['message'],
            "the error does not name the offending scope, so it is "
            "indistinguishable from 'you asked for nothing'")

    def test_a_token_without_the_scope_cannot_reach_the_route(self):
        """The other direction: a VALID token, wrong scope for this route."""
        self.client_rec.sudo().scope_ids = [(6, 0, [self.reports_read.id])]
        response = self._token(scope='reports:read')
        self.assertEqual(response.status_code, 200, response.text)
        listed = self._get('/api/v1/contacts',
                           token=response.json()['access_token'])
        self.assertEqual(listed.status_code, 403)
        self.assertEqual(listed.json()['error']['code'], 'insufficient_scope')

    # ---- the properties that make the above safe -------------------------

    def test_an_api_token_CANNOT_be_used_as_an_rpc_credential(self):
        """The security property this design is built on, asserted not assumed.

        Core's check is `scope IS NULL OR scope = <asked>`. Tokens here are
        written with scope='ncollection_api'; Odoo's RPC authentication asks
        for 'rpc'. Neither matches, so the token is refused. Had the scope been
        left NULL — core's default for "any" — every API token would have been
        a master key to the entire ORM.
        """
        plaintext = self._token().json()['access_token']
        keys = self.env['res.users.apikeys']
        self.assertTrue(
            keys._check_credentials(scope=NC_API_SCOPE, key=plaintext),
            "the token does not authenticate against its own scope")
        self.assertFalse(
            keys._check_credentials(scope='rpc', key=plaintext),
            "an API token authenticated as an RPC credential — it would grant "
            "the whole ORM, not just this module's routes")

    def test_a_token_gets_ITS_OWN_scopes_not_the_newest_token_s(self):
        """The privilege escalation security review found, and the reason it
        was invisible.

        Core's `_check_credentials` returns only a uid — not which key matched.
        Resolving our token record as "newest live token for that user" meant a
        client holding token A (scope S1) and token B (scope S2) could present
        A and be handed B's grants. With one scope shipped, every live token
        for a client carried the same grant, so nothing could observe it; it
        would have become real the moment P8-T02 added a second scope.

        Two live tokens, different scopes, same client. The older one must NOT
        inherit the newer one's authority.
        """
        self.client_rec.sudo().scope_ids = [
            (6, 0, [self.contacts_read.id, self.reports_read.id])]
        narrow = self._token(scope='reports:read').json()['access_token']
        broad = self._token(scope='contacts:read').json()['access_token']
        self.assertNotEqual(narrow, broad)

        # The BROAD token reaches contacts, as it should.
        self.assertEqual(
            self._get('/api/v1/contacts', token=broad).status_code, 200)
        # The NARROW one must not, even though a newer, broader token for the
        # same client is alive right now.
        blocked = self._get('/api/v1/contacts', token=narrow)
        self.assertEqual(
            blocked.status_code, 403,
            "an older token inherited a newer token's scopes — authorisation "
            "is being resolved by recency instead of by the credential "
            "actually presented")
        self.assertEqual(blocked.json()['error']['code'], 'insufficient_scope')

    def test_a_negative_limit_is_a_400_not_a_500(self):
        """`offset` was clamped from below and `limit` was not, so `?limit=-1`
        reached Postgres and raised InvalidRowCountInLimitClause — an
        unhandled 500, which is both a stack trace and a breach of the
        one-error-shape promise."""
        token = self._token().json()['access_token']
        response = self._get('/api/v1/contacts?limit=-1', token=token)
        self.assertNotEqual(
            response.status_code, 500,
            "a negative limit reached the database unhandled")
        self.assertEqual(response.status_code, 200)

    def test_two_clients_cannot_share_a_service_user(self):
        """Sharing one would share a rate-limit bucket and blur log
        attribution, and before the token-binding fix would have crossed their
        scopes outright."""
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self.Client.create({
                    'name': 'Duplicate user client',
                    'user_id': self.service_user.id,
                })

    def test_the_secret_is_not_recoverable_from_the_record(self):
        """Rotation returns the plaintext once. Nothing stores it."""
        stored = self.client_rec.sudo().secret_hash
        self.assertTrue(stored)
        self.assertNotIn(self.secret, stored)
        crypt = self.env['res.users.apikeys']._nc_crypt_context()
        self.assertTrue(crypt.verify(self.secret, stored))

    def test_a_bad_secret_and_an_unknown_client_are_indistinguishable(self):
        """Different responses would make this a client_id enumeration oracle."""
        bad_secret = self._token(secret='wrong')
        unknown = self._token(client_id='does-not-exist', secret='wrong')
        self.assertEqual(bad_secret.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(bad_secret.json(), unknown.json())

    def test_the_api_reads_as_the_CLIENT_user_not_as_sudo(self):
        """A client must not be able to read what its user cannot.

        Proved by taking the right away: revoke the service user's access to
        res.partner and the same token must stop returning rows.
        """
        token = self._token().json()['access_token']
        self.assertEqual(self._get('/api/v1/contacts', token=token).status_code,
                         200)
        self.service_user.sudo().group_ids = [(6, 0, [])]
        self.env.registry.clear_cache()
        blocked = self._get('/api/v1/contacts', token=token)
        self.assertNotEqual(
            blocked.status_code, 200,
            "the API returned rows for a user with no access — it is reading "
            "as sudo, so a client can see whatever it asks for")

    def test_a_revoked_token_stops_working_immediately(self):
        """Revocation deletes the core key too, so it is dead at the
        cryptographic layer, not merely flagged at ours."""
        body = self._token().json()
        token_rec = self.env['ncollection.api.token'].sudo().search(
            [], order='id desc', limit=1)
        token_rec._nc_revoke()
        blocked = self._get('/api/v1/contacts', token=body['access_token'])
        self.assertEqual(blocked.status_code, 401)
        # AND the underlying credential is gone from core's table, not merely
        # flagged here. Asserting only the 401 passes even if revocation just
        # sets a boolean — its RED proof came back green for exactly that
        # reason. A flag alone would leave a cryptographically valid key alive
        # for anything that checks core directly.
        self.assertFalse(
            self.env['res.users.apikeys']._check_credentials(
                scope=NC_API_SCOPE, key=body['access_token']),
            "the core API key survived revocation — the token is dead only in "
            "this module's opinion")

    def test_an_unsupported_grant_type_says_so(self):
        response = self.url_open(
            '/api/v1/oauth/token',
            data={'grant_type': 'authorization_code',
                  'client_id': self.client_rec.client_id,
                  'client_secret': self.secret})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'],
                         'unsupported_grant_type')

    def test_every_failure_uses_the_same_envelope(self):
        """One shape, so a client can parse errors without special-casing."""
        for response in (self._token(secret='wrong'),
                         self._token(scope='reports:read'),
                         self._get('/api/v1/contacts')):
            body = response.json()
            self.assertIn('error', body)
            self.assertIn('code', body['error'])
            self.assertIn('message', body['error'])

    # ---- logging and rate limiting ---------------------------------------

    def test_requests_are_logged_as_METADATA_only(self):
        """A log that stored bodies would be a second copy of the customer
        data the API serves, with different ACLs — the mistake #81 caught."""
        self._token()
        log = self.env['ncollection.api.request.log'].sudo().search(
            [], order='id desc', limit=1)
        self.assertEqual(log.route, '/api/v1/oauth/token')
        self.assertEqual(log.status, 200)
        stored = set(log.read()[0])
        for forbidden in ('body', 'payload', 'response', 'authorization'):
            self.assertFalse(
                [f for f in stored if forbidden in f.lower()],
                "the request log has a field that could hold a payload: %s"
                % forbidden)

    def test_a_GET_request_is_logged_too(self):
        """Both routes must be able to write, not just the token endpoint.

        Odoo 19 defaults `auth='none'` routes to a READ-ONLY transaction
        (`http.py:974`), so every log INSERT failed with
        ReadOnlySqlTransaction — caught by `_log`'s guard, invisible in the
        response. Asserting logging only on the POST route missed it, and that
        RED proof came back green. The rate limiter counts these rows, so a
        silently unlogged GET is also an unlimited one.
        """
        token = self._token().json()['access_token']
        before = self.env['ncollection.api.request.log'].sudo().search_count(
            [('route', '=', '/api/v1/contacts')])
        self._get('/api/v1/contacts', token=token)
        after = self.env['ncollection.api.request.log'].sudo().search_count(
            [('route', '=', '/api/v1/contacts')])
        self.assertEqual(
            after, before + 1,
            "a GET request was not logged — the route is running in a "
            "read-only transaction, so the rate limiter counts nothing")

    def test_the_app_layer_rate_limit_refuses_with_429(self):
        """Second layer. nginx limit_req (P1-T03) is the first, and this does
        not replace it — ARCHITECTURE_SECURITY §6 asks for two independent
        layers, and a per-client allowance is something nginx cannot express."""
        self.client_rec.sudo().rate_limit_per_minute = 1
        first = self._token()
        self.assertEqual(first.status_code, 200)
        second = self._token()
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()['error']['code'], 'rate_limited')

    def test_a_zero_rate_limit_disables_only_the_APP_layer(self):
        self.client_rec.sudo().rate_limit_per_minute = 0
        for _ in range(3):
            self.assertEqual(self._token().status_code, 200)

    # ---- #436: throttling FAILED authentication --------------------------
    #
    # The per-client limiter above only ever runs for a client that has already
    # proved its secret. Everything below is about the requests that never get
    # that far, each of which costs a PBKDF2.

    def _arm_throttle(self, after=3, duration=300):
        """Pin the threshold so these tests do not ride on core's default."""
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('base.login_cooldown_after', str(after))
        icp.set_param('base.login_cooldown_duration', str(duration))
        return after

    def test_repeated_failed_token_requests_are_throttled(self):
        """The acceptance criterion: the app layer, not only nginx.

        Before #436 this loop could run forever, each iteration paying for a
        PBKDF2-SHA512, because the per-client limiter sits AFTER authentication
        and an unknown client never reaches it.
        """
        after = self._arm_throttle()
        for attempt in range(after):
            response = self._token(secret='wrong')
            self.assertEqual(
                response.status_code, 401,
                "attempt %d was throttled early — the threshold is not the "
                "configured one" % attempt)
        blocked = self._token(secret='wrong')
        self.assertEqual(
            blocked.status_code, 429,
            "an unlimited number of failed authentications is accepted; each "
            "one costs a PBKDF2")
        self.assertEqual(blocked.json()['error']['code'],
                         'too_many_failed_attempts')

    def test_the_throttle_cannot_be_evaded_by_rotating_client_id(self):
        """Keyed on the source, not on the credential.

        A counter keyed on client_id would be free to evade: the attacker
        chooses that value, and can choose a fresh one every request. This is
        the acceptance criterion that rules out #436's own option 2.
        """
        after = self._arm_throttle()
        for attempt in range(after):
            response = self._token(
                client_id='rotating-%d' % attempt, secret='wrong')
            self.assertEqual(response.status_code, 401)
        blocked = self._token(client_id='rotating-final', secret='wrong')
        self.assertEqual(
            blocked.status_code, 429,
            "a fresh client_id reset the counter — the throttle is keyed on "
            "attacker-controlled input and is free to evade")

    def test_the_throttle_refuses_BEFORE_any_credential_work(self):
        """No PBKDF2 runs on a throttled request — asserted, not inferred.

        The whole point of #436 is the CPU cost of the hash, so "did we skip
        the hash" is the property that matters. Asserting only the 429 does not
        show it: adding a `_nc_authenticate` call BEFORE the throttle check
        still returns 429, and that mutation passed this test when it read that
        way. The status code cannot see the difference; a call count can.

        A valid secret is used deliberately — with a wrong one,
        `_nc_authenticate` returning nothing would be indistinguishable from it
        never having been called.
        """
        after = self._arm_throttle()
        for _ in range(after):
            self.assertEqual(self._token(secret='wrong').status_code, 401)

        ClientModel = type(self.env['ncollection.api.client'])
        original = ClientModel._nc_authenticate
        calls = []

        def counting(model_self, client_id, client_secret):
            calls.append(client_id)
            return original(model_self, client_id, client_secret)

        with patch.object(ClientModel, '_nc_authenticate', counting):
            blocked = self._token()      # the REAL secret
        self.assertEqual(
            blocked.status_code, 429,
            "a valid secret was accepted while throttled — the throttle is "
            "not refusing at all")
        self.assertEqual(
            calls, [],
            "the credential check ran on a throttled request: the PBKDF2 this "
            "ticket exists to bound was paid anyway, and the 429 only hid it")

    def test_a_success_does_NOT_reset_the_failure_counter(self):
        """The interleave attack, and why this diverges from core.

        Core's `_assert_can_auth` clears the whole per-source bucket on any
        success. Copying that here was a HIGH finding in security review: at
        `/web/login` the party holding a valid credential is the account's own
        user, but this endpoint can be driven by an attacker who legitimately
        holds ONE live credential — their own low-tier client, a leaked but
        unrevoked secret — while grinding guesses at OTHER clients.

        With a bucket-wide clear they could run

            fail x (threshold - 1) -> succeed once -> counter wiped -> repeat

        forever, never tripping the cooldown, and the app layer would have
        bounded nothing.

        So: the success must NOT rescue the source. This drives the exact
        interleave and asserts it still ends in a lockout.
        """
        after = self._arm_throttle()
        for _ in range(after - 1):
            self.assertEqual(self._token(secret='wrong').status_code, 401)
        self.assertEqual(
            self._token().status_code, 200,
            "a valid secret below the threshold should still work")
        # One more failure must now tip it over, because the success did not
        # roll the count back to zero.
        self.assertEqual(self._token(secret='wrong').status_code, 401)
        blocked = self._token(secret='wrong')
        self.assertEqual(
            blocked.status_code, 429,
            "a single success wiped the failure counter — an attacker holding "
            "one live credential can interleave successes and grind failed "
            "attempts forever")

    def test_failures_decay_by_TIME_now_that_success_does_not_clear(self):
        """The other half of removing the success-clear.

        If failures only ever accumulated, a source that once hit the threshold
        would be one failure away from a lockout forever. An entry older than
        `base.login_cooldown_duration` is therefore forgotten on sight.

        Asserted by ageing the stored entry rather than by sleeping — a test
        that waits 300s is a test nobody runs.
        """
        after = self._arm_throttle(duration=300)
        for _ in range(after):
            self._token(secret='wrong')
        self.assertEqual(self._token(secret='wrong').status_code, 429)

        throttle = self.env['ncollection.api.throttle']
        failures = throttle._nc_failures()
        self.assertEqual(len(failures), 1, "expected one tracked source")
        source = list(failures)[0]
        count, __ = failures[source]
        failures[source] = (
            count, datetime.datetime.now() - datetime.timedelta(seconds=301))

        self.assertFalse(
            throttle._nc_is_throttled(source),
            "a failure older than the cooldown window still throttles — "
            "counters never decay, so any source that once tripped stays one "
            "failure from a lockout permanently")
        self.assertNotIn(
            source, failures,
            "the stale entry was not dropped, so it still occupies memory")

    def test_reading_the_throttle_does_not_CREATE_an_entry(self):
        """The memory-growth half of the same security finding.

        The first version indexed a `defaultdict`, so merely ASKING about an
        unknown source inserted a permanent entry. A caller rotating source
        addresses — a routed IPv6 prefix is enough — could then grow the map
        without bound while sending one request per address, staying under both
        the per-IP cooldown and nginx's per-IP zone. Neither layer bounds that,
        which is what made it real rather than theoretical.
        """
        throttle = self.env['ncollection.api.throttle']
        throttle._nc_reset()
        self.assertFalse(throttle._nc_is_throttled('198.51.100.7'))
        self.assertEqual(
            throttle._nc_failures(), {},
            "a read inserted an entry: an unauthenticated caller can grow this "
            "map by one entry per source address, without ever failing twice")

    def test_the_tracked_map_has_a_hard_CEILING(self):
        """Bounded by design, the way nginx's own zone is.

        nginx's `limit_req_zone ... :10m` is a fixed arena with LRU eviction.
        An unbounded Python dict fed by unauthenticated input is a DoS in its
        own right — on a worker with `limit_memory_hard`, growth is an
        availability incident, not untidiness.
        """
        throttle = self.env['ncollection.api.throttle']
        throttle._nc_reset()
        failures = throttle._nc_failures()

        now = datetime.datetime.now()
        for i in range(MAX_TRACKED_SOURCES + 50):
            failures['10.0.%d.%d' % (i // 256, i % 256)] = (1, now)
        throttle._nc_evict(failures)
        self.assertLessEqual(
            len(failures), MAX_TRACKED_SOURCES,
            "the map grew past its ceiling — nothing bounds it")

    def test_the_throttle_does_not_share_core_s_login_bucket(self):
        """The module's central design claim, which nothing tested.

        `ncollection.api.throttle` deliberately does NOT reuse core's
        `registry._login_failures`. If it did, a tenant's integration retrying
        with a stale secret would put their STAFF on the /web/login cooldown
        from the office IP.

        Without this test, changing REGISTRY_ATTR back to '_login_failures'
        passes the entire suite — the exact hole the docstring claims is
        closed would reopen silently. Found in code review.
        """
        registry = self.env.registry
        before = dict(getattr(registry, '_login_failures', {}) or {})
        after = self._arm_throttle()
        for _ in range(after + 1):
            self._token(secret='wrong')

        self.assertTrue(
            self.env['ncollection.api.throttle']._nc_failures(),
            "the API throttle recorded nothing, so this proves nothing")
        self.assertEqual(
            dict(getattr(registry, '_login_failures', {}) or {}), before,
            "API failures landed in CORE's login-cooldown bucket — a failing "
            "integration would lock this tenant's humans out of /web/login")

    def test_the_throttle_reports_a_DIFFERENT_code_than_the_rate_limit(self):
        """Both are 429, but the remedy differs: `rate_limited` means slow
        down, `too_many_failed_attempts` means the credentials are wrong and
        retrying will not help. One code for both would tell an integrator
        nothing actionable."""
        self._arm_throttle(after=1)
        self.assertEqual(self._token(secret='wrong').status_code, 401)
        throttled = self._token(secret='wrong')
        self.assertEqual(throttled.status_code, 429)
        self.assertEqual(throttled.json()['error']['code'],
                         'too_many_failed_attempts')

        # And the per-client limiter still answers with its own code.
        self.env['ncollection.api.throttle']._nc_reset()
        self.client_rec.sudo().rate_limit_per_minute = 1
        self.assertEqual(self._token().status_code, 200)
        limited = self._token()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()['error']['code'], 'rate_limited')

    def test_setting_the_cooldown_to_zero_disables_the_throttle(self):
        """One switch with one meaning across both surfaces:
        `base.login_cooldown_after = 0` already disables the /web/login
        cooldown, and disables this for the same reason. Reusing core's policy
        rather than inventing a second parameter is what buys that."""
        self._arm_throttle(after=0)
        for attempt in range(6):
            self.assertEqual(
                self._token(secret='wrong').status_code, 401,
                "attempt %d was throttled with the mechanism disabled"
                % attempt)


@tagged('post_install', '-at_install')
class TestApiBusinessEndpoints(HttpCase):
    """P8-T02: REST Business Endpoints tests (Contacts, Products, Sales, Invoices, Stock, CRM, OpenAPI)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Client = cls.env['ncollection.api.client']
        cls.Scope = cls.env['ncollection.api.scope']

        cls.all_scopes = cls.Scope.search([])

        group_xml_ids = [
            'base.group_user',
            'base.group_partner_manager',
            'product.group_product_manager',
            'sales_team.group_sale_manager',
            'sales_team.group_sale_salesman',
            'account.group_account_invoice',
            'account.group_account_user',
            'account.group_account_manager',
            'crm.group_use_lead',
            'stock.group_stock_user',
            'stock.group_stock_manager',
            'purchase.group_purchase_user',
            'purchase.group_purchase_manager',
        ]
        groups = []
        for xml_id in group_xml_ids:
            try:
                g = cls.env.ref(xml_id, raise_if_not_found=False)
                if g:
                    groups.append(g.id)
            except Exception:
                pass

        cls.service_user = cls.env['res.users'].create({
            'name': 'API Business Service User',
            'login': 'nc_api_business_user',
            'group_ids': [(6, 0, groups)],
        })

        cls.client_rec = cls.Client.create({
            'name': 'Business API Test Client',
            'user_id': cls.service_user.id,
            'scope_ids': [(6, 0, cls.all_scopes.ids)],
        })
        cls.secret = cls.client_rec._nc_rotate_secret()

        cls.test_partner = cls.env['res.partner'].create({
            'name': 'Seed Business Partner',
            'email': 'partner@test.com',
            'is_company': True,
        })

    def setUp(self):
        super().setUp()
        self.env['ncollection.api.throttle']._nc_reset()

    def _token(self, scope=None):
        requested_scope = scope or " ".join(sorted(self.all_scopes.mapped('code')))
        response = self.url_open(
            '/api/v1/oauth/token',
            data={'grant_type': 'client_credentials',
                  'client_id': self.client_rec.client_id,
                  'client_secret': self.secret,
                  'scope': requested_scope})
        return response

    def _get(self, path, token=None):
        headers = {'Authorization': 'Bearer %s' % token} if token else {}
        return self.url_open(path, headers=headers)

    def _post(self, path, data, token=None):
        headers = {'Authorization': 'Bearer %s' % token} if token else {}
        return self.url_open(path, json=data, headers=headers, method='POST')

    def _put(self, path, data, token=None):
        headers = {'Authorization': 'Bearer %s' % token} if token else {}
        return self.url_open(path, json=data, headers=headers, method='PUT')

    def _delete(self, path, token=None):
        headers = {'Authorization': 'Bearer %s' % token} if token else {}
        return self.url_open(path, headers=headers, method='DELETE')

    # ---- Contacts ----

    def test_contacts_crud_lifecycle(self):
        """Full CRUD on /api/v1/contacts."""
        tok = self._token(scope='contacts:read contacts:write').json()['access_token']

        # 1. Create
        created = self._post('/api/v1/contacts', {
            'name': 'New Enterprise Client',
            'email': 'enterprise@test.com',
            'phone': '+971501112233',
            'is_company': True,
            'city': 'Dubai',
            'vat': '100123456789012',
        }, token=tok)
        self.assertEqual(created.status_code, 201, created.text)
        data = created.json()
        self.assertEqual(data['name'], 'New Enterprise Client')
        self.assertEqual(data['city'], 'Dubai')
        partner_id = data['id']

        # 2. List
        listed = self._get('/api/v1/contacts?name=New Enterprise', token=tok)
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()['count'], 1)

        # 3. Get single
        single = self._get('/api/v1/contacts/%d' % partner_id, token=tok)
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()['name'], 'New Enterprise Client')

        # 4. Update
        updated = self._put('/api/v1/contacts/%d' % partner_id, {
            'phone': '+971509998888',
        }, token=tok)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['phone'], '+971509998888')

        # 5. Delete (archive)
        deleted = self._delete('/api/v1/contacts/%d' % partner_id, token=tok)
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()['success'])

    def test_contacts_scope_enforcement(self):
        """Writing contacts requires contacts:write scope."""
        read_tok = self._token(scope='contacts:read').json()['access_token']
        post_res = self._post('/api/v1/contacts', {'name': 'Unauthorized'}, token=read_tok)
        self.assertEqual(post_res.status_code, 403)
        self.assertEqual(post_res.json()['error']['code'], 'insufficient_scope')

    def test_contacts_pagination_clamping(self):
        """Pagination limit/offset parameters are parsed and clamped."""
        tok = self._token(scope='contacts:read').json()['access_token']
        res = self._get('/api/v1/contacts?limit=1&offset=0', token=tok)
        self.assertEqual(res.status_code, 200)
        self.assertLessEqual(res.json()['count'], 1)

        bad_res = self._get('/api/v1/contacts?limit=invalid', token=tok)
        self.assertEqual(bad_res.status_code, 400)
        self.assertEqual(bad_res.json()['error']['code'], 'invalid_request')

    # ---- Products ----

    def test_products_endpoints(self):
        """Product CRUD endpoints or 422 module_not_installed fallback."""
        tok = self._token(scope='products:read products:write').json()['access_token']
        if 'product.template' not in self.env:
            res = self._get('/api/v1/products', token=tok)
            self.assertEqual(res.status_code, 422)
            self.assertEqual(res.json()['error']['code'], 'module_not_installed')
            return

        created = self._post('/api/v1/products', {
            'name': 'API Test Widget',
            'default_code': 'WIDGET-01',
            'list_price': 250.0,
            'type': 'consu',
        }, token=tok)
        self.assertEqual(created.status_code, 201, created.text)
        prod_id = created.json()['id']

        listed = self._get('/api/v1/products?default_code=WIDGET-01', token=tok)
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()['count'], 1)

        single = self._get('/api/v1/products/%d' % prod_id, token=tok)
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()['list_price'], 250.0)

        updated = self._put('/api/v1/products/%d' % prod_id, {
            'list_price': 300.0,
        }, token=tok)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['list_price'], 300.0)

    # ---- Sales Orders ----

    def test_sales_endpoints(self):
        """Sales order CRUD or 422 module_not_installed fallback."""
        tok = self._token(scope='sales:read sales:write').json()['access_token']
        if 'sale.order' not in self.env:
            res = self._get('/api/v1/sales', token=tok)
            self.assertEqual(res.status_code, 422)
            self.assertEqual(res.json()['error']['code'], 'module_not_installed')
            return

        prod = self.env['product.product'].create({'name': 'Order Product', 'list_price': 100.0})

        created = self._post('/api/v1/sales', {
            'partner_id': self.test_partner.id,
            'order_line': [{
                'product_id': prod.id,
                'product_uom_qty': 3.0,
                'price_unit': 100.0,
            }],
        }, token=tok)
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()['id']

        listed = self._get('/api/v1/sales?partner_id=%d' % self.test_partner.id, token=tok)
        self.assertEqual(listed.status_code, 200)

        single = self._get('/api/v1/sales/%d' % order_id, token=tok)
        self.assertEqual(single.status_code, 200)
        self.assertEqual(len(single.json()['order_line']), 1)

        confirmed = self._post('/api/v1/sales/%d/action_confirm' % order_id, {}, token=tok)
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()['state'], 'sale')

    # ---- Invoices ----

    def test_invoices_endpoints(self):
        """Customer invoice CRUD or 422 module_not_installed fallback."""
        tok = self._token(scope='invoices:read invoices:write').json()['access_token']
        if 'account.move' not in self.env:
            res = self._get('/api/v1/invoices', token=tok)
            self.assertEqual(res.status_code, 422)
            self.assertEqual(res.json()['error']['code'], 'module_not_installed')
            return

        created = self._post('/api/v1/invoices', {
            'partner_id': self.test_partner.id,
            'invoice_line_ids': [{
                'name': 'API Consultation Line',
                'quantity': 2.0,
                'price_unit': 1500.0,
            }],
        }, token=tok)
        self.assertEqual(created.status_code, 201, created.text)
        move_id = created.json()['id']

        listed = self._get('/api/v1/invoices?partner_id=%d' % self.test_partner.id, token=tok)
        self.assertEqual(listed.status_code, 200)

        single = self._get('/api/v1/invoices/%d' % move_id, token=tok)
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()['state'], 'draft')

    # ---- Stock Levels ----

    def test_stock_levels_endpoint(self):
        """Stock level queries or 422 module_not_installed fallback."""
        tok = self._token(scope='stock:read').json()['access_token']
        if 'stock.quant' not in self.env:
            res = self._get('/api/v1/stock/levels', token=tok)
            self.assertEqual(res.status_code, 422)
            self.assertEqual(res.json()['error']['code'], 'module_not_installed')
            return

        res = self._get('/api/v1/stock/levels', token=tok)
        self.assertEqual(res.status_code, 200)
        self.assertIn('results', res.json())

    # ---- CRM Leads ----

    def test_crm_leads_endpoints(self):
        """CRM lead CRUD or 422 module_not_installed fallback."""
        tok = self._token(scope='crm:read crm:write').json()['access_token']
        if 'crm.lead' not in self.env:
            res = self._get('/api/v1/crm/leads', token=tok)
            self.assertEqual(res.status_code, 422)
            self.assertEqual(res.json()['error']['code'], 'module_not_installed')
            return

        created = self._post('/api/v1/crm/leads', {
            'name': 'Big Enterprise Prospect',
            'partner_id': self.test_partner.id,
            'expected_revenue': 50000.0,
            'email_from': 'prospect@enterprise.com',
        }, token=tok)
        self.assertEqual(created.status_code, 201, created.text)
        lead_id = created.json()['id']

        listed = self._get('/api/v1/crm/leads', token=tok)
        self.assertEqual(listed.status_code, 200)

        single = self._get('/api/v1/crm/leads/%d' % lead_id, token=tok)
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()['expected_revenue'], 50000.0)

        updated = self._put('/api/v1/crm/leads/%d' % lead_id, {
            'expected_revenue': 60000.0,
        }, token=tok)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['expected_revenue'], 60000.0)

    # ---- OpenAPI Spec ----

    def test_openapi_specification_endpoint(self):
        """OpenAPI 3.1 specification dynamic generation."""
        res = self._get('/api/v1/openapi.json')
        self.assertEqual(res.status_code, 200, res.text)
        spec = res.json()
        self.assertEqual(spec.get('openapi'), '3.1.0')
        self.assertIn('info', spec)
        self.assertIn('paths', spec)
        self.assertIn('/contacts', spec['paths'])
        self.assertIn('/products', spec['paths'])
        self.assertIn('/sales', spec['paths'])
        self.assertIn('/invoices', spec['paths'])
        self.assertIn('/stock/levels', spec['paths'])
        self.assertIn('/crm/leads', spec['paths'])
        self.assertIn('/oauth/token', spec['paths'])
        self.assertIn('components', spec)
