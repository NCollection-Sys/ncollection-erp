# -*- coding: utf-8 -*-
"""Authenticated encryption of provider credentials. No Odoo import.

THE SCHEME IS NOT INVENTED HERE. The platform already derives per-tenant secrets from ONE master that lives only in the
container environment: ``HMAC-SHA256(master, label || tenant)`` for config-sync and for the AI gateway
(ARCHITECTURE_SECURITY, ncollection_ai/models/gateway_client.py). This module reuses that idea for a REVERSIBLE secret,
which the repository did not have (inbound keys are hash-only):

    key  = HKDF-SHA256(master, info = label || db || company || provider || key_version)      (32 bytes)
    blob = base64( nonce(12) || AES-256-GCM(key, nonce, plaintext, aad = db|company|provider|name|version) )

* the master (``NC_EINVOICE_SECRET_KEY``) is NEVER stored in a database; unset ⇒ the feature refuses, never falls back;
* the key depends on the tenant database name and the company, and so does the AAD: a ciphertext copied into another
  tenant or company, or moved to another provider / secret name, fails authentication instead of decrypting;
* nothing about the plaintext (length aside) can be derived from the stored blob, and no fingerprint of it is kept;
* errors never contain the plaintext, the key or the master.

``cryptography`` (AES-GCM, HKDF) is already in the Odoo image: no new dependency."""

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MASTER_ENV = 'NC_EINVOICE_SECRET_KEY'
_LABEL = b'nc-einvoice-secret:'
_NONCE_BYTES = 12


class SecretError(Exception):
    """Never carries secret material."""


MASTER_MIN_LENGTH = 32  # characters: a 256-bit key as hex, base64 or a long random string


def master_from_env():
    value = os.environ.get(MASTER_ENV, '')
    if len(value) < MASTER_MIN_LENGTH:
        raise SecretError('the e-invoicing secret store is not configured on this deployment (%s)' % MASTER_ENV)
    return value.encode('utf-8')


def _context(db, company_id, provider, name, version):
    parts = (db, str(company_id), provider, name, str(version))
    if any(not part for part in parts):
        raise SecretError('incomplete secret context')
    # a JSON list, not a delimiter-joined string: no name can ever be mistaken for two fields
    return json.dumps(list(parts), ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _key(master, db, company_id, provider, version):
    info = _LABEL + _context(db, company_id, provider, '-', version)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(master)


def encrypt(master, plaintext, *, db, company_id, provider, name, version=1):
    if not plaintext:
        raise SecretError('an empty secret is not stored')
    nonce = os.urandom(_NONCE_BYTES)
    aad = _context(db, company_id, provider, name, version)
    blob = nonce + AESGCM(_key(master, db, company_id, provider, version)).encrypt(
        nonce, plaintext.encode('utf-8'), aad
    )
    return base64.b64encode(blob).decode('ascii')


def decrypt(master, blob, *, db, company_id, provider, name, version=1):
    try:
        raw = base64.b64decode(blob, validate=True)
        nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        aad = _context(db, company_id, provider, name, version)
        return AESGCM(_key(master, db, company_id, provider, version)).decrypt(nonce, ciphertext, aad).decode('utf-8')
    except (InvalidTag, ValueError, TypeError):
        raise SecretError('the stored secret cannot be decrypted in this context') from None


# ---------------------------------------------------------------------------------------------------------------------
# KEY ROTATION (R48). The master is versioned. The deployment holds the CURRENT master and, only while a rotation is in
# progress, the PREVIOUS one; both live in the container environment, never in a database:
#
#     NC_EINVOICE_SECRET_KEY            the current master     NC_EINVOICE_SECRET_KEY_VERSION   its version (default 1)
#     NC_EINVOICE_SECRET_KEY_PREVIOUS   the previous master       NC_EINVOICE_SECRET_KEY_PREVIOUS_VERSION   its version
#
# Every ciphertext records the version it was made with (``key_version`` is part of the key derivation AND of the AAD),
# so an old ciphertext stays decryptable with the previous master until it is re-encrypted with the current one, and a
# ciphertext presented under another version fails authentication instead of decrypting. Rotation never needs a human
# to see a credential: the service re-encrypts in-process (models/secret.py ``action_rotate_keys``).
VERSION_ENV = 'NC_EINVOICE_SECRET_KEY_VERSION'
PREVIOUS_ENV = 'NC_EINVOICE_SECRET_KEY_PREVIOUS'
PREVIOUS_VERSION_ENV = 'NC_EINVOICE_SECRET_KEY_PREVIOUS_VERSION'


def _version(name, default=None):
    raw = os.environ.get(name, '')
    if not raw:
        if default is None:
            raise SecretError('%s is required when a previous master is configured' % name)
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SecretError('%s must be a positive integer' % name) from None
    if value < 1:
        raise SecretError('%s must be a positive integer' % name)
    return value


def keyring_from_env():
    """``(current_version, {version: master})`` from the environment. The previous master is optional; when given it
    must have its own, different version and must not equal the current master (a 'rotation' to the same key would
    re-encrypt nothing useful and hide a configuration mistake)."""
    current = master_from_env()
    current_version = _version(VERSION_ENV, 1)
    ring = {current_version: current}
    previous = os.environ.get(PREVIOUS_ENV, '')
    if previous:
        if len(previous) < MASTER_MIN_LENGTH:
            raise SecretError('the previous e-invoicing master key is too short (%s)' % PREVIOUS_ENV)
        previous_version = _version(PREVIOUS_VERSION_ENV)
        if previous_version == current_version:
            raise SecretError('the previous and the current master key must have different versions')
        if previous.encode('utf-8') == current:
            raise SecretError('the previous and the current master key are the same key')
        ring[previous_version] = previous.encode('utf-8')
    return current_version, ring
