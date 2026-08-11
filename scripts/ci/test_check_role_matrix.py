#!/usr/bin/env python3
"""Tests for the role-matrix gate (#382).

The gate exists because `hooks.py` claimed docs/ROLE_MATRIX.md was enforced and
it was not. These tests exist because the FIRST version of the gate reproduced
that same defect: it scanned cells for backticked xml-ids and a list of bare
role words, and silently contributed nothing for anything else. Review proved
three ways to fool it, all reproduced here as tests:

    `stock.group_stock_user`, crm.group_use_lead_menu  -> dropped the second
    Employees                                           -> dropped entirely
    Manager (chain, mirrors Owner scope)                -> INVENTED an Owner edge

The first two let the document describe a grant the code does not implement
while the gate printed "clean" — the exact #382 failure, one level up. So the
parser now fails closed, and these tests pin that it stays that way.

Run standalone (no pytest dependency in CI):

    python3 scripts/ci/test_check_role_matrix.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_role_matrix as C  # noqa: E402

P = C.ROLE_PREFIX

# Shaped like the real §2: two "Implies" columns plus a Rationale column.
GOOD_DOC = """
## 2. The matrix

| Role (xml-id) | Implies (static, base only) | Implies (runtime-linked) | Rationale |
|---|---|---|---|
| **Employee** `group_role_employee` | `base.group_user` | — | floor |
| **Sales** `group_role_sales` | Employee | `sales_team.group_sale_salesman` | scope |
| **Owner** `group_role_owner` | CEO (chain), `base.group_system` | `account.group_account_user` | all |

