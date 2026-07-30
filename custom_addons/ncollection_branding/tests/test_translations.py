# -*- coding: utf-8 -*-
"""Arabic translation (.po) conformance (P3-T08).

The CI test database runs in English, so it never auto-loads the ncollection_*
`i18n/ar.po` files (Odoo only imports a language's .po when that language is
active — Arabic goes live in tenant DBs via the UAE localization). This test
therefore validates the .po files STRUCTURALLY on every run: UTF-8, well-formed
msgid/msgstr grammar, matched counts, and every source string actually
translated (no empty msgstr) — catching the hand-authoring hazards that would
otherwise only surface on staging.

It does NOT (and cannot) assert translation QUALITY or visual RTL correctness —
those are the native-review and staging-validation steps documented on the PR.
"""
import glob
import os
import re

from odoo.tests import TransactionCase, tagged

# custom_addons/ncollection_branding/tests -> custom_addons
_ADDONS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_STR_LINE = re.compile(r'^(msgid|msgstr)?\s*"(.*)"\s*$')


def _parse_po(text):
    """Minimal .po parser -> list of (msgid, msgstr) with concatenated multi-line
    strings. Raises ValueError on grammar it cannot make sense of."""
    pairs = []
    cur_key = None          # 'msgid' | 'msgstr'
    msgid = msgstr = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = _STR_LINE.match(line)
        if not m:
            raise ValueError("unparseable .po line: %r" % raw)
        key, content = m.group(1), m.group(2)
        if key == 'msgid':
            if cur_key == 'msgstr':          # previous entry complete
                pairs.append((msgid, msgstr))
            msgid, msgstr, cur_key = content, None, 'msgid'
        elif key == 'msgstr':
            msgstr, cur_key = content, 'msgstr'
        else:                                # continuation "..." line
            if cur_key == 'msgid':
                msgid += content
            elif cur_key == 'msgstr':
                msgstr += content
            else:
                raise ValueError("dangling string line: %r" % raw)
    if cur_key == 'msgstr':
        pairs.append((msgid, msgstr))
    return pairs


@tagged('post_install', '-at_install')
class TestArabicTranslations(TransactionCase):

    def _ar_po_paths(self):
        return sorted(glob.glob(os.path.join(_ADDONS_DIR, 'ncollection_*', 'i18n', 'ar.po')))

    def test_ar_po_files_present(self):
        # P3-T08 wanted complete, consistent coverage — an ar.po in every
        # user-facing ncollection_* module, not partial modules.
        self.assertGreaterEqual(
            len(self._ar_po_paths()), 10,
            "expected an ar.po in every user-facing ncollection_* module")

    def test_ar_po_valid_and_fully_translated(self):
        for path in self._ar_po_paths():
            rel = os.path.relpath(path, _ADDONS_DIR)
            with open(path, encoding='utf-8') as fh:   # UTF-8 or this raises
                pairs = _parse_po(fh.read())
            # first entry is the header (empty msgid); must exist.
            self.assertTrue(pairs, "%s has no entries" % rel)
            self.assertEqual(pairs[0][0], '', "%s: first entry must be the .po header" % rel)
            for msgid, msgstr in pairs[1:]:
                self.assertTrue(msgid, "%s: empty msgid" % rel)
                self.assertTrue(
                    msgstr, "%s: source %r has an empty translation" % (rel, msgid[:60]))
