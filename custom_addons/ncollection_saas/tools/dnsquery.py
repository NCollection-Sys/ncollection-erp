# -*- coding: utf-8 -*-
"""A minimal, dependency-free DNS client for custom-domain verification (TXT / A / AAAA / CNAME).

The Odoo image ships no dnspython and the project does not add pip packages to it, so the platform asks the resolver
itself: one UDP query (TCP retry when truncated) to the resolvers in /etc/resolv.conf, RFC 1035 wire format, name
compression handled, a hard timeout. It reads answers; it never caches, follows referrals or trusts the additional
section. The edge agent re-checks ownership with ``dig`` before a certificate is requested, so this is the first of two
independent checks, not the only one.
"""
import ipaddress

import random
import socket
import struct

TYPES = {'A': 1, 'CNAME': 5, 'TXT': 16, 'AAAA': 28}
_RCODE_NXDOMAIN = 3
_MAX_PACKET = 65535


class DnsError(Exception):
    """The lookup itself failed (timeout, SERVFAIL, malformed answer): not the same as 'no such record'."""


def _resolvers():
    servers = []
    try:
        with open('/etc/resolv.conf', encoding='ascii', errors='ignore') as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == 'nameserver':
                    servers.append(parts[1])
    except OSError:
        return ['127.0.0.11']  # Docker's embedded resolver: the container default
    return servers or ['127.0.0.11']


def _encode_name(name):
    out = b''
    for label in name.rstrip('.').split('.'):
        raw = label.encode('idna') if label else b''
        if not raw or len(raw) > 63:
            raise DnsError('invalid name')
        out += bytes([len(raw)]) + raw
    return out + b'\x00'


def _read_name(packet, offset, depth=0):
    labels = []
    while True:
        if offset >= len(packet) or depth > 20:
            raise DnsError('malformed name')
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack('!H', packet[offset:offset + 2])[0] & 0x3FFF
            name, _ = _read_name(packet, pointer, depth + 1)
            labels.append(name)
            return '.'.join(x for x in labels if x), offset + 2
        if length == 0:
            return '.'.join(labels), offset + 1
        labels.append(packet[offset + 1:offset + 1 + length].decode('ascii', 'replace'))
        offset += 1 + length


def _parse(packet, qid, qtype):
    if len(packet) < 12:
        raise DnsError('short answer')
    rid, flags, qd, an, _ns, _ar = struct.unpack('!HHHHHH', packet[:12])
    if rid != qid:
        raise DnsError('id mismatch')
    rcode = flags & 0x0F
    if rcode == _RCODE_NXDOMAIN:
        return [], flags
    if rcode:
        raise DnsError('rcode %d' % rcode)
    offset = 12
    for _ in range(qd):
        _, offset = _read_name(packet, offset)
        offset += 4
    answers = []
    for _ in range(an):
        _, offset = _read_name(packet, offset)
        rtype, _rclass, _ttl, rdlen = struct.unpack('!HHIH', packet[offset:offset + 10])
        offset += 10
        rdata = packet[offset:offset + rdlen]
        if rtype == qtype or (rtype == TYPES['CNAME'] and qtype != TYPES['TXT']):
            if rtype == TYPES['A'] and rdlen == 4:
                answers.append(('A', str(ipaddress.IPv4Address(rdata))))
            elif rtype == TYPES['AAAA'] and rdlen == 16:
                answers.append(('AAAA', str(ipaddress.IPv6Address(rdata))))
            elif rtype == TYPES['CNAME']:
                answers.append(('CNAME', _read_name(packet, offset)[0].lower()))
            elif rtype == TYPES['TXT']:
                parts, pos = [], 0
                while pos < len(rdata):
                    size = rdata[pos]
                    parts.append(rdata[pos + 1:pos + 1 + size].decode('utf-8', 'replace'))
                    pos += 1 + size
                answers.append(('TXT', ''.join(parts)))
        offset += rdlen
    return answers, flags


def query(name, rtype, timeout=4.0, servers=None):
    """[(type, value)] for ``name``; [] when the name or the record does not exist; DnsError when the lookup failed."""
    qtype = TYPES[rtype]
    qid = random.SystemRandom().randint(0, 0xFFFF)
    question = struct.pack('!HHHHHH', qid, 0x0100, 1, 0, 0, 0) + _encode_name(name) + struct.pack('!HH', qtype, 1)
    last = None
    for server in (servers or _resolvers()):
        family = socket.AF_INET6 if ':' in server else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(question, (server, 53))
                packet = sock.recv(_MAX_PACKET)
            answers, flags = _parse(packet, qid, qtype)
            if flags & 0x0200:  # truncated: ask again over TCP
                with socket.create_connection((server, 53), timeout=timeout) as sock:
                    sock.sendall(struct.pack('!H', len(question)) + question)
                    size = struct.unpack('!H', _recv_exact(sock, 2))[0]
                    answers, _ = _parse(_recv_exact(sock, size), qid, qtype)
            return answers
        except (OSError, DnsError, struct.error) as exc:
            last = exc
    raise DnsError(str(last) if last else 'no resolver')


def _recv_exact(sock, size):
    data = b''
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise DnsError('connection closed')
        data += chunk
    return data


def txt_values(name, **kw):
    return [value for kind, value in query(name, 'TXT', **kw) if kind == 'TXT']


def addresses(name, **kw):
    """(ipv4 set, ipv6 set, cname or '') following at most one CNAME the resolver already flattened into the answer."""
    a_answers = query(name, "A", **kw)
    v4 = {value for kind, value in a_answers if kind == "A"}
    v6 = {value for kind, value in query(name, "AAAA", **kw) if kind == "AAAA"}
    cnames = [value for kind, value in a_answers if kind == "CNAME"]
    return v4, v6, (cnames[0] if cnames else '')
