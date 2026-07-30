# -*- coding: utf-8 -*-
"""RTL conformance guard (P3-T08 / UI_UX §23).

The Arabic (RTL) interface relies on every ncollection_* stylesheet using
LOGICAL CSS properties (margin-inline-start, border-inline-end, inset-inline-*)
rather than physical left/right ones, so the layout mirrors automatically when
the active language's direction is 'rtl'. This test scans our SCSS and fails if
a physical, direction-breaking property reappears — keeping the RTL audit from
silently regressing.
"""
import glob
import os
import re

from odoo.tests import TransactionCase, tagged

# .../<addons>/ncollection_branding/tests/<this file> -> <addons>
# dirname x3: file -> tests -> ncollection_branding -> <addons>
_ADDONS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Physical, direction-breaking declarations. Their logical replacements:
#   margin/padding/border-left|right  -> *-inline-start|end
#   left:|right: (positioning)        -> inset-inline-start|end
#   text-align/float: left|right      -> *: start|end
_PHYSICAL_RE = re.compile(
    r'(?:(?:margin|padding|border)-(?:left|right)\b'
    r'|(?:^|[;{]|\s)(?:left|right)\s*:'
    r'|text-align\s*:\s*(?:left|right)\b'
    r'|float\s*:\s*(?:left|right)\b)',
    re.MULTILINE,
)
_LINE_COMMENT_RE = re.compile(r'//[^\n]*')
_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)


def _strip_comments(scss):
    return _LINE_COMMENT_RE.sub('', _BLOCK_COMMENT_RE.sub('', scss))


@tagged('post_install', '-at_install')
class TestRtlConformance(TransactionCase):

    def test_no_physical_direction_in_ncollection_scss(self):
        offenders = []
        pattern = os.path.join(_ADDONS_DIR, 'ncollection_*', '**', '*.scss')
        for path in glob.glob(pattern, recursive=True):
            with open(path, encoding='utf-8') as fh:
                body = _strip_comments(fh.read())
            for i, line in enumerate(body.splitlines(), 1):
                if _PHYSICAL_RE.search(line):
                    rel = os.path.relpath(path, _ADDONS_DIR)
                    offenders.append('%s:%d: %s' % (rel, i, line.strip()))
        self.assertFalse(
            offenders,
            "Physical (RTL-breaking) CSS found — use logical properties "
            "(inline-start/end) per UI_UX §23 / P3-T08:\n" + '\n'.join(offenders))
