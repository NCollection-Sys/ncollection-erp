# -*- coding: utf-8 -*-
"""The contract between the engine and an Accredited Service Provider (ASP) adapter. Pure: no Odoo, no network.

Why a contract and no ASP: NCollection is Corner 1 (Guidelines V2.0 §5.1). The ASP (Corner 2) transmits to the buyer's
ASP and reports the tax data to the FTA; the Person appoints ONE ASP for sending and receiving (§6.1). Which ASP a
tenant uses is the tenant's choice, so the core knows only this contract. A provider module
(``ncollection_uae_einvoicing_<asp>``) subclasses :class:`AdapterBase`, registers it with :func:`register`, and owns
every network call and every provider-specific format. The core never imports a provider module.

What the contract fixes:

* **Idempotency.** Every submission carries :func:`idempotency_key` (the e-invoice UUID, its revision and the sha256 of
  the exact XML): submitting the same document twice must yield the SAME provider document id and never a second
  document at the ASP.
* **Correlation.** The engine's correlation id travels with every request and must come back on every result and
  callback event, so a response is always attributable to one e-invoice.
* **Error classification.** Every failure is an :class:`AdapterError` with an :class:`ErrorCategory`; only TRANSIENT
  is retryable. Nothing is retried on a guess.
* **Callbacks.** An adapter verifies a callback's authenticity (signature, freshness, replay) BEFORE parsing it; an
  unverified callback is never turned into a state change.
* **Confirmations.** The ASP reports the tax data to the FTA (Guidelines V2.0 §5.1 steps 4, 7-11) and must tell the
  Person, event-driven, that documents were transmitted (App 4 §16.2.4); the contract carries those confirmations as
  :class:`CallbackEvent` / :class:`StatusResult` kinds. The engine never generates a Tax Data Document itself.
"""

import enum
import hashlib
from dataclasses import dataclass, field
from typing import Optional


class ErrorCategory(enum.Enum):
    TRANSIENT = 'transient'  # timeouts, 408, 429, 5xx, connection errors: retry with backoff
    PERMANENT = 'permanent'  # a failure that will repeat as is (bad request shape, unknown document)
    REJECTED = 'rejected'  # the ASP or the network refused the DOCUMENT (validation): correct and resubmit
    AUTHENTICATION = 'authentication'  # credentials refused or expired: an administrator must act
    CONFIGURATION = 'configuration'  # the adapter or the provider record is not usable as configured


RETRYABLE = frozenset({ErrorCategory.TRANSIENT})

SUBMISSION_STATUSES = ('accepted', 'rejected', 'failed')
EVENT_KINDS = ('submission', 'exchange', 'fta', 'mls', 'buyer_response')


@dataclass(eq=False)  # not frozen: the interpreter writes __traceback__ / __context__ on a raised exception
class AdapterError(Exception):
    category: ErrorCategory
    code: str
    message: str
    http_status: Optional[int] = None
    retry_after: Optional[int] = None  # seconds, when the provider says so

    @property
    def retryable(self):
        return self.category in RETRYABLE

    def __str__(self):
        return '%s/%s: %s' % (self.category.value, self.code, self.message)


@dataclass(frozen=True)
class SubmissionRequest:
    einvoice_uuid: str
    revision: int
    document_kind: str  # 'invoice' | 'credit_note'
    type_code: str
    xml: bytes
    xml_sha256: str
    idempotency_key: str
    correlation_id: str
    seller_endpoint: str  # '<scheme>:<id>'
    buyer_endpoint: str


@dataclass(frozen=True)
class SubmissionResult:
    status: str  # one of SUBMISSION_STATUSES
    correlation_id: str
    idempotency_key: str
    asp_document_id: Optional[str] = None
    received_at: Optional[str] = None  # ISO 8601, as reported by the provider
    error: Optional[AdapterError] = None
    response_sha256: Optional[str] = None  # digest of the raw provider response, for the audit trail
    duplicate: bool = False  # True when the provider recognised an earlier submission of the same key

    @property
    def retryable(self):
        return bool(self.error and self.error.retryable)


@dataclass(frozen=True)
class CallbackEvent:
    kind: str  # one of EVENT_KINDS
    status: str
    correlation_id: Optional[str]
    asp_document_id: Optional[str]
    occurred_at: Optional[str]
    message_id: str  # the provider's unique id of this event: duplicates are ignored by it
    metadata: dict = field(default_factory=dict)
    payload_sha256: Optional[str] = None


