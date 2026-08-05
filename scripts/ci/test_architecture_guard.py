#!/usr/bin/env python3
"""Tests for architecture_guard's inert-XML handling (#348).

SCOPE, STATED UP FRONT: this covers the comment/CDATA-skipping behaviour and
the one rule interaction that behaviour deliberately does NOT extend to. It is not
coverage of the whole guard — #330 scoped that as its own ticket, and an
undeclared gap in a test file reads as coverage it does not have.

WHY THIS FILE EXISTS AT ALL. The bug was that Rule 6 matched `<tree>` and
`attrs=` as raw text, so a comment DOCUMENTING the rule failed the rule. The
fix is small enough to look obviously right, which is exactly the kind of fix
that rots: the next person greps for `<tree` and adds a matcher without knowing
comments and CDATA are supposed to be blanked first. These cases fail if they do.

Run standalone (no pytest dependency in CI):

    python3 scripts/ci/test_architecture_guard.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import architecture_guard as guard  # noqa: E402


def _addon_path() -> Path:
    """An ABSOLUTE path under the real custom_addons/.

    `addon_of()` resolves against `guard.REPO_ROOT`, so a relative fixture
    path resolves against the process CWD instead and returns None — which
    makes check_menu_license_gate early-return. The Rule-2 tests below first
    used a relative path, and the negative one passed VACUOUSLY: it asserted
    "no findings" against a checker that never ran. Caught by running the
    suite from another directory, where the POSITIVE test failed too.
    """
    return guard.REPO_ROOT / "custom_addons/ncollection_core/views/_fixture.xml"


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


class TestCdataIsCharacterData(unittest.TestCase):
    """CDATA is blanked for the same reason comments are, not as an extra.

    A `<![CDATA[...]]>` body reaches Odoo as a STRING, never as view markup,
    so Rule 6 has nothing to say about a `<tree>` spelled inside one. Found by
    the odoo-reviewer on the first round of this ticket, when the fix handled
    comments only.
    """

    def test_a_tree_inside_cdata_is_not_markup(self):
        self.assertEqual(
            view_findings('<field><![CDATA[ <tree/> ]]></field>'), [])

    def test_an_unterminated_comment_inside_cdata_cannot_swallow_live_markup(self):
        """THE HOLE, pinned. Inside CDATA, `<!--` is ordinary text, so an
        unterminated one there is perfectly well-formed XML and gets no
        backstop from the well-formedness gate. Handling comments in a
        SEPARATE, earlier pass would run from it past `]]>` to the next real
        `-->` and blank the live `<tree/>` in between — the guard silently
        passing something it must catch."""
        findings = view_findings(
            '<field><![CDATA[ <!-- unterminated ]]></field>\n'
            '<tree/>\n'
            '<!-- a later, genuine comment -->')
        self.assertEqual(len(findings), 1,
                         "live markup after a CDATA block must still be scanned")
        self.assertIn(":2:", findings[0])

    def test_a_cdata_open_inside_a_comment_does_not_escape_the_comment(self):
        """The mirror image, and the reason both live in ONE alternation: the
        scan is left to right, so whichever construct opens first consumes the
        other and neither can start inside the other."""
        findings = view_findings('<!-- mentions <![CDATA[ --> <tree/>')
        self.assertEqual(len(findings), 1,
                         "the comment must end at its own -->, not at ]]>")

    def test_line_numbers_survive_a_multi_line_cdata_block(self):
        text = ('<field><![CDATA[\n'   # 1
                '  line two\n'         # 2
                ']]></field>\n'        # 3
                '<tree/>')             # 4
        findings = view_findings(text)
        self.assertEqual(len(findings), 1)
        self.assertIn(":4:", findings[0])


class TestProcessingInstructions(unittest.TestCase):
    """The third inert construct, and the third instance of one hole.

    A PI's content runs to the first `?>` and is otherwise unconstrained, so a
    literal `<!--` inside one is well-formed XML that no parser reads as a
    comment. Found by the code-reviewer, on the commit that had just fixed the
    identical hole for CDATA.
    """

    def test_a_comment_open_inside_a_pi_cannot_swallow_live_markup(self):
        """THE CRITICAL, pinned. This input is well-formed XML — xmllint and
        minidom both accept it — so no well-formedness gate would ever catch
        it, and the guard reported nothing at all for a real `<tree>`."""
        findings = view_findings(
            '<odoo>\n'
            '  <?example note <!-- looks like a comment open ?>\n'
            '  <tree string="real"/>\n'
            '  <!-- unrelated later real comment -->\n'
            '</odoo>')
        self.assertEqual(len(findings), 1,
                         "a real <tree> after a PI must still be reported")
        self.assertIn(":3:", findings[0])

    def test_a_tree_inside_a_pi_is_not_markup(self):
        self.assertEqual(view_findings('<?php echo "<tree/>"; ?>'), [])

    def test_the_xml_declaration_is_harmless_to_blank(self):
        findings = view_findings('<?xml version="1.0"?>\n<tree/>')
        self.assertEqual(len(findings), 1)
        self.assertIn(":2:", findings[0])


class TestLineBreakPreservation(unittest.TestCase):
    """`splitlines` decides the line numbers, so it must decide the blanking.

    Blanking every character except `\\n` looks obviously right and is not:
    `str.splitlines` also breaks on `\\r`, `\\v`, `\\f`, `\\x1c`-`\\x1e`, `\\x85`,
    `\\u2028` and `\\u2029`. Any of those inside a comment collapsed the whole
    comment to one line and misreported every finding below it.
    """

    def test_every_separator_splitlines_honours_is_preserved(self):
        for sep in ('\n', '\r\n', '\r', '\v', '\f', '\x1c', '\x1d', '\x1e',
                    '\x85', ' ', ' '):
            with self.subTest(sep=repr(sep)):
                text = '<odoo>%s<!--%sa%sb%s-->%s<tree/>' % ((sep,) * 5)
                findings = view_findings(text)
                self.assertEqual(len(findings), 1)
                self.assertIn(
                    ":%d:" % len(text.splitlines()), findings[0],
                    "the finding must land on the line splitlines() agrees is "
                    "the last one")

    def test_blanking_never_changes_the_line_count(self):
        for sep in ('\n', '\r\n', '\r', ' '):
            with self.subTest(sep=repr(sep)):
                text = '<a>%s<!--%sx%sy%s-->%s</a>' % ((sep,) * 5)
                self.assertEqual(
                    len(guard.without_inert_xml_text(text).splitlines()),
                    len(text.splitlines()))


class TestSearchGroupAttributes(unittest.TestCase):
    """`<group>` in a `<search>` may carry only schema-approved attributes.

    NOT a style rule: Odoo 19's RelaxNG rejects the WHOLE view, so the module
    fails to INSTALL rather than degrade — and CI's `Validate XML` step cannot
    catch it, because the file IS well-formed and the violation is
    schema-level. First signal today is a failed install, minutes in.

    THE PREMISE IN THE TICKET WAS WRONG, which is why these cases are pinned
    against the schema rather than against the ticket. #61 recorded "Odoo 19
    dropped `expand`", and #353 inherited it. Reading base/rng/common.rng
    shows a `<group>` may carry only colspan/rowspan/fill/height/width/name/
    color/invisible/position/groups — `string` is absent too, and it is the
    commoner spelling. An expand-only rule would have passed the more likely
    bug straight through.
    """

    def test_expand_is_rejected(self):
        findings = view_findings('<search><group expand="0"><filter name="a"/></group></search>')
        self.assertEqual(len(findings), 1)
        self.assertIn("separator", findings[0])

    def test_string_is_rejected_too(self):
        """The case the ticket's own framing would have missed."""
        self.assertEqual(
            len(view_findings('<search><group string="Group By"><filter name="a"/></group></search>')),
            1, "<group string=> is as fatal as <group expand=>")

    def test_a_bare_group_is_fine(self):
        """Verified against the pinned odoo:19 schema: this VALIDATES. A rule
        that flagged every `<group>` in a search would be wrong, not merely
        noisy — it would push people to change working views."""
        self.assertEqual(view_findings('<search><group/></search>'), [])

    def test_a_group_with_children_but_no_attributes_is_fine(self):
        self.assertEqual(
            view_findings('<search><group><filter name="a"/></group></search>'), [])

    def test_schema_approved_attributes_are_fine(self):
        self.assertEqual(
            view_findings('<search><group name="g" groups="base.group_user"/></search>'), [])

    def test_a_form_group_with_string_is_untouched(self):
        """Forms are not RNG-validated at all — the rng directory ships no
        form_view.rng — which is why `<group string="Measurement">` installs
        fine. The rule is scoped to search regions for exactly this reason."""
        self.assertEqual(
            view_findings('<form><group string="Measurement"><field name="a"/></group></form>'), [])

    def test_the_token_inside_a_comment_is_not_markup(self):
        """Composes with #348 — and this is a live case: alert_views.xml's own
        header documents `<group expand="0">` in prose."""
        self.assertEqual(
            view_findings('<search><!-- never <group expand="0"> --><field name="a"/></search>'), [])

    def test_only_the_offending_group_is_reported(self):
        """A compliant search view earlier in the file must not be blamed for
        a later one — the region match is non-greedy for this reason."""
        text = ('<search>\n<separator/>\n</search>\n'
                '<form><group string="ok"/></form>\n'
                '<search>\n<group expand="0"/>\n</search>')
        findings = view_findings(text)
        self.assertEqual(len(findings), 1)
        self.assertIn(":6:", findings[0])


