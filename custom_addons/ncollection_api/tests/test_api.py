# -*- coding: utf-8 -*-
"""P8-T01: the acceptance criterion, and the properties that make it safe.

    "the OAuth2 flow issues a token that lists contacts on a demo tenant;
     unauthorized scopes are rejected"

Both halves are asserted end-to-end over real HTTP, not by calling the
controller methods directly — a route that works when called as a Python
function and 404s over the wire has passed nothing.
"""
from odoo.tests import HttpCase, tagged

from odoo.addons.ncollection_api.models.api_token import NC_API_SCOPE


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
