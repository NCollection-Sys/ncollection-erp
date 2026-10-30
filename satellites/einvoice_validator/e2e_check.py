#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end proof, run in the validator worker's environment (SaxonC-HE + lxml installed; Odoo NOT needed).

1. every OFFICIAL sample of the pinned release validates clean through the worker (so the worker and the rules agree);
2. documents BUILT BY THE ENGINE from payloads validate clean through the worker over HTTP, using the engine's own
client; 3. negative controls: removing/breaking something makes the matching official rule fail; 4. the worker refuses
what it must (DOCTYPE/entity, wrong release) and a tampered rule set stops it.

    docker run --rm -v <repo>/custom_addons/ncollection_uae_einvoicing:/addon:ro -v
    <repo>/satellites/einvoice_validator:/sat:ro \
        python:3.12-slim sh -c "pip install -q saxonche==13.1.0 lxml && python /sat/e2e_check.py"

Exit status 0 only if every expectation holds."""

import base64
import copy
import glob
import hashlib
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import types
from decimal import Decimal as D
from http.server import ThreadingHTTPServer

ADDON = os.environ.get('NC_ADDON_DIR', '/addon')
SAT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SAT)
import worker  # noqa: E402

# make `ncollection_uae_einvoicing.lib` / `.tests.payloads` importable WITHOUT importing Odoo (tests/__init__ needs it)
pkg = types.ModuleType('ncollection_uae_einvoicing')
pkg.__path__ = [ADDON]
sys.modules['ncollection_uae_einvoicing'] = pkg
tests_pkg = types.ModuleType('ncollection_uae_einvoicing.tests')
tests_pkg.__path__ = [os.path.join(ADDON, 'tests')]
sys.modules['ncollection_uae_einvoicing.tests'] = tests_pkg
from ncollection_uae_einvoicing.lib import adjustments, spec, validator  # noqa: E402
from ncollection_uae_einvoicing.lib.xml_builder import build  # noqa: E402
from ncollection_uae_einvoicing.tests import payloads  # noqa: E402

profile = spec.get_profile()
failures_total = []
TOKEN = secrets.token_hex(24)  # random per run: no secret literal in the source


def _required_real_cases():
    """REQUIRED_CASES of tests/test_real_move_crosscheck.py, read from its source (that module imports Odoo)."""
    import ast

    with open(os.path.join(ADDON, 'tests', 'test_real_move_crosscheck.py'), encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'REQUIRED_CASES' for t in node.targets):
            return ast.literal_eval(node.value)
    return {}


def expect(label, condition, detail=''):
    print('%-4s %s %s' % ('OK' if condition else 'FAIL', label, detail))
    if not condition:
        failures_total.append(label)


def main():
    rules_dir = profile.resource_dir
    srv_validator = worker.Validator(rules_dir, profile.pint_version, worker.SaxonTransformer())
    server = ThreadingHTTPServer(('127.0.0.1', 0), worker.make_handler(srv_validator, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    client = validator.HttpValidatorClient('http://127.0.0.1:%d' % port, timeout=60, token=TOKEN)
    print('engine:', srv_validator.engine, '| rules manifest sha256:', srv_validator.manifest_sha256[:16])
    # PINT AE Self-Billing: its own worker over its own pinned rule set (as in docker-compose.einvoice.yml)
    sb_profile = spec.get_profile(spec.SELF_BILLING_PROFILE_KEY)
    sb_validator = worker.Validator(sb_profile.resource_dir, sb_profile.pint_version, worker.SaxonTransformer())
    sb_server = ThreadingHTTPServer(('127.0.0.1', 0), worker.make_handler(sb_validator, TOKEN))
    threading.Thread(target=sb_server.serve_forever, daemon=True).start()
    sb_client = validator.HttpValidatorClient(
        'http://127.0.0.1:%d' % sb_server.server_address[1], timeout=60, token=TOKEN
    )
    print('self-billing rules manifest sha256:', sb_validator.manifest_sha256[:16])

    # ---- 1. official samples
    for kind, folder in (('invoice', 'invoice'), ('credit_note', 'creditnote')):
        for path in sorted(glob.glob(os.path.join(profile.examples_dir, folder, '*.xml'))):
            with open(path, 'rb') as handle:
                outcome = client.validate(handle.read(), kind, profile)
            fatal = [f.rule for f in outcome.failures if f.flag != 'warning']
            expect('official sample %s' % os.path.basename(path), outcome.ok, str(fatal))

    # ---- 1b. PINT AE Self-Billing: official samples, engine-built 389 / 261, and the two profiles kept apart
    for kind, folder in (('invoice', 'invoice'), ('credit_note', 'creditnote')):
        for path in sorted(glob.glob(os.path.join(sb_profile.examples_dir, folder, '*.xml'))):
            with open(path, 'rb') as handle:
                outcome = sb_client.validate(handle.read(), kind, sb_profile)
            fatal = [f.rule for f in outcome.failures if f.flag != 'warning']
            expect('official self-billing sample %s' % os.path.basename(path), outcome.ok, str(fatal))
    sb_invoice = build(payloads.standard_payload(type_code='389'), sb_profile)
    sb_credit = build(payloads.credit_note_payload(type_code='261'), sb_profile)
    for label, xml, kind in (('self-billed tax invoice 389', sb_invoice, 'invoice'),
                             ('self-billed tax credit note 261', sb_credit, 'credit_note')):
        outcome = sb_client.validate(xml, kind, sb_profile)
        expect('engine-built %s' % label, outcome.ok, str([f.rule for f in outcome.failures if f.flag != 'warning']))
    outcome = client.validate(sb_invoice, 'invoice', profile)
    expect('a 389 is refused by the BILLING rules (ibr-cl-01): the profiles are not interchangeable',
           not outcome.ok and 'ibr-cl-01' in {f.rule for f in outcome.failures})
    outcome = sb_client.validate(build(payloads.standard_payload(), profile), 'invoice', sb_profile)
    expect('a 380 is refused by the SELF-BILLING rules (ibr-cl-01)',
           not outcome.ok and 'ibr-cl-01' in {f.rule for f in outcome.failures})
    for label, bad, kind, rule in (
        ('self-billed 389 without the supplier VAT identifier', re.sub(
            rb'<cac:AccountingSupplierParty>(.*?)<cac:PartyTaxScheme>.*?</cac:PartyTaxScheme>',
            rb'<cac:AccountingSupplierParty>\1', sb_invoice, count=1, flags=re.S), 'invoice', 'ibr-134-ae'),
        ('self-billed 261 without its UUID', re.sub(rb'<cbc:UUID>[^<]*</cbc:UUID>\s*', b'', sb_credit, count=1),
         'credit_note', 'ibr-193-ae'),
    ):
        outcome = sb_client.validate(bad, kind, sb_profile)
        failed = {f.rule for f in outcome.failures if f.flag != 'warning'}
        expect('negative control: %s fails %s' % (label, rule), not outcome.ok and rule in failed,
               str(sorted(failed)[:6]))
    try:
        sb_client.validate(sb_invoice, 'invoice', profile)
        expect('the client refuses a Self-Billing answer as a Billing one (rule-set fingerprint)', False)
    except validator.ValidatorUnavailable:
        expect('the client refuses a Self-Billing answer as a Billing one (rule-set fingerprint)', True)

    # ---- 2. documents built by the engine
    def lines(*specs):
        return [payloads._line(i + 1, *s[:2], **s[2]) for i, s in enumerate(specs)]

    cases = {}
    cases['standard invoice'] = payloads.standard_payload()
    cases['credit note'] = payloads.credit_note_payload()
    cases['volume-discount credit note'] = payloads.credit_note_payload(credit_reason='VD', preceding=[])
    _items, adjs, found = adjustments.split_lines(
        [
            {
                'id': '2',
                'name': 'Rebate',
                'qty': D('1'),
                'price_unit': D('-100'),
                'net': D('-100'),
                'category': 'S',
                'rate': D('5'),
                'odoo_tax': D('-5'),
                'adjustment': 'allowance',
                'reason_code': '95',
                'reason': 'Discount',
            },
            {
                'id': '3',
                'name': 'Rush',
                'qty': D('1'),
                'price_unit': D('40'),
                'net': D('40'),
                'category': 'S',
                'rate': D('5'),
                'odoo_tax': D('2'),
                'adjustment': 'charge',
                'reason_code': 'AAC',
                'reason': 'Technical',
            },
        ]
    )
    assert not found, found
    cases['document allowance + charge'] = payloads.standard_payload(
        lines=[payloads._line(1, 1, '1000.00')], adjustments=adjs, odoo_totals=None
    )
    _items, e_adjs, found = adjustments.split_lines(
        [
            {
                'id': '2',
                'name': 'Rebate',
                'qty': D('1'),
                'price_unit': D('-10'),
                'net': D('-10'),
                'category': 'E',
                'rate': None,
                'exemption_code': 'DL8.46.1',
                'odoo_tax': D('0'),
                'adjustment': 'allowance',
                'reason_code': '95',
                'reason': 'Discount',
            }
        ]
    )
    assert not found, found
    cases['exempt document allowance (Percent 0)'] = payloads.standard_payload(
        type_code='480',
        lines=[payloads._line(1, 1, '100.00', category='E', exemption_code='DL8.46.1')],
        adjustments=e_adjs,
        odoo_totals=None,
    )
    cases['zero-rated'] = payloads.standard_payload(
        lines=[payloads._line(1, 1, '100.00', rate='0', category='Z')], odoo_totals=None
    )
    # DECISION 2026-10-28 / OCQ-20: an exempt-only document is 480, because fatal ibr-151-ae refuses it as a 380 (the
    # matching negative control is below); Guidelines V2.0 App 6 §18.3.1 disagrees and the conflict stays open
    cases['exempt only is 480 (fatal ibr-151-ae; OCQ-20)'] = payloads.standard_payload(
        type_code='480',
        lines=[payloads._line(1, 1, '100.00', category='E', exemption_code='DL8.46.1')],
        odoo_totals=None,
    )
    # Guidelines V2.0 App 6 §18.3.1: zero-rated supplies are not 480; a 'Z' line satisfies ibr-151-ae
    cases['exempt + zero-rated is 380 (App 6 §18.3.1, ibr-151-ae)'] = payloads.standard_payload(
        lines=[
            payloads._line(1, 1, '100.00', rate='0', category='Z'),
            payloads._line(2, 1, '40.00', category='E', exemption_code='DL8.46.1'),
        ],
        odoo_totals=None,
    )
    # an out-of-scope credit note (81): buyer legal registration present (ibr-136-ae), reason and original invoice
    cn_lines = [payloads._line(1, 1, '100.00', category='O', item_type='S', hs=None, sac='998311')]
    cases['out-of-scope credit note 81 (ibr-122-ae, ibr-136-ae)'] = payloads.credit_note_payload(
        type_code='81', lines=cn_lines, odoo_totals=None
    )
    # Guidelines V2.0 App 5 §17.1: the final invoice references the advance invoice in IBT-025 / IBT-026
    cases['final 380 referencing its advance invoice (App 5 §17.1)'] = payloads.standard_payload(
        preceding=[{'number': 'ADV-001', 'date': '2026-09-01'}]
    )
    # export (XXXXXXX1) to a buyer not on Peppol: predefined endpoint 9900000099 and the deliver-to address
    # (ibr-152-ae)
    export = payloads.standard_payload(
        flags={'export': True},
        transaction_type_code='00000001',
        lines=[payloads._line(1, 1, '100.00', rate='0', category='Z')],
        odoo_totals=None,
        delivery={'street': 'Gulf Road', 'city': 'Kuwait City', 'subdivision': 'Al Asimah', 'country': 'KW'},
    )
    export['buyer'].update(
        endpoint_id='9900000099', tin=None, trn=None, legal_id=None, legal_type=None, legal_authority=None,
        street='Gulf Road', city='Kuwait City', subdivision='Al Asimah', country='KW',
    )
    cases['export with deliver-to address (ibr-152-ae)'] = export
    # Guidelines V2.0 §10.5.1 domestic reverse charge: AE, no VAT (ibr-162-ae, ae-09), goods type (ibr-166-ae,
    # ibr-006-ae), GTIN scheme 0160 (ibr-174-ae), buyer TRN (ibr-103-ae) and the narrative as IBT-120 of the AE
    # breakdown
    cases['domestic reverse charge with narrative (G2.0 §10.5.1)'] = payloads.standard_payload(
        lines=[
            payloads._line(
                1, 2, '1500.00', rate='5', category='AE', goods_type='DL8.48.8.2', gtin='4006381333931',
                rc_narrative='Reverse charge: Cabinet Decision No. 91 of 2023 (electronic devices)',
            )
        ],
        odoo_totals=None,
    )
    cases['mixed S + Z + E'] = payloads.standard_payload(
        lines=[
            payloads._line(1, 1, '100.00'),
            payloads._line(2, 1, '50.00', rate='0', category='Z'),
            payloads._line(3, 1, '25.00', category='E', exemption_code='DL8.46.2'),
        ],
        odoo_totals=None,
    )
    cases['services (SAC)'] = payloads.standard_payload(
        lines=[payloads._line(1, 1, '100.00', item_type='S', hs=None, sac='998311')], odoo_totals=None
    )
    cases['seller without TRN (out-of-scope 480)'] = None
    oos_lines = [payloads._line(1, 1, '100.00', category='O', item_type='S', hs=None, sac='998311')]
    oos = payloads.standard_payload(type_code='480', lines=oos_lines, odoo_totals=None)
    oos['seller']['trn'] = None
    cases['seller without TRN (out-of-scope 480)'] = oos
    cases['three lines of 0.30 (within official slack)'] = payloads.standard_payload(
        lines=[payloads._line(i + 1, 1, '0.30') for i in range(3)], odoo_totals=None
    )
    cases['foreign currency USD'] = payloads.standard_payload(
        currency='USD', exchange_rate=D('3.6725'), aed={'tax_total': D('46.02'), 'total_incl': D('966.45')}
    )
    cases['deemed supply (predefined endpoint, no payment means)'] = None
    deemed = payloads.standard_payload(payment=None, flags={'deemed_supply': True}, transaction_type_code='01000000')
    deemed['buyer'].update(endpoint_id='9900000097', tin=None)
    cases['deemed supply (predefined endpoint, no payment means)'] = deemed
    for label, payload in cases.items():
        kind = payload['kind']
        xml = build(payload)
        outcome = client.validate(xml, kind, profile)
        fatal = [(f.rule, f.text[:90]) for f in outcome.failures if f.flag != 'warning']
        expect('engine-built %s' % label, outcome.ok, str(fatal))

    # ---- 3. negative controls on engine-built XML
    base = build(payloads.standard_payload()).decode('utf-8')
    controls = {
        'standard rate 6%': (
            base.replace('<cbc:Percent>5</cbc:Percent>', '<cbc:Percent>6</cbc:Percent>'),
            'ibr-190-ae',
        ),
        'no UUID': (re.sub(r'<cbc:UUID>.*?</cbc:UUID>', '', base, flags=re.S), 'ibr-193-ae'),
        'no payment means': (re.sub(r'<cac:PaymentMeans>.*?</cac:PaymentMeans>', '', base, flags=re.S), 'ibr-191-ae'),
        'credit transfer without account': (
            re.sub(r'<cac:PayeeFinancialAccount>.*?</cac:PayeeFinancialAccount>', '', base, flags=re.S),
            'ibr-192-ae',
        ),
        'payable total tampered': (
            re.sub(r'(<cbc:PayableAmount currencyID="AED">)[0-9.]+', r'\g<1>1.00', base),
            'ibr-co-16',
        ),
    }
    exempt_380 = payloads.standard_payload(
        lines=[payloads._line(1, 1, '100.00', category='E', exemption_code='DL8.46.1')], odoo_totals=None
    )
    controls['exempt-only document typed 380'] = (build(exempt_380).decode('utf-8'), 'ibr-151-ae')
    no_delivery = copy.deepcopy(export)
    no_delivery['delivery'] = None
    controls['export without deliver-to address'] = (build(no_delivery).decode('utf-8'), 'ibr-152-ae')
    for label, (xml, rule) in controls.items():
        outcome = client.validate(xml.encode('utf-8'), 'invoice', profile)
        rules = {f.rule for f in outcome.failures}
        expect(
            'negative control: %s fails %s' % (label, rule), rule in rules and not outcome.ok, str(sorted(rules)[:6])
        )

    # ---- 3a. XML the engine's mapper produced from REAL posted Odoo documents (tests/test_real_move_crosscheck.py)
    fixtures = os.environ.get('NC_EINVOICE_FIXTURES')
    if fixtures:
        required = _required_real_cases()
        expect('real-move fixtures: the required case list was read', bool(required), str(sorted(required)))
        for case, case_spec in sorted(required.items()):
            kind, outcome_expected, rule = case_spec[:3]
            # an optional 4th element names the profile (the Self-Billing cases); the default is Billing
            case_profile = spec.get_profile(case_spec[3]) if len(case_spec) > 3 else profile
            path = os.path.join(fixtures, case + '.xml')
            if not os.path.isfile(path):
                expect('real-move %s exported' % case, False, 'missing from %s' % fixtures)
                continue
            with open(path, 'rb') as handle:
                case_client = sb_client if case_profile.self_billing else client
                outcome = case_client.validate(handle.read(), kind, case_profile)
            failed = sorted({f.rule for f in outcome.failures if f.flag != 'warning'})
            if outcome_expected == 'pass':
                expect('real-move %s validates under the official rules' % case, outcome.ok, str(failed[:8]))
            else:
                expect(
                    'real-move %s is refused by %s' % (case, rule), not outcome.ok and rule in failed, str(failed[:8])
                )

    # ---- 3b. the official validator does NOT catch a bad seller emirate (rule shadowed, see lib/upstream.py)
    bad = base.replace('<cbc:CountrySubentity>DXB<', '<cbc:CountrySubentity>DU<')
    outcome = client.validate(bad.encode('utf-8'), 'invoice', profile)
    expect(
        'official ibr-128-ae is shadowed: an invalid seller emirate validates clean (recorded upstream)', outcome.ok
    )

    # ---- 3c. the worker authenticates its caller and signs its answers
    def post(path, body, headers):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        connection.request('POST', path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    good_xml = build(payloads.standard_payload())
    nonce = 'ab' * 16

    def body_for(kind, xml_bytes):
        return json.dumps(
            {
                'kind': kind,
                'spec_version': profile.pint_version,
                'xml_b64': base64.b64encode(xml_bytes).decode(),
                'nonce': nonce,
            }
        ).encode()

    request = body_for('invoice', good_xml)
    bearer = {'content-type': 'application/json', 'authorization': 'Bearer ' + validator.auth_secret(TOKEN)}
    expect('no token: 401', post('/validate', request, {'content-type': 'application/json'})[0] == 401)
    expect(
        'wrong token: 401',
        post(
            '/validate',
            request,
            {'content-type': 'application/json', 'authorization': 'Bearer ' + validator.auth_secret('y' * 40)},
        )[0]
        == 401,
    )
    expect(
        'the shared token itself is not the bearer: 401',
        post('/validate', request, {'content-type': 'application/json', 'authorization': 'Bearer ' + TOKEN})[0] == 401,
    )
    status, data = post('/validate', request, bearer)
    answer = json.loads(data)
    expect('good request: 200 and signed', status == 200 and bool(answer.get('signature')))
    expect(
        'worker and engine compute the same signature',
        answer['signature']
        == validator.sign_answer(
            TOKEN,
            answer['xml_sha256'],
            answer['spec_version'],
            answer['manifest_sha256'],
            answer['engine'],
            answer['failures'],
            nonce,
        ),
    )
    expect('the answer echoes the caller nonce', answer['nonce'] == nonce)
    expect('the answer names the document it is about', answer['xml_sha256'] == hashlib.sha256(good_xml).hexdigest())
    expect(
        'a negative content-length is refused',
        post('/validate', b'', dict(bearer, **{'content-length': '-1'}))[0] in (400, 411, 413),
    )
    wrong_root = body_for('credit_note', good_xml)
    expect('an invoice declared as a credit note is refused (400)', post('/validate', wrong_root, bearer)[0] == 400)
    not_ubl = body_for('invoice', b'<foo/>')
    expect(
        'a document with another root is refused (400), not validated', post('/validate', not_ubl, bearer)[0] == 400
    )
    try:
        validator.HttpValidatorClient('http://127.0.0.1:%d' % port, timeout=30, token='z' * 40).validate(
            good_xml, 'invoice', profile
        )
        expect('a client with the wrong token is unavailable', False)
    except validator.ValidatorUnavailable:
        expect('a client with the wrong token is unavailable', True)

    # a process that does not hold the token cannot forge "no failures" - not even one that SAW the request and so
    # knows the bearer (it signs with the only key it has)
    class Forger(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)))
            captured = (self.headers.get('authorization') or '')[7:]
            digest = hashlib.sha256(good_xml).hexdigest()
            forged = {
                'engine': srv_validator.engine,
                'spec_version': profile.pint_version,
                'manifest_sha256': srv_validator.manifest_sha256,
                'failures': [],
                'xml_sha256': digest,
                'nonce': seen['nonce'],
                'signature': validator.sign_answer(
                    captured,
                    digest,
                    profile.pint_version,
                    srv_validator.manifest_sha256,
                    srv_validator.engine,
                    [],
                    seen['nonce'],
                ),
            }
            body = json.dumps(forged).encode()
            self.send_response(200)
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    forger = ThreadingHTTPServer(('127.0.0.1', 0), Forger)
    threading.Thread(target=forger.serve_forever, daemon=True).start()
    try:
        validator.HttpValidatorClient(
            'http://127.0.0.1:%d' % forger.server_address[1], timeout=30, token=TOKEN
        ).validate(good_xml, 'invoice', profile)
        expect('a forged unsigned "valid" answer is rejected', False)
    except validator.ValidatorUnavailable:
        expect('a forged unsigned "valid" answer is rejected', True)
    forger.shutdown()

    # ---- 4. what the worker refuses
    for label, body in (
        ('DOCTYPE', b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><Invoice/>'),
        ('ENTITY', b'<!ENTITY a "b"><Invoice/>'),
    ):
        try:
            client.validate(body, 'invoice', profile)
            expect('worker refuses %s' % label, False)
        except validator.ValidatorUnavailable:
            expect('worker refuses %s' % label, True)
    tmp = tempfile.mkdtemp()
    try:
        copy_dir = os.path.join(tmp, 'rules')
        shutil.copytree(rules_dir, copy_dir)
        with open(os.path.join(copy_dir, 'codelist', 'UNCL4461.gc'), 'ab') as handle:
            handle.write(b' ')
        try:
            worker.verify_rules(copy_dir)
            expect('worker refuses a tampered rule set', False)
        except RuntimeError:
            expect('worker refuses a tampered rule set', True)
    finally:
        shutil.rmtree(tmp, True)
    server.shutdown()
    sb_server.shutdown()
    print('\n%s' % ('ALL EXPECTATIONS HOLD' if not failures_total else 'FAILED: %s' % failures_total))
    return 1 if failures_total else 0


if __name__ == '__main__':
    sys.exit(main())
