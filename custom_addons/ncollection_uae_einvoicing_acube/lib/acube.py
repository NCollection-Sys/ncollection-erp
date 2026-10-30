# -*- coding: utf-8 -*-
"""The A-Cube (Platform ACube Technologies LLC, MoF accreditation 188961) adapter for the UAE e-invoicing engine.

BUILT AGAINST THE PUBLISHED CONTRACT, NOT AGAINST A LIVE SANDBOX. Every endpoint, field and status below comes from
A-Cube's public OpenAPI 3.1 document ``https://docs.acubeapi.com/openapi/gov-ae-api.json`` (fetched 2026-10-29,
sha256 ``858d1060…ddff``) and its public guides (authentication, lifecycle, webhooks, receive). Nothing here was
exercised against the provider: no credential exists yet. Each assumption the published contract does not settle is
marked ``SANDBOX-VERIFY`` and listed in docs/markdown/UAE_EINVOICING.md §9f; until a sandbox run confirms them, the
release stays blocked (decision 2026-10-28).

WHAT THE CONTRACT SAYS (quoted facts, not guesses)
* Hosts: sandbox ``https://ae-sandbox.api.acubeapi.com``, production ``https://ae.api.acubeapi.com``; login
  ``POST https://common.api.acubeapi.com/login`` with ``email`` / ``password`` / ``environment``; a JWT valid 24 hours,
  sent as ``Authorization: Bearer``.
* Outgoing: ``POST /invoices/outgoing`` and ``POST /credit-notes/outgoing`` take the PINT AE UBL XML as the body
  (``application/xml``); 201 = created (``invoiceId``), 400 / 422 = refused (``violations``), 404 = legal entity not
  found. Document types include ``self-billed-invoice`` / ``self-billed-credit-note`` (389 / 261).
* Status: ``GET /invoices/{uuid}`` / ``GET /credit-notes/{uuid}``; ``status`` is one of new, received, valid,
  sending_started, sending_queued, sending_failed, processed, delivered, delivery_failed, accepted, rejected,
  withdrawn, action_required. ``GET …/{uuid}/tdd`` downloads the Tax Data Document (200) or has none yet (204 / 404).
* Inbound: ``GET /invoices?direction=incoming`` (paged), the original XML at ``GET …/{uuid}/source``.
* Webhooks (event destinations): events ``document.delivered`` / ``document.received``; retried 15 times; secured ONLY
  by a static token the subscriber chooses, sent in a header (no signature, no timestamp, no nonce).

DESIGN CONSEQUENCES
* A webhook is a TRIGGER, never a fact: an unsigned body could be replayed or forged by anyone who learns the token, so
  :meth:`parse_callback` uses the body only to learn WHICH document to look at, and the states come from the API itself
  (:meth:`fetch_status`, authenticated with our credentials). A replayed or forged callback can therefore only cause a
  harmless re-read; event ids are deterministic per (document, state), so a repeat is a duplicate the engine ignores.
* Idempotency: the published API has no idempotency key. Before POSTing, the adapter looks for an outgoing document of
  this legal entity with the same document number and type (``SANDBOX-VERIFY``: the list endpoint has no number
  filter, so it pages); a hit is returned as ``duplicate=True`` with the existing id. A 409 / duplicate answer is
  treated the same way.
* The base URL is chosen from two CONSTANTS by the provider's environment; no URL is ever taken from configuration or
  from a provider answer, and redirects are not followed, so the adapter cannot be pointed elsewhere (SSRF).
* Secrets (``email``, ``password``, ``webhook_token``) are read through the engine's secret reader on demand; the
  token is cached in memory only, never logged, never stored.
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from odoo.addons.ncollection_uae_einvoicing.lib import adapter_contract as contract

HOSTS = {'sandbox': 'https://ae-sandbox.api.acubeapi.com', 'production': 'https://ae.api.acubeapi.com'}
LOGIN_URL = 'https://common.api.acubeapi.com/login'
CALLBACK_HEADER = 'X-NC-Webhook-Token'
TIMEOUT = 30
TOKEN_MARGIN = 600  # renew 10 minutes before the JWT's own expiry
PAGE_SIZE = 100
MAX_INBOUND_BYTES = 10 * 1024 * 1024  # = lib/inbound_parse.MAX_BYTES of the engine
CREDIT_TYPES = {'381', '81', '261'}
KINDS = {'invoices': 'invoice', 'credit-notes': 'credit_note'}

# A-Cube status -> engine events. Only what the published contract makes unambiguous is mapped; every other status is
# reported as an unmapped event the engine stores and does not apply (SANDBOX-VERIFY: accepted / rejected / withdrawn /
# action_required have no published meaning yet, so they move NOTHING).
STATUS_EVENTS = {
    'delivered': ('exchange', 'delivered'),
    'delivery_failed': ('exchange', 'rejected'),
}

# (database, provider id, environment, credential fingerprint) -> (token, expires_at). Process memory only. The
# DATABASE
# is part of the key because the shared tenant runner serves many tenant databases in one process, where two tenants'
# provider records can carry the same id: without it one tenant could reuse another tenant's A-Cube token.
_TOKENS = {}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # any provider answer larger than this is refused, never buffered further
MAX_DUPLICATE_PAGES = 50  # the duplicate check scans at most this many pages, then fails SAFE (no blind send)


class HttpResponse:
    def __init__(self, status, body, headers=None):
        self.status, self.body, self.headers = status, body or b'', headers or {}

    def json(self):
        return json.loads(self.body.decode('utf-8'))


def requests_transport(method, url, headers=None, body=None, timeout=TIMEOUT):
    """The real transport (``requests`` is in the Odoo image). Never follows a redirect."""
    import requests

    try:
        # verify=True: TLS certificates are always checked (stated, not left to a default); no redirect is followed;
        # the body is read in chunks and refused past MAX_RESPONSE_BYTES
        response = requests.request(method, url, headers=headers or {}, data=body, timeout=timeout,
                                    allow_redirects=False, verify=True, stream=True)
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                response.close()
                raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'response-too-large',
                                            'the provider answer is larger than accepted')
            chunks.append(chunk)
    except requests.RequestException as exc:
        raise contract.AdapterError(contract.ErrorCategory.TRANSIENT, type(exc).__name__,
                                    'the provider could not be reached') from None
    return HttpResponse(response.status_code, b''.join(chunks), dict(response.headers))


def _jwt_expiry(token):
    try:
        payload = token.split('.')[1]
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        return float(data['exp'])
    except (IndexError, KeyError, TypeError, ValueError):
        return time.time() + 3600  # an unreadable expiry: renew within the hour rather than trust 24h


class AcubeAdapter(contract.AdapterBase):
    code = 'acube'

    def __init__(self, provider, secrets, transport=None, clock=time.time):
        super().__init__(provider, secrets)
        self.transport = transport or requests_transport
        self.clock = clock
        environment = getattr(provider, 'nc_acube_environment', None) or 'sandbox'
        if environment not in HOSTS:
            raise contract.AdapterError(contract.ErrorCategory.CONFIGURATION, 'environment',
                                        'unknown A-Cube environment')
        self.environment = environment
        self.base = HOSTS[environment]

    # ------------------------------------------------------------------ authentication
    def _database(self):
        env = getattr(self.provider, 'env', None)
        return env.cr.dbname if env is not None else getattr(self.provider, 'database', None)

    def _token(self, renew=False):
        email = self.secrets('email')
        key = (self._database(), getattr(self.provider, 'id', None), self.environment,
               hashlib.sha256(email.encode('utf-8')).hexdigest()[:16])
        cached = _TOKENS.get(key)
        if cached and not renew and cached[1] - TOKEN_MARGIN > self.clock():
            return cached[0]
        body = json.dumps({'email': email, 'password': self.secrets('password'),
                           'environment': self.environment}).encode('utf-8')
        response = self.transport('POST', LOGIN_URL, {'Content-Type': 'application/json',
                                                      'Accept': 'application/json'}, body)
        if response.status in (400, 401, 403):
            _TOKENS.pop(key, None)
            raise contract.AdapterError(contract.ErrorCategory.AUTHENTICATION, 'login-refused',
                                        'A-Cube refused the credentials', http_status=response.status)
        if response.status != 200:
            raise self.classify_error(http_status=response.status, provider_code='login')
        try:
            token = response.json()['token']
        except (ValueError, KeyError, TypeError):
            raise contract.AdapterError(contract.ErrorCategory.TRANSIENT, 'login-malformed',
                                        'the login answer is malformed') from None
        _TOKENS[key] = (token, _jwt_expiry(token))
        return token

    def _call(self, method, path, body=None, content_type=None, query=None, accept='application/json'):
        url = self.base + path + ('?' + urlencode(query, doseq=True) if query else '')
        for attempt in (1, 2):
            headers = {'Authorization': 'Bearer ' + self._token(renew=attempt == 2), 'Accept': accept}
            if content_type:
                headers['Content-Type'] = content_type
            response = self.transport(method, url, headers, body)
            if response.status == 401 and attempt == 1:
                continue  # an expired / revoked token: log in once more, then give up
            return response
        return response

    # ------------------------------------------------------------------ outbound
    @staticmethod
    def _collection(type_code):
        return 'credit-notes' if str(type_code) in CREDIT_TYPES else 'invoices'

    @staticmethod
    def _document_id(collection, uuid):
        return '%s:%s' % (collection, uuid)

    @staticmethod
    def _split(asp_document_id):
        collection, _sep, uuid = (asp_document_id or '').partition(':')
        if collection not in KINDS or not uuid:
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'unknown-document',
                                        'not an A-Cube document id')
        return collection, uuid

    @staticmethod
    def _uuid_of(xml):
        from lxml import etree

        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        try:
            return etree.fromstring(xml, parser=parser).findtext(
                '{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}UUID')
        except etree.XMLSyntaxError:
            return None

    def _number_and_owner(self, request):
        from lxml import etree

        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        root = etree.fromstring(request.xml, parser=parser)
        number = root.findtext('{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}ID')
        # the OWNER of an outgoing document is the sender: the seller, or the BUYER of a self-billed document
        owner = (request.buyer_endpoint if request.self_billed else request.seller_endpoint).split(':', 1)[-1]
        return number, owner

    def _find_existing(self, request, collection):
        """The provider id of THIS document if A-Cube already holds it, else None. A-Cube has no idempotency key, so
        the proof is the document itself: a candidate must have the same owner, direction, type, number AND recipient,
        and its stored XML (``/source``) must carry OUR ``cbc:UUID`` (engine-generated, unique per revision). A number
        alone is not unique (another revision, another counterparty), so a number match never suppresses a send.

        FAIL SAFE: when the check cannot be completed (the list or the source cannot be read, or more than
        MAX_DUPLICATE_PAGES full pages) it raises TRANSIENT, so the submission is retried later and never POSTed blind
        (SANDBOX-VERIFY: the real paging shape and whether a filter by number exists)."""
        number, owner = self._number_and_owner(request)
        ours = self._uuid_of(request.xml)
        recipient = (request.seller_endpoint if request.self_billed else request.buyer_endpoint).split(':', 1)[-1]
        for page in range(1, MAX_DUPLICATE_PAGES + 1):
            response = self._call('GET', '/' + collection, query={'direction': 'outgoing', 'tin': owner,
                                                                  'page': page, 'itemsPerPage': PAGE_SIZE})
            if response.status != 200:
                raise self._inconclusive(response.status)
            items = self._items(response)
            for item in items:
                if (item.get('invoiceNumber') != number
                        or str(item.get('documentTypeCode')) != str(request.type_code)
                        or str(item.get('recipientIdentifier') or '').split(':', 1)[-1] != recipient
                        or not item.get('invoiceId')):
                    continue
                source = self._call('GET', '/%s/%s/source' % (collection, item['invoiceId']),
                                    accept='application/xml')
                if source.status != 200:
                    raise self._inconclusive(source.status)
                if ours and self._uuid_of(source.body) == ours:
                    return item['invoiceId']
            if len(items) < PAGE_SIZE:
                return None
        raise self._inconclusive(None)

    @staticmethod
    def _inconclusive(status):
        return contract.AdapterError(contract.ErrorCategory.TRANSIENT, 'duplicate-check-inconclusive',
                                     'whether A-Cube already holds this document could not be established',
                                     http_status=status)

    @staticmethod
    def _items(response):
        try:
            items = response.json()
        except ValueError:
            raise contract.AdapterError(contract.ErrorCategory.TRANSIENT, 'list-malformed',
                                        'the list answer is malformed') from None
        if isinstance(items, dict):
            items = items.get('hydra:member', items.get('member', []))
        return [item for item in (items or []) if isinstance(item, dict)]

    def submit(self, request):
        collection = self._collection(request.type_code)
        digest = None
        try:
            existing = self._find_existing(request, collection)
            if existing:
                return contract.SubmissionResult(
                    status='accepted', correlation_id=request.correlation_id,
                    idempotency_key=request.idempotency_key, asp_document_id=self._document_id(collection, existing),
                    duplicate=True)
            response = self._call('POST', '/%s/outgoing' % collection, body=request.xml,
                                  content_type='application/xml')
            digest = hashlib.sha256(response.body).hexdigest()
        except contract.AdapterError as exc:
            return contract.SubmissionResult(status='failed', correlation_id=request.correlation_id,
                                             idempotency_key=request.idempotency_key, error=exc)
        if response.status == 201:
            try:
                uuid = response.json()['invoiceId']
            except (ValueError, KeyError, TypeError):
                # the document may exist at the provider: TRANSIENT, so the retry first looks it up (_find_existing)
                return self._failed(request, contract.AdapterError(
                    contract.ErrorCategory.TRANSIENT, 'created-unreadable', 'created, but the answer is unreadable'),
                    digest)
            return contract.SubmissionResult(
                status='accepted', correlation_id=request.correlation_id, idempotency_key=request.idempotency_key,
                asp_document_id=self._document_id(collection, uuid), response_sha256=digest)
        if response.status in (400, 422):
            return contract.SubmissionResult(
                status='rejected', correlation_id=request.correlation_id, idempotency_key=request.idempotency_key,
                error=contract.AdapterError(contract.ErrorCategory.REJECTED, self._violation_code(response),
                                            'A-Cube refused the document%s' % self._violation_path(response),
                                            http_status=response.status),
                response_sha256=digest)
        if response.status == 404:
            return self._failed(request, contract.AdapterError(
                contract.ErrorCategory.CONFIGURATION, 'legal-entity-not-found',
                'the legal entity is not onboarded at A-Cube', http_status=404), digest)
        error = self.classify_error(http_status=response.status)
        retry_after = response.headers.get('Retry-After') if response.headers else None
        if retry_after and str(retry_after).isdigit():
            error = contract.AdapterError(error.category, error.code, error.message, error.http_status,
                                          int(retry_after))
        return self._failed(request, error, digest)

    @staticmethod
    def _failed(request, error, digest=None):
        return contract.SubmissionResult(status='failed', correlation_id=request.correlation_id,
                                         idempotency_key=request.idempotency_key, error=error,
                                         response_sha256=digest)

    @staticmethod
    def _violation_path(response):
        """Where A-Cube located the first violation (``propertyPath``): structure only, never the message text,
        and only when it looks like a path, so no document data can ride along into the attempt row."""
        try:
            path = str((response.json().get('violations') or [{}])[0].get('propertyPath') or '')
        except (ValueError, AttributeError, IndexError, TypeError):
            return ''
        return ' at %s' % path[:120] if path and all(ch.isalnum() or ch in '._[]/:-' for ch in path) else ''

    @staticmethod
    def _violation_code(response):
        """The first violation code, if any: provider codes are kept, never their free text (it may echo data)."""
        try:
            violations = response.json().get('violations') or []
            return str(violations[0].get('code') or 'violation')[:64]
        except (ValueError, AttributeError, IndexError, TypeError):
            return 'refused'

    # ------------------------------------------------------------------ status
    def fetch_status(self, asp_document_id, correlation_id=None):
        collection, uuid = self._split(asp_document_id)
        response = self._call('GET', '/%s/%s' % (collection, uuid))
        if response.status != 200:
            raise self.classify_error(http_status=response.status)
        try:
            status = str(response.json().get('status') or '')
        except (ValueError, AttributeError):
            raise contract.AdapterError(contract.ErrorCategory.TRANSIENT, 'status-malformed',
                                        'the status answer is malformed') from None
        events = []
        kind, mapped = STATUS_EVENTS.get(status, ('exchange', 'acube:' + status))
        events.append(contract.CallbackEvent(
            kind=kind, status=mapped, correlation_id=correlation_id, asp_document_id=asp_document_id,
            occurred_at=None, message_id='acube:%s:%s' % (uuid, status), metadata={'acube_status': status}))
        tdd = self._call('GET', '/%s/%s/tdd' % (collection, uuid), accept='application/xml')
        if tdd.status == 200 and tdd.body:
            sha = hashlib.sha256(tdd.body).hexdigest()
            events.append(contract.CallbackEvent(
                kind='fta', status='reported', correlation_id=correlation_id, asp_document_id=asp_document_id,
                occurred_at=None, message_id='acube:%s:tdd:%s' % (uuid, sha[:16]),
                metadata={'tdd_reference': 'acube-tdd-sha256:' + sha}, payload_sha256=sha))
        return contract.StatusResult(asp_document_id=asp_document_id, correlation_id=correlation_id,
                                     events=tuple(events))

    # ------------------------------------------------------------------ callbacks
    def verify_callback(self, headers, body):
        """The static token A-Cube sends (event destination ``authType=header``), compared in constant time."""
        try:
            expected = self.secrets('webhook_token')
        except contract.AdapterError:
            return False
        given = ''
        for name, value in (headers or {}).items():
            if str(name).lower() == CALLBACK_HEADER.lower():
                given = str(value)
        return bool(expected) and len(expected) >= 32 and hmac.compare_digest(given.encode(), expected.encode())

    def parse_callback(self, headers, body):
        """A TRIGGER: which document to re-read. The states come from :meth:`fetch_status` (authenticated)."""
        try:
            data = json.loads(body.decode('utf-8'))
            event_type = str(data.get('eventType') or data.get('event') or '')
            uuid = str(data.get('invoiceUuid') or data.get('documentId') or data.get('uuid') or '')
            document_type = str(data.get('documentType') or '')
        except (ValueError, AttributeError, UnicodeDecodeError):
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'malformed', 'malformed callback') from None
        if not uuid or len(uuid) > 64 or not all(ch.isalnum() or ch == '-' for ch in uuid):
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'malformed', 'no document id')
        collection = 'credit-notes' if 'credit' in document_type else 'invoices'
        asp_id = self._document_id(collection, uuid)
        if event_type == 'document.received':
            return [contract.CallbackEvent(kind='inbound', status='received', correlation_id=None,
                                           asp_document_id=asp_id, occurred_at=None,
                                           message_id='acube:%s:received' % uuid)]
        if event_type == 'document.delivered':
            return list(self.fetch_status(asp_id).events)
        return []

    # ------------------------------------------------------------------ inbound
    def fetch_inbound(self, asp_document_id):
        collection, uuid = self._split(asp_document_id)
        meta = self._call('GET', '/%s/%s' % (collection, uuid))
        if meta.status != 200:
            raise self.classify_error(http_status=meta.status)
        data = meta.json()
        if data.get('direction') != 'incoming':
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'not-incoming',
                                        'the document is not an incoming one')
        source = self._call('GET', '/%s/%s/source' % (collection, uuid), accept='application/xml')
        if source.status != 200 or not source.body:
            raise self.classify_error(http_status=source.status)
        if len(source.body) > MAX_INBOUND_BYTES:
            # the same ceiling the engine's parser applies, checked BEFORE anything is archived
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'inbound-too-large',
                                        'the received document is larger than accepted')
        return contract.InboundDocument(
            asp_document_id=asp_document_id, message_id='acube:%s' % uuid, xml=source.body,
            sender_endpoint=data.get('senderIdentifier'), receiver_endpoint=data.get('recipientIdentifier'),
            received_at=None)

    def list_inbound(self, cursor=None):
        """Every incoming document id on the pages from ``cursor`` (``"<invoice page>,<credit-note page>"``); the
        engine dedupes on the id, so re-reading the last page is harmless."""
        try:
            pages = [max(1, int(part)) for part in (cursor or '1,1').split(',')][:2]
        except ValueError:
            pages = [1, 1]
        ids, next_pages = [], []
        for collection, page in zip(('invoices', 'credit-notes'), pages):
            response = self._call('GET', '/' + collection, query={'direction': 'incoming', 'page': page,
                                                                  'itemsPerPage': PAGE_SIZE})
            if response.status != 200:
                raise self.classify_error(http_status=response.status)
            items = self._items(response)
            ids.extend(self._document_id(collection, str(item['invoiceId'])) for item in items
                       if item.get('invoiceId'))
            next_pages.append(page + 1 if len(items) >= PAGE_SIZE else page)
        return ids, '%d,%d' % tuple(next_pages)


contract.register(AcubeAdapter)
