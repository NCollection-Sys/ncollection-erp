# -*- coding: utf-8 -*-
"""The retention / offboarding evidence package (R46): a deterministic ZIP with a checksum manifest. No Odoo import.

MD 243/2025 Art 11 (as amended by MD 197/2026) and Guidelines V2.0 §5.4 require a Person to retain its Electronic
Invoices, Electronic Credit Notes and associated data electronically, so that they stay intact, accessible and
reproducible for the retention period. This module turns what the engine archived into ONE file that proves that,
independently of this system:

    package.json            what the package is: tenant database, company, counts, the evidence it contains
    MANIFEST.sha256         ``<sha256>  <path>`` for every other file, sorted
    einvoices/<name>/...    record.json (identity, states, payload, findings, history, submissions, attempts, events)
                            and every archived XML of the record (``xml-<n>-<sha256 prefix>.xml``, superseded too)
    inbound/<id>/...        record.json (facts, states, history) and the received XML

DETERMINISTIC: the same evidence gives the same bytes (sorted entries, fixed ZIP timestamps, sorted JSON keys, no
"generated at" inside the package; when and by whom it was produced is recorded OUTSIDE it, on the export record).
SAFE: every path component is reduced to ``[A-Za-z0-9._-]`` (no traversal, no absolute path, no drive letter), and
:func:`verify` refuses any entry that is not plain, any duplicate, any file not in the manifest and any mismatch.
"""

import hashlib
import io
import json
import re
import zipfile

MANIFEST = 'MANIFEST.sha256'
HEADER = 'package.json'
FORMAT = 'ncollection-uae-einvoice-evidence/1'
_FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # ZIP needs a timestamp; a fixed one keeps the package byte-reproducible
_SAFE = re.compile(r'[^A-Za-z0-9._-]+')
MAX_ENTRIES = 1_000_000


class PackageError(ValueError):
    pass


def safe_part(value):
    """One path component: never empty, never '.' or '..', only ``[A-Za-z0-9._-]``."""
    part = _SAFE.sub('-', str(value)).strip('.-') or '_'
    return part[:120]


def dumps(data):
    return (json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1, default=str) + '\n').encode('utf-8')


def build(files, header):
    """``files``: {path: bytes}. Returns ``(zip_bytes, manifest_sha256)``."""
    for path in files:
        _check_path(path)
        if path in (MANIFEST, HEADER):
            raise PackageError('reserved path %s' % path)
    lines = ['%s  %s' % (hashlib.sha256(files[path]).hexdigest(), path) for path in sorted(files)]
    manifest = ('\n'.join(lines) + '\n').encode('ascii')
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    header = dict(header, format=FORMAT, files=len(files), manifest_sha256=manifest_sha)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in [(HEADER, dumps(header)), (MANIFEST, manifest)] + sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue(), manifest_sha


def _check_path(path):
    if not path or path.startswith(('/', '\\')) or '\\' in path or ':' in path:
        raise PackageError('unsafe path %r' % path)
    if any(part in ('', '.', '..') for part in path.split('/')):
        raise PackageError('unsafe path %r' % path)


def verify(zip_bytes, max_bytes=2 * 1024 ** 3):
    """``(header, problems)``: every file present, listed once, and matching its checksum (empty problems = intact).

    Refuses (as problems, never by extracting anything) traversal or absolute paths, duplicate names, links and
    oversized content, so it is safe on a package received from elsewhere."""
    problems = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return None, ['not a ZIP file']
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        return None, ['too many entries']
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        problems.append('duplicate entries')
    total = 0
    for info in infos:
        try:
            _check_path(info.filename)
        except PackageError as exc:
            problems.append(str(exc))
        if (info.external_attr >> 16) & 0o170000 not in (0, 0o100000):
            problems.append('not a plain file: %s' % info.filename)
        total += info.file_size
    if total > max_bytes:
        return None, problems + ['the package is larger than allowed']
    if problems:
        return None, problems
    if MANIFEST not in names or HEADER not in names:
        return None, ['the manifest or the header is missing']
    manifest = archive.read(MANIFEST)
    header = json.loads(archive.read(HEADER))
    if header.get('manifest_sha256') != hashlib.sha256(manifest).hexdigest():
        problems.append('the header does not match the manifest')
    listed = {}
    for line in manifest.decode('ascii').splitlines():
        digest, _sep, path = line.partition('  ')
        listed[path] = digest
    content = set(names) - {MANIFEST, HEADER}
    for path in sorted(content - set(listed)):
        problems.append('unlisted: %s' % path)
    for path, digest in sorted(listed.items()):
        if path not in content:
            problems.append('missing: %s' % path)
        elif hashlib.sha256(archive.read(path)).hexdigest() != digest:
            problems.append('changed: %s' % path)
    if header.get('files') != len(listed):
        problems.append('the header counts %s files, the manifest lists %s' % (header.get('files'), len(listed)))
    return header, problems
