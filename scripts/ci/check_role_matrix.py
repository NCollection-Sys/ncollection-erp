#!/usr/bin/env python3
"""Make docs/ROLE_MATRIX.md actually authoritative (#382).

WHAT WAS WRONG. `ncollection_core/hooks.py` stated the contract:

    The authoritative human-readable matrix lives in `docs/ROLE_MATRIX.md`;
    the mapping below and that document MUST change together (enforced by the
    transitive-closure test in tests/test_roles.py).

Nothing enforced it. `test_hook_mapping_matches_matrix` and
`test_no_unexpected_escalation` compare `ROLE_IMPLICATIONS` against
`MATRIX_ALLOWED_DIRECT` — a Python dict IN THE SAME TEST FILE. Neither opens
`docs/ROLE_MATRIX.md`. So there were three sources of truth, two enforced
against each other, and the third — the one described as authoritative and the
one a human actually reads — enforced against nothing.

Demonstrated during #347: a group implication was added, the in-file dict caught
the mapping change immediately (that guard is real and works), and the DOCUMENT
went stale with nothing noticing. A reviewer found it by reading — which is
precisely the check the comment claims is automated.

WHY THIS IS A SCRIPT AND NOT AN ODOO TEST. #382 proposed "the test parses the
doc". It cannot: tests run inside the odoo container, which mounts only
./custom_addons and ./oca. `docs/` is not there — verified, `test -f` on the
path fails inside the container. Implementing the ticket as literally written
would require mounting docs/ into every environment including production, or
moving a human-facing document into an addon. Both are worse than putting the
check where both files already exist: the host, in CI's lint job and pre-push.

WHAT IT CHECKS. §2 of the matrix has two "Implies" columns, and they are
enforced by different mechanisms, so they are compared against different things:

    column 2  "Implies (static, base only)"      -> security/role_groups.xml
    column 3  "Implies (runtime-linked, ...)"    -> hooks.ROLE_IMPLICATIONS

    doc column 3          == ROLE_IMPLICATIONS       (hooks.py)
    doc columns 2 + 3     == MATRIX_ALLOWED_DIRECT   (tests/test_roles.py)

The Odoo test keeps doing the job only it can do — comparing the allowlist to
the group graph a real database actually ends up with. This adds the missing
edge: the allowlist itself now has to match the document.

Exit 0 = the three agree. Exit 1 = they do not, naming each difference.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "docs" / "ROLE_MATRIX.md"
HOOKS = REPO / "custom_addons" / "ncollection_core" / "hooks.py"
TESTS = REPO / "custom_addons" / "ncollection_core" / "tests" / "test_roles.py"

ROLE_PREFIX = "ncollection_core."

# Column 1 reads `**Sales** \`group_role_sales\``; columns 2-3 refer to other
# roles by DISPLAY NAME ("Employee", "Manager (chain)"), not by xml-id.
_ROLE_WORDS = {
    "employee": "group_role_employee",
    "sales": "group_role_sales",
    "warehouse": "group_role_warehouse",
    "hr": "group_role_hr",
    "accountant": "group_role_accountant",
    "manager": "group_role_manager",
    "ceo": "group_role_ceo",
    "owner": "group_role_owner",
}

_BACKTICKED = re.compile(r"`([A-Za-z_][\w.]*\.[\w.]+)`")
_ROLE_ID = re.compile(r"`(group_role_\w+)`")
_WORD = re.compile(r"[A-Za-z]+")


def _cell_targets(cell: str) -> set[str]:
    """Every group xml-id a matrix cell names.

    Two spellings appear and both must be understood, because using only one
    would silently ignore half the matrix:
      * backticked full xml-ids -> `base.group_user`, `stock.group_stock_user`
      * bare role display names -> Employee, "Manager (chain)"
    """
    out = {m.group(1) for m in _BACKTICKED.finditer(cell)}
    # Strip the backticked spans before scanning words, so `hr.group_hr_user`
    # does not also register as the bare role word "hr".
    residue = _BACKTICKED.sub(" ", cell)
    for word in _WORD.findall(residue):
        role = _ROLE_WORDS.get(word.lower())
        if role:
            out.add(ROLE_PREFIX + role)
    return out


def parse_matrix(text: str) -> dict[str, dict[str, set[str]]]:
    """§2's table -> {role_xmlid: {'static': {...}, 'runtime': {...}}}."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip().startswith("## 2."))
    except StopIteration:
        raise SystemExit(
            "REFUSING: docs/ROLE_MATRIX.md has no '## 2.' section — the "
            "document was restructured and this check would silently verify "
            "nothing.")
    out: dict[str, dict[str, set[str]]] = {}
    for ln in lines[start:]:
        if ln.strip().startswith("## 3."):
            break
        if not ln.strip().startswith("|"):
            continue
        cols = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cols) < 3:
            continue
        m = _ROLE_ID.search(cols[0])
        if not m:
            continue                      # header row / separator
        out[ROLE_PREFIX + m.group(1)] = {
            "static": _cell_targets(cols[1]),
            "runtime": _cell_targets(cols[2]),
        }
    return out