@dataclass(frozen=True)
class StatusResult:
    asp_document_id: str
    correlation_id: Optional[str]
    events: tuple  # of CallbackEvent, oldest first


def idempotency_key(einvoice_uuid, revision, xml_sha256):
    """Deterministic: the same document revision with the same XML always gives the same key."""
    if not (einvoice_uuid and xml_sha256) or not isinstance(revision, int):
        raise ValueError('uuid, revision and xml sha256 are required')
    return hashlib.sha256(('%s|%d|%s' % (einvoice_uuid, revision, xml_sha256)).encode('utf-8')).hexdigest()


def classify_http_status(status, provider_code=None):
    """The default classification of an HTTP answer; an adapter may refine it with the provider's own codes."""
    if status is None:
        return AdapterError(ErrorCategory.TRANSIENT, provider_code or 'no-response', 'no response from the provider')
    if status in (408, 425, 429) or 500 <= status <= 599:
        return AdapterError(ErrorCategory.TRANSIENT, provider_code or str(status), 'temporary provider failure',
                            http_status=status)
    if status in (401, 403):
        return AdapterError(ErrorCategory.AUTHENTICATION, provider_code or str(status), 'credentials refused',
                            http_status=status)
    if status in (400, 409, 422):
        return AdapterError(ErrorCategory.REJECTED, provider_code or str(status), 'document refused',
                            http_status=status)
    return AdapterError(ErrorCategory.PERMANENT, provider_code or str(status), 'unexpected provider answer',
                        http_status=status)


class AdapterBase:
    """What every ASP adapter implements. ``provider`` is the ``ncollection.einvoice.provider`` record (read only);
    ``secrets`` is a callable ``secrets(name) -> str`` that decrypts one of the provider's stored secrets on demand
    (the adapter never sees the master key and must never log, store or return a secret)."""

    code = None  # the registration key; also the value of ``ncollection.einvoice.provider.adapter``

    def __init__(self, provider, secrets):
        self.provider = provider
        self.secrets = secrets

    def submit(self, request):
        """Send ONE document. Returns a :class:`SubmissionResult`; never raises for a provider answer (an answer is a
        result with ``error`` set). Must be idempotent on ``request.idempotency_key``."""
        raise NotImplementedError

    def fetch_status(self, asp_document_id, correlation_id=None):
        """Poll the provider for what happened to a document. Returns a :class:`StatusResult` or raises
        :class:`AdapterError`."""
        raise NotImplementedError

    def verify_callback(self, headers, body):
        """True only for an authentic, fresh, not-replayed callback. Must not raise for a bad callback."""
        raise NotImplementedError

    def parse_callback(self, headers, body):
        """The events of a VERIFIED callback, as a list of :class:`CallbackEvent`. Raises :class:`AdapterError`
        (PERMANENT) for a malformed body; never any other exception."""
        raise NotImplementedError

    def classify_error(self, http_status=None, provider_code=None, exception=None):
        """An :class:`AdapterError` for a failure; the default uses :func:`classify_http_status`."""
        if exception is not None and http_status is None:
            return AdapterError(ErrorCategory.TRANSIENT, type(exception).__name__, 'transport failure')
        return classify_http_status(http_status, provider_code)


_REGISTRY = {}


def register(adapter_class):
    """Register an adapter class under its ``code`` (done by a provider module at import time)."""
    code = getattr(adapter_class, 'code', None)
    if not code or not issubclass(adapter_class, AdapterBase):
        raise ValueError('an adapter needs a code and must subclass AdapterBase')
    if code in _REGISTRY and _REGISTRY[code] is not adapter_class:
        raise ValueError('adapter code %r is already registered' % code)
    _REGISTRY[code] = adapter_class
    return adapter_class


def unregister(code):
    _REGISTRY.pop(code, None)


def get(code):
    """The adapter class for ``code``; AdapterError CONFIGURATION when none is registered."""
    if code not in _REGISTRY:
        raise AdapterError(ErrorCategory.CONFIGURATION, 'adapter-not-registered',
                           'no adapter is registered for %r' % (code,))
    return _REGISTRY[code]
