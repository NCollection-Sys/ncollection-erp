# -*- coding: utf-8 -*-
"""OWL templates: event handlers must not call component methods bare.

THE BUG. In ``ncollection_account_reports`` the picker handlers read
``t-on-input="(ev) => this.searchRelation(field(fname), ...)"``. ``field(...)``
is a component method. In a RENDER expression OWL calls it on the component, so
``t-if="has('x')"`` works - but inside an arrow function that runs later it is a
detached call with no ``this``, and typing in the Partner Ledger's partner
search threw ``Cannot read properties of undefined (reading 'state')``.

Nothing but a real keystroke exposes this, so the rule is enforced statically:
inside any ``t-on-*`` attribute, a function call must be a member call
(``this.x(...)``/``obj.x(...)``) or a known global.
"""
import glob
import os
import re

from lxml import etree

from odoo.tests import TransactionCase, tagged

_ADDONS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GLOBALS = {'Math', 'parseInt', 'parseFloat', 'String', 'Number', 'Boolean', 'Date', 'JSON',
            'Object', 'Array', 'encodeURIComponent', 'setTimeout', 'if', 'function', 'return'}
_BARE_CALL = re.compile(r'(?<![\w.$])([A-Za-z_]\w*)\s*\(')


@tagged('post_install', '-at_install')
class TestOwlTemplateRules(TransactionCase):

    def _templates(self):
        pattern = os.path.join(_ADDONS, 'ncollection_*', 'static', 'src', '**', '*.xml')
        return glob.glob(pattern, recursive=True)

    def test_event_handlers_never_call_component_methods_bare(self):
        offenders = []
        scanned = 0
        for path in self._templates():
            try:
                tree = etree.parse(path)
            except etree.XMLSyntaxError:
                continue
            for node in tree.iter():
                for name, value in node.attrib.items():
                    if not name.startswith('t-on-'):
                        continue
                    scanned += 1
                    for call in _BARE_CALL.findall(value):
                        if call not in _GLOBALS:
                            offenders.append('%s: %s=%r -> bare call %s(...)' % (
                                os.path.relpath(path, _ADDONS), name, value, call))
        self.assertTrue(scanned, 'no t-on handlers found - the scan itself is broken')
        self.assertFalse(
            offenders,
            'A bare call inside an event handler runs without `this`:\n' + '\n'.join(offenders))
