#!/usr/bin/env python3
"""Make docs/ROLE_MATRIX.md actually authoritative (#382).

WHAT WAS WRONG. `ncollection_core/hooks.py` stated the contract:

    The authoritative human-readable matrix lives in `docs/ROLE_MATRIX.md`;
    the mapping below and that document MUST change together (enforced by the
    transitive-closure test in tests/test_roles.py).

Nothing enforced it. `test_hook_mapping_matches_matrix` and
`test_no_unexpected_escalation` compare `ROLE_IMPLICATIONS` against
`MATRIX_ALLOWED_DIRECT` — a Python dict IN THE SAME TEST FILE. Neither opens
`docs/ROLE_MATRIX.md`. Three sources of truth: two checked against each other,
and the third — the one described as authoritative, and the one a human reads —
checked against nothing. It drifted during #347 and only a reviewer noticed.

WHY THIS IS A SCRIPT AND NOT AN ODOO TEST. #382 proposed "the test parses the
doc". It cannot: tests run inside the odoo container, which mounts only
./custom_addons and ./oca. Verified three ways — docker-compose.yml's odoo
volumes, CI's `docker run` bind mounts, and the production Dockerfile's COPYs.
Implementing it as written would mean shipping docs/ into production images or
moving a human-facing document into an addon. This runs on the host instead,
in CI's lint job and on pre-push, where both files already exist.

IT FAILS CLOSED, AND THAT IS THE WHOLE DESIGN. The first version of this file
scanned cells for backticked xml-ids and a list of bare role words, and silently
contributed NOTHING for anything else. Review proved that reintroduces the very
defect this ticket exists to close:

    `stock.group_stock_user`, crm.group_use_lead_menu   -> {'stock.group_stock_user'}
    Employees                                            -> set()
    Manager (chain, mirrors Owner scope)                 -> {manager, OWNER}

The first two let the document describe a grant the code does not implement
while this script prints "clean". The third invents an edge from prose. So cells
are now parsed against a STRICT GRAMMAR and anything unrecognised is a hard
failure, never an empty set:

    cell   := "—"  |  token ("," token)*
    token  := `module.group_xmlid`            (backticks REQUIRED)
            |  RoleName                        (canonical, from the matrix)
            |  RoleName (qualifier)            (e.g. "CEO (chain)")

Prose belongs in the Rationale column. A clarifying aside inside columns 2-3
now fails loudly with an actionable message rather than being half-understood.

WHAT IT CHECKS. §2 has two "Implies" columns, enforced by different mechanisms,
so they are compared against different things:

    column 2  "static, base only"   -> security/role_groups.xml
    column 3  "runtime-linked"      -> hooks.ROLE_IMPLICATIONS

    doc column 3       == ROLE_IMPLICATIONS      (hooks.py)
    doc columns 2 + 3  == MATRIX_ALLOWED_DIRECT  (tests/test_roles.py)

ONLY §2 IS MACHINE-CHECKED. §3 (inheritance chains), §4 (decisions) and §5
(escalation checklist) are human-maintained prose and are NOT verified here.
§3 in particular restates §2 and can drift silently; that is a known, declared
gap rather than an implied guarantee.

The Odoo test keeps the job only it can do: comparing the allowlist to the group
graph a real database ends up with.

Exit 0 = the three agree. Exit 1 = they do not, or a cell could not be parsed.
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


class MatrixSyntaxError(Exception):
    """A §2 cell does not match the grammar. Never swallowed."""


# Columns 2-3 refer to other roles by DISPLAY NAME ("Employee", "CEO (chain)").
# Derived-vs-hardcoded: this stays hand-written, but _check_role_words_cover()
# asserts every role the table actually defines appears here, so adding a 9th
# role to the document without teaching this map fails instead of silently
# dropping references to it.
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

_ROLE_ID = re.compile(r"`(group_role_\w+)`")
# A backticked xml-id: at least one dot, nothing else in the token.
_TOKEN_XMLID = re.compile(r"^`([A-Za-z_]\w*(?:\.\w+)+)`$")
# A bare role name with an optional BALANCED parenthetical qualifier.
_TOKEN_ROLE = re.compile(r"^([A-Za-z]+)(?:\s*\([^()]*\))?$")
# Spellings that mean "this role implies nothing here".
_NONE_CELL = {"", "—", "–", "-", "n/a", "none"}


def _cell_targets(cell: str, where: str = "a cell") -> set[str]:
    """Every group xml-id a §2 cell names. Raises rather than guessing."""
    text = cell.strip()
    if text.lower() in _NONE_CELL:
        return set()
    out: set[str] = set()
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            raise MatrixSyntaxError(
                "%s: empty entry — a stray or trailing comma in %r" % (where, text))
        m = _TOKEN_XMLID.match(token)
        if m:
            out.add(m.group(1))
            continue
        m = _TOKEN_ROLE.match(token)
        if m:
            role = _ROLE_WORDS.get(m.group(1).lower())
            if role:
                out.add(ROLE_PREFIX + role)
                continue
            raise MatrixSyntaxError(
                "%s: %r is not one of the roles this matrix defines (%s). If it "
                "is a group, wrap it in backticks as `module.xmlid`; if it is "
                "prose, move it to the Rationale column."
                % (where, m.group(1), ", ".join(sorted(_ROLE_WORDS))))
        raise MatrixSyntaxError(
            "%s: cannot parse %r. Columns 2-3 accept only `module.xmlid` in "
            "backticks, a role name, or a role name with a (qualifier) — one "
            "per comma-separated entry. Prose belongs in the Rationale column, "
            "because a half-understood cell is how the document and the code "
            "drift apart while this check reports clean (#382)."
            % (where, token))
    return out


def _check_role_words_cover(roles: set[str]) -> None:
    """Every role the table DEFINES must be nameable in another row's cell."""
    known = {ROLE_PREFIX + v for v in _ROLE_WORDS.values()}
    missing = sorted(roles - known)
    if missing:
        raise MatrixSyntaxError(
            "docs/ROLE_MATRIX.md defines role(s) %s that scripts/ci/"
            "check_role_matrix.py cannot resolve by name. Add them to "
            "_ROLE_WORDS, or a reference to them from another row would be "
            "silently dropped." % ", ".join(missing))