## 3. Inheritance chains
"""


def _doc(static="Employee", runtime="`sales_team.group_sale_salesman`"):
    return GOOD_DOC.replace(
        "| **Sales** `group_role_sales` | Employee | "
        "`sales_team.group_sale_salesman` | scope |",
        "| **Sales** `group_role_sales` | %s | %s | scope |" % (static, runtime))


class TestCellGrammar(unittest.TestCase):
    """The three shapes review proved could fool the first version."""

    def test_an_xmlid_without_backticks_is_refused_not_dropped(self):
        """CRITICAL 1. A pasted or reflow-mangled id used to vanish.

        Dropping it silently means the document can promise a grant the code
        does not implement while this gate reports clean.
        """
        with self.assertRaises(C.MatrixSyntaxError) as ctx:
            C._cell_targets("`stock.group_stock_user`, crm.group_use_lead_menu")
        self.assertIn("crm.group_use_lead_menu", str(ctx.exception))

    def test_an_unknown_role_word_is_refused_not_dropped(self):
        """CRITICAL 2. 'Employees' is one letter from a real role name."""
        for probe in ("Employees", "Support"):
            with self.subTest(word=probe):
                with self.assertRaises(C.MatrixSyntaxError):
                    C._cell_targets(probe)

    def test_prose_in_an_implies_column_is_refused_not_half_understood(self):
        """HIGH 3. This used to INVENT an edge to Owner from a clarifying aside.

        Failing loudly is correct: the fix is to move prose to the Rationale
        column, and the message says so.
        """
        with self.assertRaises(C.MatrixSyntaxError):
            C._cell_targets("Manager (chain, mirrors Owner scope)")

    def test_the_legitimate_spellings_still_parse(self):
        self.assertEqual(C._cell_targets("`base.group_user`"), {"base.group_user"})
        self.assertEqual(C._cell_targets("Employee"), {P + "group_role_employee"})
        self.assertEqual(C._cell_targets("CEO (chain)"), {P + "group_role_ceo"})
        self.assertEqual(
            C._cell_targets("CEO (chain), `base.group_system`"),
            {P + "group_role_ceo", "base.group_system"})
        self.assertEqual(C._cell_targets("—"), set())

    def test_a_backticked_id_does_not_also_register_as_a_bare_role_word(self):
        """`hr.group_hr_user` must not also yield the role `hr`."""
        self.assertEqual(C._cell_targets("`hr.group_hr_user`"), {"hr.group_hr_user"})

    def test_a_stray_comma_is_refused(self):
        with self.assertRaises(C.MatrixSyntaxError):
            C._cell_targets("`base.group_user`, ")


class TestMatrixParsing(unittest.TestCase):

    def test_the_good_document_parses_to_what_it_says(self):
        got = C.parse_matrix(GOOD_DOC)
        self.assertEqual(set(got), {P + "group_role_employee",
                                    P + "group_role_sales",
                                    P + "group_role_owner"})
        self.assertEqual(got[P + "group_role_employee"]["runtime"], set())
        self.assertEqual(got[P + "group_role_owner"]["static"],
                         {P + "group_role_ceo", "base.group_system"})

    def test_a_missing_section_2_refuses(self):
        with self.assertRaises(C.MatrixSyntaxError):
            C.parse_matrix("# Matrix\n\n## 1. Design rules\n\nnothing here\n")

    def test_a_role_the_parser_cannot_name_refuses(self):
        """A 9th role added to the table but not to _ROLE_WORDS.

        Without this, another row referring to it by name would be silently
        dropped — CRITICAL 2 arriving through the front door.
        """
        doc = GOOD_DOC.replace(
            "## 3. Inheritance chains",
            "| **Support** `group_role_support` | Employee | — | new |\n\n"
            "## 3. Inheritance chains")
        with self.assertRaises(C.MatrixSyntaxError) as ctx:
            C.parse_matrix(doc)
        self.assertIn("group_role_support", str(ctx.exception))


class TestConstantReader(unittest.TestCase):

    def _tmp(self, body):
        fh = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
        fh.write(body)
        fh.close()
        return Path(fh.name)

    def test_reads_a_plain_literal(self):
        p = self._tmp("X = {'a': ['b']}\n")
        self.assertEqual(C._literal(p, "X"), {"a": ["b"]})

    def test_a_missing_constant_refuses(self):
        p = self._tmp("Y = 1\n")
        with self.assertRaises(C.MatrixSyntaxError):
            C._literal(p, "X")

    def test_a_second_assignment_refuses_rather_than_reading_the_first(self):
        """Reading only the first would compare against a partial value."""
        p = self._tmp("X = {'a': []}\nX = {'a': ['b']}\n")
        with self.assertRaises(C.MatrixSyntaxError) as ctx:
            C._literal(p, "X")
        self.assertIn("assigned 2 times", str(ctx.exception))

    def test_a_non_literal_refuses_with_our_message(self):
        p = self._tmp("X = dict(a=[])\n")
        with self.assertRaises(C.MatrixSyntaxError):
            C._literal(p, "X")


class TestEndToEnd(unittest.TestCase):
    """run() against real files on disk, so the exit codes are pinned too."""

    def setUp(self):
        self._saved = (C.MATRIX, C.HOOKS, C.TESTS)
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        C.MATRIX, C.HOOKS, C.TESTS = self._saved

    def _wire(self, doc, implications, allowed):
        d = Path(self.dir)
        (d / "m.md").write_text(doc)
        (d / "h.py").write_text("ROLE_IMPLICATIONS = %r\n" % implications)
        (d / "t.py").write_text("MATRIX_ALLOWED_DIRECT = %r\n" % allowed)
        C.MATRIX, C.HOOKS, C.TESTS = d / "m.md", d / "h.py", d / "t.py"

    def _agreeing(self):
        return (
            {P + "group_role_sales": ["sales_team.group_sale_salesman"],
             P + "group_role_owner": ["account.group_account_user"]},
            {P + "group_role_employee": ["base.group_user"],
             P + "group_role_sales": [P + "group_role_employee",
                                      "sales_team.group_sale_salesman"],
             P + "group_role_owner": [P + "group_role_ceo", "base.group_system",
                                      "account.group_account_user"]},
        )

    def test_agreement_exits_zero(self):
        impl, allowed = self._agreeing()
        self._wire(GOOD_DOC, impl, allowed)
        self.assertEqual(C.run(), 0)

    def test_code_granting_more_than_the_doc_exits_one(self):
        impl, allowed = self._agreeing()
        impl[P + "group_role_sales"].append("account.group_account_user")
        self._wire(GOOD_DOC, impl, allowed)
        self.assertEqual(C.run(), 1)

    def test_the_doc_listing_more_than_the_code_exits_one(self):
        impl, allowed = self._agreeing()
        doc = _doc(runtime="`sales_team.group_sale_salesman`, `hr.group_hr_user`")
        self._wire(doc, impl, allowed)
        self.assertEqual(C.run(), 1)

    def test_the_test_allowlist_drifting_exits_one(self):
        """The escalation-shaped drift: a role gaining base.group_system."""
        impl, allowed = self._agreeing()
        allowed[P + "group_role_sales"].append("base.group_system")
        self._wire(GOOD_DOC, impl, allowed)
        self.assertEqual(C.run(), 1)

    def test_an_unparseable_cell_exits_one_through_main(self):
        """main() must convert the exception into a REFUSING exit, not a crash."""
        impl, allowed = self._agreeing()
        self._wire(_doc(runtime="crm.group_use_lead_menu"), impl, allowed)
        self.assertEqual(C.main(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
