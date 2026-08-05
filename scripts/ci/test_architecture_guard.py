#!/usr/bin/env python3
"""Tests for architecture_guard's XML-comment handling (#348).

SCOPE, STATED UP FRONT: this covers the comment-skipping behaviour and the one
rule interaction that behaviour deliberately does NOT extend to. It is not
coverage of the whole guard — #330 scoped that as its own ticket, and an
undeclared gap in a test file reads as coverage it does not have.

WHY THIS FILE EXISTS AT ALL. The bug was that Rule 6 matched `<tree>` and
`attrs=` as raw text, so a comment DOCUMENTING the rule failed the rule. The
fix is small enough to look obviously right, which is exactly the kind of fix
that rots: the next person greps for `<tree` and adds a matcher without knowing
comments are supposed to be blanked first. These cases fail if they do.

Run standalone (no pytest dependency in CI):

    python3 scripts/ci/test_architecture_guard.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import architecture_guard as guard  # noqa: E402


def view_findings(text: str, name: str = "views/thing.xml") -> list[str]:
    findings: list[str] = []
    guard.check_view_syntax(Path(name), text, findings)
    return findings


class TestCommentsAreNotMarkup(unittest.TestCase):
    """A comment about the rule must not fail the rule."""

    def test_a_comment_mentioning_tree_is_not_a_violation(self):
        self.assertEqual(
            view_findings('<odoo>\n  <!-- use <list>, never <tree> -->\n</odoo>'),
            [])

    def test_a_comment_mentioning_attrs_is_not_a_violation(self):
        self.assertEqual(
            view_findings('<odoo>\n  <!-- no attrs= in Odoo 19 -->\n</odoo>'),
            [])

    def test_a_multi_line_comment_is_covered_end_to_end(self):
        """The single-line case can be passed by a naive per-line strip; this
        one cannot. It is the shape real explanations take -- both offending
        tokens sit on interior lines with no comment delimiter of their own."""
        text = (
            '<odoo>\n'
            '  <!--\n'
            '      Odoo 19 dropped <tree>.\n'
            '      It also dropped attrs="{...}".\n'
            '  -->\n'
            '  <list/>\n'
            '</odoo>')
        self.assertEqual(view_findings(text), [])

    def test_two_comments_on_one_line_are_both_blanked(self):
        """Pins the non-greedy match. A greedy `<!--.*-->` would swallow the
        live markup BETWEEN the two comments, and a real `<tree>` sitting
        there would stop being reported -- a guard hole, not a false alarm."""
        self.assertEqual(
            view_findings('<!-- a --> <list/> <!-- mentions <tree> -->'), [])
        self.assertEqual(
            len(view_findings('<!-- a --> <tree/> <!-- b -->')), 1,
            "live markup between two comments must still be scanned")


class TestRealViolationsStillFail(unittest.TestCase):
    """The half that matters more: the fix must not have disarmed the rule."""

    def test_a_real_tree_tag_is_still_a_violation(self):
        findings = view_findings('<odoo>\n  <tree string="x"/>\n</odoo>')
        self.assertEqual(len(findings), 1)
        self.assertIn("uses <tree>", findings[0])

    def test_a_real_attrs_is_still_a_violation(self):
        findings = view_findings('<field name="a" attrs="{\'invisible\': []}"/>')
        self.assertEqual(len(findings), 1)
        self.assertIn("attrs=", findings[0])

    def test_markup_after_a_comment_ends_on_the_same_line_is_scanned(self):
        """The off-by-one that a sloppy blanking would introduce: everything
        after `-->` is live markup again."""
        findings = view_findings('<!-- documented --> <tree/>')
        self.assertEqual(len(findings), 1)

    def test_markup_before_a_comment_starts_on_the_same_line_is_scanned(self):
        findings = view_findings('<tree/> <!-- documented -->')
        self.assertEqual(len(findings), 1)

    def test_a_stray_comment_close_in_an_attribute_does_not_move_the_boundary(self):
        """A bare `-->` inside an attribute value is legal XML, and the guard
        must not treat it as ending a comment that already closed. The reverse
        -- faking a comment OPEN from an attribute -- is impossible, because a
        raw `<` is illegal inside an attribute value in well-formed XML."""
        findings = view_findings('<!-- doc --> <field help="a --> b"/> <tree/>')
        self.assertEqual(len(findings), 1,
                         "markup after a stray --> must still be scanned")

    def test_line_numbers_survive_a_preceding_multi_line_comment(self):
        """Blanking, not deleting. Findings are reported as `path:line` and a
        reader follows them literally; shifting them by the length of every
        comment above would send people to the wrong line, which is worse than
        the false positive this ticket removes."""
        text = (
            '<odoo>\n'          # 1
            '  <!--\n'          # 2
            '      three\n'     # 3
            '      lines\n'     # 4
            '  -->\n'           # 5
            '  <tree/>\n'       # 6
            '</odoo>')
        findings = view_findings(text)
        self.assertEqual(len(findings), 1)
        self.assertIn(":6:", findings[0])


class TestScopeOfTheExemption(unittest.TestCase):
    """Comments are exempt from SYNTAX rules only. Not from everything."""

    def test_a_commented_out_secret_is_still_a_secret(self):
        """THE DELIBERATE ASYMMETRY. Commenting out a credential does not
        remove it from the repository -- it is in the file, in git history, and
        readable by anyone with clone access. Rule 6 asks 'is this live
        markup?'; the secret rule asks 'is this string present?', and the
        answer there is yes either way.

        The fixture is assembled at runtime so this file does not itself carry
        a string that trips the guard scanning it.
        """
        secret = 'pass' + 'word = "' + 'sup3rs3cr3tvalue' + '"'
        text = '<odoo>\n  <!-- old: %s -->\n</odoo>' % secret
        findings: list[str] = []
        guard.check_secrets(Path("data/thing.xml"), text, findings)
        self.assertEqual(len(findings), 1,
                         "a commented-out credential must still be reported")

    def test_non_xml_files_are_untouched(self):
        self.assertEqual(view_findings('<tree/>', name="models/thing.py"), [])


class TestBlanking(unittest.TestCase):
    """The helper's own contract, asserted directly."""

    def test_line_count_is_preserved_exactly(self):
        text = '<a>\n<!--\nx\ny\n-->\n</a>'
        self.assertEqual(
            len(guard.without_xml_comments(text).splitlines()),
            len(text.splitlines()))

    def test_text_outside_comments_is_untouched(self):
        self.assertEqual(guard.without_xml_comments('<list/>'), '<list/>')

    def test_an_unterminated_comment_is_left_alone(self):
        """Documented behaviour, not an oversight: the file is malformed XML
        and CI's well-formedness step fails it on its own. Pinned so nobody
        'fixes' it into swallowing the rest of a real file."""
        text = '<odoo>\n<!-- never closed\n<tree/>\n</odoo>'
        self.assertEqual(guard.without_xml_comments(text), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