def parse_matrix(text: str) -> dict[str, dict[str, set[str]]]:
    """§2's table -> {role_xmlid: {'static': {...}, 'runtime': {...}}}."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip().startswith("## 2."))
    except StopIteration:
        raise MatrixSyntaxError(
            "docs/ROLE_MATRIX.md has no '## 2.' section — the document was "
            "restructured and this check would otherwise verify nothing.")
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
        role = ROLE_PREFIX + m.group(1)
        out[role] = {
            "static": _cell_targets(cols[1], "%s, static column" % role),
            "runtime": _cell_targets(cols[2], "%s, runtime column" % role),
        }
    _check_role_words_cover(set(out))
    return out


def _literal(path: Path, name: str):
    """Read a module-level constant WITHOUT importing (odoo is unavailable)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = [n for n in tree.body
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    if not found:
        raise MatrixSyntaxError(
            "%s not found in %s — it was renamed or removed, and this check "
            "would otherwise verify nothing." % (name, path))
    if len(found) > 1:
        # Reading only the first would compare against a partial value.
        raise MatrixSyntaxError(
            "%s is assigned %d times at module level in %s. This reader takes "
            "one literal assignment; a second one (or a later .update()) would "
            "be silently ignored." % (name, len(found), path))
    try:
        return ast.literal_eval(found[0].value)
    except (ValueError, TypeError) as exc:
        raise MatrixSyntaxError(
            "%s in %s is no longer a plain literal (%s), so it cannot be read "
            "without importing odoo." % (name, path, exc))


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


def run() -> int:
    matrix = parse_matrix(MATRIX.read_text(encoding="utf-8"))
    if not matrix:
        print("REFUSING: parsed 0 roles from docs/ROLE_MATRIX.md §2 — the "
              "table's shape changed and every comparison below would be "
              "vacuous.", file=sys.stderr)
        return 1

    implications = {k: set(v) for k, v in _literal(HOOKS, "ROLE_IMPLICATIONS").items()}
    allowed = {k: set(v) for k, v in _literal(TESTS, "MATRIX_ALLOWED_DIRECT").items()}

    problems: list[str] = []

    # 1. runtime column vs hooks.ROLE_IMPLICATIONS. Roles whose runtime cell is
    #    "—" legitimately have no entry, so iterate the union of both key sets.
    for role in sorted(set(matrix) | set(implications)):
        doc = matrix.get(role, {}).get("runtime", set())
        problems += _diff("ROLE_IMPLICATIONS", role, doc,
                          implications.get(role, set()))

    # 2. both columns vs the test's allowlist.
    for role in sorted(set(matrix) | set(allowed)):
        entry = matrix.get(role, {})
        doc = entry.get("static", set()) | entry.get("runtime", set())
        problems += _diff("MATRIX_ALLOWED_DIRECT", role, doc,
                          allowed.get(role, set()))

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


def main() -> int:
    try:
        return run()
    except MatrixSyntaxError as exc:
        print("REFUSING: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