class TestScopeOfTheExemption(unittest.TestCase):
    """Comments are exempt from SYNTAX rules only. Not from everything."""

    def test_rule_2_does_not_trip_on_a_comment_about_license_gating(self):
        """Same defect as the one this ticket fixes for Rule 6, in a sibling
        check. A comment EXPLAINING license gating is not a license-gated
        menu. This is a false positive rather than a hole, which is why it
        ranks below the PI case -- but it is the ticket's own premise."""
        text = ('<odoo>\n'
                '  <!-- Menus here use groups="module.license_pro"; the ORM\n'
                '       side is mirrored in models/access.py (Rule 4). -->\n'
                '  <menuitem id="m" name="Plain"/>\n'
                '</odoo>')
        findings: list[str] = []
        guard.check_menu_license_gate(_addon_path(), text, set(), findings)
        self.assertEqual(findings, [],
                         "a comment about license gating is not a gated menu")

    def test_rule_2_still_fires_on_a_real_license_gated_menu(self):
        """The half that matters: the exemption must not disarm the rule."""
        text = '<menuitem id="m" groups="module.license_pro"/>'
        findings: list[str] = []
        guard.check_menu_license_gate(_addon_path(), text, set(), findings)
        self.assertEqual(len(findings), 1)

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
            len(guard.without_inert_xml_text(text).splitlines()),
            len(text.splitlines()))

    def test_text_outside_comments_is_untouched(self):
        self.assertEqual(guard.without_inert_xml_text('<list/>'), '<list/>')

    def test_an_unterminated_comment_is_left_alone(self):
        """Documented behaviour, not an oversight: the file is malformed XML
        and CI's well-formedness step fails it on its own. Pinned so nobody
        'fixes' it into swallowing the rest of a real file."""
        text = '<odoo>\n<!-- never closed\n<tree/>\n</odoo>'
        self.assertEqual(guard.without_inert_xml_text(text), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