def _literal(path: Path, name: str):
    """Read a module-level constant WITHOUT importing (odoo is unavailable)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return ast.literal_eval(node.value)
    raise SystemExit(
        "REFUSING: %s not found in %s — it was renamed or removed, and this "
        "check would silently verify nothing." % (name, path))


def _diff(label: str, role: str, expected: set[str], actual: set[str]) -> list:
    problems = []
    for extra in sorted(actual - expected):
        problems.append(
            "  %s: %s grants %s, which the matrix does not list"
            % (label, role, extra))
    for missing in sorted(expected - actual):
        problems.append(
            "  %s: the matrix lists %s for %s, but the code does not grant it"
            % (label, missing, role))
    return problems


def main() -> int:
    matrix = parse_matrix(MATRIX.read_text(encoding="utf-8"))
    if not matrix:
        print("REFUSING: parsed 0 roles from docs/ROLE_MATRIX.md §2 — the "
              "table's shape changed and every comparison below would be "
              "vacuous.", file=sys.stderr)
        return 1

    implications = {k: set(v) for k, v in _literal(HOOKS, "ROLE_IMPLICATIONS").items()}
    allowed = {k: set(v) for k, v in _literal(TESTS, "MATRIX_ALLOWED_DIRECT").items()}

    problems: list[str] = []

    # 1. runtime column vs hooks.ROLE_IMPLICATIONS. Roles with an empty runtime
    #    cell ("—") legitimately have no entry at all, so compare on the union
    #    of both key sets rather than on either one's keys.
    for role in sorted(set(matrix) | set(implications)):
        doc = matrix.get(role, {}).get("runtime", set())
        code = implications.get(role, set())
        problems += _diff("ROLE_IMPLICATIONS", role, doc, code)

    # 2. both columns vs the test's allowlist.
    for role in sorted(set(matrix) | set(allowed)):
        entry = matrix.get(role, {})
        doc = entry.get("static", set()) | entry.get("runtime", set())
        test = allowed.get(role, set())
        problems += _diff("MATRIX_ALLOWED_DIRECT", role, doc, test)

    if problems:
        print("docs/ROLE_MATRIX.md and the code disagree:", file=sys.stderr)
        for p in problems:
            print(p, file=sys.stderr)
        print("\nThe matrix is the authoritative document. Change it and the "
              "code in the same PR — which is what hooks.py has always "
              "claimed, and what nothing checked until #382.", file=sys.stderr)
        return 1

    print("role matrix clean (%d roles; doc == ROLE_IMPLICATIONS == "
          "MATRIX_ALLOWED_DIRECT)" % len(matrix))
    return 0


# --- self-test -------------------------------------------------------------
# This file parses a markdown table with regexes, which is exactly the kind of
# code that starts matching nothing and reporting success. R8 in invariants.py
# shipped with that defect; check_skips.py shipped with a parser written to
# match a fixture the author had invented. These run before main().
_SELF_TEST_TABLE = """
## 2. The matrix

| Role (xml-id) | Implies (static, base only) | Implies (runtime-linked) | Rationale |
|---|---|---|---|
| **Employee** `group_role_employee` | `base.group_user` | — | floor |
| **Sales** `group_role_sales` | Employee | `sales_team.group_sale_salesman` | scope |
| **Owner** `group_role_owner` | CEO (chain), `base.group_system` | `account.group_account_user` | all |

## 3. next
"""


def _self_test() -> None:
    got = parse_matrix(_SELF_TEST_TABLE)
    assert set(got) == {
        ROLE_PREFIX + "group_role_employee",
        ROLE_PREFIX + "group_role_sales",
        ROLE_PREFIX + "group_role_owner",
    }, got
    emp = got[ROLE_PREFIX + "group_role_employee"]
    assert emp["static"] == {"base.group_user"}, emp
    assert emp["runtime"] == set(), emp          # the em-dash means "none"
    sal = got[ROLE_PREFIX + "group_role_sales"]
    # A bare role word in the static column resolves to that role's xml-id.
    assert sal["static"] == {ROLE_PREFIX + "group_role_employee"}, sal
    assert sal["runtime"] == {"sales_team.group_sale_salesman"}, sal
    own = got[ROLE_PREFIX + "group_role_owner"]
    # "CEO (chain), `base.group_system`" -> both spellings in ONE cell.
    assert own["static"] == {ROLE_PREFIX + "group_role_ceo",
                             "base.group_system"}, own
    # A backticked xml-id must NOT also register as a bare role word.
    assert _cell_targets("`hr.group_hr_user`") == {"hr.group_hr_user"}
    # A cell naming nothing yields nothing rather than raising.
    assert _cell_targets("—") == set()


if __name__ == "__main__":
    _self_test()
    sys.exit(main())
