#!/usr/bin/env python3
"""Deterministic architecture-rule checks for a PR diff.

Not a linter substitute (flake8/pylint-odoo already run separately) and not
an LLM reviewer — this catches the specific regression classes that
NCollection ERP's architecture docs exist to prevent:

  1. Deprecated Odoo 19 view syntax (`<tree>` instead of `<list>`, `attrs=`)
  2. Menu-only license/role gating with no matching ORM/RPC-layer check
     (docs/markdown/ARCHITECTURE_SECURITY.md §4 — Defense in Depth)
  3. Platform-layer code touching tenant-layer models directly instead of
     going through RPC/JSON-RPC (docs/markdown/DELIVERABLE_1_SYSTEM_DESIGN.md §7,
     the two-layer separation rule)
  4. Hardcoded secrets (belt-and-suspenders; GitHub secret scanning is the
     primary control, this is a fast local backstop)

LIMITATIONS (read before trusting this as a hard gate):
  - This is regex/substring matching, not an AST or RPC-boundary analysis.
    Rule 2 only proves "a Python file in the same addon changed," not that
    it contains a matching enforcement change — treat a pass as a hint, not
    proof, and still spot-check licensing-relevant PRs by eye.
  - Rule 3's model list is a fixed set of common tenant models, not
    exhaustive — dynamic model names (`self.env[var]`), aliased `env`
    variables, and XML-defined server actions are not caught.
  - A future Rule (`ir.rule` records cross-referenced against a role matrix
    doc) is intentionally NOT implemented yet — docs/markdown/ROLE_MATRIX.md
    does not exist in the repo as of this writing.

Usage:
    python scripts/ci/architecture_guard.py --base origin/develop
    python scripts/ci/architecture_guard.py --base origin/main
    python scripts/ci/architecture_guard.py --files a.py b.xml  # explicit file list

Exit code 0 = clean, 1 = violations found OR the diff could not be computed
(fails closed — see changed_files()).
"""

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Rule 1: deprecated Odoo 19 view syntax
# ---------------------------------------------------------------------------
TREE_TAG_RE = re.compile(r"<tree\b")
# Non-greedy so two search views in one file are two regions, not one spanning
# region that would swallow the legitimate markup between them.
SEARCH_REGION_RE = re.compile(r"<search\b.*?</search\s*>", re.DOTALL)
SEARCH_GROUP_RE = re.compile(r"<group\b([^>]*)>")
ATTR_NAME_RE = re.compile(r"([A-Za-z_][\w.-]*)\s*=")

# The ONLY attributes Odoo 19 permits on a `<group>` inside a `<search>`.
#
# Read off the schema, not guessed: `base/rng/search_view.rng` includes
# `common.rng`, whose `group` define lists colspan/rowspan/fill/height/width/
# name/color/invisible, plus `position`+`name` from the `overload` define and
# `groups` from `access_rights`. Anything else makes the RelaxNG validator
# reject the WHOLE view, so the module fails to INSTALL rather than degrade.
#
# `string` and `expand` are both absent, which is the practical point: the two
# spellings people actually write are both fatal. Verified against the pinned
# odoo:19 image — `<group/>` and `<group><filter/></group>` validate;
# `<group string="G">` and `<group expand="0">` are both REJECTED.
#
# Forms are unaffected and this rule is scoped to search regions anyway: the
# rng directory ships no form_view.rng at all, which is why `<group
# string="Measurement">` in a form installs perfectly well.
SEARCH_GROUP_ALLOWED_ATTRS = frozenset({
    "colspan", "rowspan", "fill", "height", "width", "name", "color",
    "invisible", "position", "groups",
})
ATTRS_RE = re.compile(r"\battrs\s*=")

# Rule 6 is about MARKUP, and a comment is not markup (#348).
#
# The guard used to match these patterns as raw text, so a comment DOCUMENTING
# the rule failed the rule. Hit for real while writing #346:
#
#     <!-- P5-T04 alerts. Odoo 19: <list>, never <tree>; no attrs=. -->
#
# produced two violations on that line while the view below it was correct
# throughout. The workaround is to delete the explanation, which is the wrong
# direction — a guard that punishes writing the convention down next to the
# code it governs teaches people to remove it.
#
# Bodies are blanked rather than removed so LINE NUMBERS survive: findings
# report `path:line`, and shifting them would send readers to the wrong place.
#
# DELIBERATELY NOT APPLIED TO check_secrets. A commented-out credential is
# still a credential sitting in the repository; only the SYNTAX rules care
# whether the text is live markup.
#
# An unterminated `<!--` matches nothing here, so such a file is scanned
# exactly as before — which fails SAFE: the worst case is the false positive
# this ticket removes, never a missed violation. For addon XML the file is also
# rejected outright by CI's `Validate XML (well-formedness)` step (xmllint over
# custom_addons/); outside custom_addons nothing validates it, which is why the
# conservative direction here matters rather than being a formality.
#
# CDATA IS BLANKED TOO, and for the same reason rather than as an extra. A
# `<![CDATA[...]]>` body is character DATA — the parser hands it to Odoo as a
# string, never as view markup — so Rule 6 has nothing to say about a `<tree>`
# spelled inside one.
#
# It also closes a hole. Inside CDATA, `<!--` is ordinary text, so an
# unterminated one there is NOT malformed XML and gets no backstop from the
# well-formedness gate; a comments-only pass would run from it past `]]>` to
# the next real `-->` and blank whatever live markup sat in between. Both
# constructs therefore go in ONE alternation: the scan is left to right, so
# whichever opens first consumes the other, and neither can start inside the
# other. Two sequential passes would reintroduce exactly that bug.
#
# PROCESSING INSTRUCTIONS are the third member of the set, for the identical
# reason. A PI's content runs to the first `?>` and is otherwise unconstrained,
# so `<?example note <!-- ?>` is well-formed XML containing a literal `<!--`
# that no parser reads as a comment. Same hole as CDATA, same fix. (The XML
# declaration `<?xml ... ?>` is a PI too and gets blanked; it carries no view
# markup, so nothing is lost.)
#
# The general rule is now visible: `<` opens something inert whenever the next
# character is `!` or `?`. Anything added to XML with that shape belongs in
# this alternation. What CANNOT be faked from live markup is an inert OPEN,
# because a raw `<` is illegal inside an attribute value in well-formed XML —
# that is the guarantee the whole approach rests on, and it survives here. A
# stray `-->` in an attribute IS legal, but the non-greedy match ends at the
# first close after a genuine open, so it cannot pull live markup in.
XML_INERT_RE = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>", re.DOTALL)


def without_inert_xml_text(text: str) -> str:
    """Blank inert-XML bodies, preserving line count and numbering."""
    return XML_INERT_RE.sub(_blank_but_keep_line_breaks, text)


def _blank_but_keep_line_breaks(match: "re.Match[str]") -> str:
    """Space-fill a matched span, leaving its line breaks in place.

    Line breaks are found with `splitlines` — the SAME function that assigns
    the line numbers in the findings — rather than by listing separators here.
    A hand-written list would drift: `str.splitlines` breaks on `\\r`, `\\v`,
    `\\f`, `\\x1c`-`\\x1e`, `\\x85`, `\\u2028` and `\\u2029` as well as `\\n`, and
    an earlier version blanked everything but `\\n`, so a bare-CR file
    collapsed each comment to one line and misreported every finding below it
    by the difference. Deriving from the numbering function makes the two
    incapable of disagreeing.
    """
    out = []
    for line in match.group(0).splitlines(keepends=True):
        content = line.splitlines()[0]          # the line minus its break
        out.append(" " * len(content) + line[len(content):])
    return "".join(out)


# ---------------------------------------------------------------------------
# Rule 2: menu hiding without matching ORM/RPC enforcement
# A changed view/menu file that adds `groups=` restrictions is fine on its
# own, but if a PR touches menu-visibility for a *licensed* feature area and
# doesn't touch any Python access-control file in the same addon, flag it
# for human review rather than silently trusting UI-only gating.
# ---------------------------------------------------------------------------
LICENSE_MENU_HINT_RE = re.compile(r"groups=[\"'][\w.]*license", re.IGNORECASE)
ORM_ENFORCEMENT_HINTS = ("check_access_rights", "ir.rule", "_check_company", "AccessError")

# ---------------------------------------------------------------------------
# Rule 3: two-layer separation — platform layer (ncollection_saas,
# ncollection_subscription) must not import/query tenant ERP models
# directly. Cross-layer communication goes through RPC/JSON-RPC per
# DELIVERABLE_1 §7. List is not exhaustive (see LIMITATIONS in the module
# docstring) — covers the highest-traffic tenant models.
# ---------------------------------------------------------------------------
PLATFORM_ADDONS = {"ncollection_saas", "ncollection_subscription", "ncollection_billing",
                   "ncollection_reseller"}
TENANT_MODELS = (
    "sale.order", "stock.move", "account.move", "purchase.order",
    "res.partner", "account.move.line", "crm.lead", "hr.employee",
    "product.product", "product.template", "stock.picking",
)
# `cls.env[...]` as well as `self.env[...]`: setUpClass fixtures spell the
# lookup the other way, and matching only `self.` meant four identical
# res.partner creates in ncollection_reseller's own setUpClass methods were
# never flagged -- while the two that used `self.` failed the mainline. A guard
# whose coverage depends on which alias a test happens to use reports "clean"
# for the wrong reason (#304). Aliased locals (`env = self.env`) are still
# missed; that limitation is documented at the top of this file and unchanged.
TENANT_MODEL_HINTS = tuple(
    f"{recv}.env[{q}{m}{q}]"
    for m in TENANT_MODELS for q in ("'", '"') for recv in ("self", "cls")
)

# ---------------------------------------------------------------------------
# Rule 4: obvious hardcoded secrets
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|password|token)\s*=\s*[\"'][^\"'\s]{8,}[\"']"),
    re.compile(r"sk_live_[0-9a-zA-Z]{16,}"),   # Stripe live secret key
    re.compile(r"AKIA[0-9A-Z]{16}"),           # AWS access key id
]
# Matched per potential-secret occurrence, not per whole line, so a real
# secret sharing a line with an unrelated comment containing one of these
# words is still caught.
SECRET_ALLOW_HINTS = ("os.environ", "getenv", "example", "placeholder", "xxx", "changeme")


def changed_files(base: str) -> list[Path]:
    # --diff-filter=d: exclude deletions — a deleted file cannot violate rules,
    # and after P1-T04 the de-vendored oca/ paths may exist on disk again
    # (regenerated by `make oca`) without being OUR code to lint.
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", f"{base}...HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"error: git diff against {base} failed:\n{result.stderr}", file=sys.stderr)
        print("architecture-guard cannot verify this PR — failing closed.", file=sys.stderr)
        sys.exit(1)
    return [
        REPO_ROOT / p
        for p in result.stdout.splitlines()
        # oca/ is generated external OCA code (repos.yml pins) — never ours to lint.
        if p.strip() and not p.startswith("oca/")
    ]


def addon_of(path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(REPO_ROOT / "custom_addons")
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def check_view_syntax(path: Path, text: str, findings: list[str]) -> None:
    if path.suffix != ".xml":
        return
    # Blank once: the line rules and the region rule below must agree about
    # what counts as live markup, and blanking twice would just be slower.
    live = without_inert_xml_text(text)
    for i, line in enumerate(live.splitlines(), 1):
        if TREE_TAG_RE.search(line):
            findings.append(f"{path}:{i}: uses <tree> — Odoo 19 requires <list> (Rule 6)")
        if ATTRS_RE.search(line):
            findings.append(f"{path}:{i}: uses deprecated attrs= — use direct field conditions (Rule 6)")
    _check_search_group_expand(path, live, findings)


def _check_search_group_expand(path: Path, live: str, findings: list[str]) -> None:
    """A `<group>` inside a `<search>` carrying a disallowed attribute (#353).

    NOT a style rule. Odoo 19's RelaxNG validator rejects the WHOLE view for
    this attribute, so the module fails to install outright rather than
    degrading. Hit for real in #61: the fix was to flatten to `<separator/>`,
    the shape ncollection_saas/views/backup_views.xml already uses.

    Nothing caught it before. `architecture_guard` had no `expand` rule at all,
    and CI's `Validate XML (well-formedness)` step runs `xmllint --noout` —
    the file IS well-formed; the violation is schema-level. So the first
    signal was a failed module install, minutes into the `test` job.

    WHY THIS ONE IS REGION-SCOPED AND THE OTHERS ARE PER LINE. `<group>` is
    perfectly legitimate in a `<form>`, and `expand` is legitimate on other
    elements — only the combination inside a `<search>` is fatal. A per-line
    rule cannot express "inside a search view" and would either miss it or
    fail every form in the repo.

    Deliberately a REGEX over the region rather than an ElementTree parse.
    Parsing would be more precise, and is only safe on well-formed XML — which
    is not guaranteed for `.xml` outside custom_addons/, where nothing
    validates well-formedness (see the XML_INERT_RE block). Failing to parse
    would mean either crashing the guard or silently skipping the file; a
    regex over the same text the other Rule-6 checks read keeps the failure
    mode consistent with them. An unterminated `<search>` matches no region
    and is simply not scanned — the same fail-safe direction as an
    unterminated comment.
    """
    for region in SEARCH_REGION_RE.finditer(live):
        for m in SEARCH_GROUP_RE.finditer(region.group(0)):
            bad = sorted({
                a for a in ATTR_NAME_RE.findall(m.group(1))
                if a not in SEARCH_GROUP_ALLOWED_ATTRS
            })
            if not bad:
                continue
            offset = region.start() + m.start()
            line = live.count("\n", 0, offset) + 1
            findings.append(
                f"{path}:{line}: <group {bad[0]}=...> inside a "
                f"<search> — Odoo 19's schema allows none of {bad} on a search "
                f"group, and RelaxNG rejects the WHOLE view, so the module "
                f"fails to INSTALL rather than degrade. Flatten the search view "
                f"and use <separator/> between filter groups, as "
                f"ncollection_saas/views/backup_views.xml does (Rule 6)")


def check_menu_license_gate(path: Path, text: str, changed_paths: set[Path], findings: list[str]) -> None:
    # Inert text blanked here too (#348). Rule 2 is about a menu that IS
    # license-gated; a comment EXPLAINING license gating is not a menu, and
    # tripping on one is the same defect this ticket fixes for Rule 6 — found
    # in the same file by the review of that fix. Rules 3 and 4 are untouched:
    # Rule 3 only reads `.py`, and for Rule 4 a commented-out credential is
    # still a credential in the repository (see check_secrets).
    if path.suffix != ".xml" or not LICENSE_MENU_HINT_RE.search(
            without_inert_xml_text(text)):
        return
    addon = addon_of(path)
    if addon is None:
        return
    # Require an actual enforcement-hint string (check_access_rights, ir.rule,
    # AccessError, ...) inside a changed .py file in the same addon — not just
    # "some Python file changed nearby", which proves nothing about intent.
    addon_py_enforcement_changed = any(
        addon_of(p) == addon and p.suffix == ".py"
        and any(hint in p.read_text(encoding="utf-8", errors="ignore") for hint in ORM_ENFORCEMENT_HINTS)
        for p in changed_paths if p.exists()
    )
    if not addon_py_enforcement_changed:
        findings.append(
            f"{path}: license-gated menu/group changed with no matching ORM-enforcement "
            f"hint ({', '.join(ORM_ENFORCEMENT_HINTS)}) in any changed Python file in "
            f"'{addon}' — UI hiding without ORM/RPC enforcement is not security "
            f"(ARCHITECTURE_SECURITY.md §4, Ring 2). Verify a matching "
            f"check_access_rights/ir.rule change exists, or justify in the PR description."
        )


# Scoped escape hatch. The two-layer rule forbids platform code from reaching
# into a *tenant* database's ERP models. It does NOT forbid the platform from
# using its OWN Odoo models in the ADMIN DB — e.g. billing its tenants for their
# subscriptions with account.move (DELIVERABLE_1 §2.5 places "billing" in the
# admin DB). The regex can't tell the two apart, so a line may opt out with this
# exact trailing marker — and ONLY that line. Every other tenant-model reference,
# and every UNANNOTATED account.move, still fails. A reviewer must confirm each
# annotated line is genuine admin-DB own-data (never a tenant DB).
# Two accepted spellings. `admin-db-billing` is the original and stays valid so
# the existing annotations keep working; `admin-db-platform` is domain-neutral,
# because "billing" was becoming a lie -- ncollection_reseller's partner rows
# are platform-owned data with nothing to do with billing, and a future auditor
# grepping the billing marker to review billing carve-outs would snag them.
ADMIN_DB_MARKERS = (
    "# arch-guard: admin-db-billing",
    "# arch-guard: admin-db-platform",
)


def check_two_layer_separation(path: Path, text: str, findings: list[str]) -> None:
    addon = addon_of(path)
    if addon not in PLATFORM_ADDONS or path.suffix != ".py":
        return
    for i, line in enumerate(text.splitlines(), 1):
        if any(marker in line for marker in ADMIN_DB_MARKERS):
            continue  # explicitly-annotated admin-DB own-data line (see above)
        if any(hint in line for hint in TENANT_MODEL_HINTS):
            findings.append(
                f"{path}:{i}: platform-layer addon '{addon}' directly references a "
                f"tenant ERP model — cross-layer access must go through RPC/JSON-RPC, "
                f"not direct ORM calls into a tenant database (two-layer separation rule). "
                f"If this is the platform's OWN admin-DB data (e.g. subscription "
                f"billing), append '{ADMIN_DB_MARKERS[-1]}' to the line."
            )


def check_secrets(path: Path, text: str, findings: list[str]) -> None:
    if path.suffix not in (".py", ".xml", ".yml", ".yaml", ".json", ".env", ""):
        return
    for i, line in enumerate(text.splitlines(), 1):
        for pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            # Only exempt if the allow-hint sits near the actual match, not
            # merely anywhere on the line — a real secret sharing a line with
            # an unrelated "# example" comment must still be caught.
            window = line[max(0, match.start() - 20):match.end() + 20].lower()
            if any(hint in window for hint in SECRET_ALLOW_HINTS):
                continue
            findings.append(f"{path}:{i}: looks like a hardcoded secret — use env vars / secrets manager")
            break


def check_test_collectability(path: Path, text: str, findings: list[str]) -> None:
    """Every test class must be one CI's --test-tags actually selects (#286).

    CI runs `--test-tags /ncollection_core,/ncollection_saas,...` — MODULE
    scoped, no class. That is why a test method landing in the wrong class is
    still caught: the class is collected, the method runs, it errors. Verified
    by planting one and watching CI's tag form report it while a class-filtered
    local run stayed silent at exit 0.

    So this rule does NOT re-implement collection. It defends the property that
    makes CI's coverage true in the first place: every test class carries a
    @tagged whose PHASE the module-scoped run selects. Today all 77 classes use
    `post_install, -at_install`. A class with no @tagged, or tagged out of both
    phases, or marked -standard, would run NOWHERE and no count would move.

    Deliberately narrow. Odoo's own tag algebra is richer than this; the point
    is to catch drift away from a uniformity that currently holds, not to
    model the resolver.
    """
    if path.suffix != ".py" or "/tests/" not in path.as_posix():
        return
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return  # flake8 owns syntax; do not double-report

    def is_test_case(node: ast.ClassDef) -> bool:
        for b in node.bases:
            name = b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
            if name.endswith(("TransactionCase", "HttpCase", "SingleTransactionCase")):
                return True
        return False

    def tag_args(node: ast.ClassDef) -> list[str] | None:
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "tagged":
                return [a.value for a in d.args if isinstance(a, ast.Constant)
                        and isinstance(a.value, str)]
            if isinstance(d, ast.Name) and d.id == "tagged":
                return []
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not is_test_case(node):
            continue
        methods = [n.name for n in node.body
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
        if not methods:
            continue
        tags = tag_args(node)
        if tags is None:
            findings.append(
                f"{path}:{node.lineno}: test class {node.name} has {len(methods)} "
                f"test method(s) and no @tagged — every other test class in this "
                f"repo declares one, and an untagged class silently changes which "
                f"phase runs it (#286)")
            continue
        if "-standard" in tags:
            findings.append(
                f"{path}:{node.lineno}: test class {node.name} is tagged "
                f"'-standard', so CI's --test-tags never selects it and its "
                f"{len(methods)} test(s) run nowhere (#286)")
            continue
        if "post_install" not in tags and "at_install" not in tags:
            findings.append(
                f"{path}:{node.lineno}: test class {node.name} declares neither "
                f"'post_install' nor 'at_install' ({tags!r}); it belongs to no "
                f"run phase, so its {len(methods)} test(s) never execute (#286)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="git ref to diff against")
    parser.add_argument("--files", nargs="*", help="explicit file list instead of git diff")
    args = parser.parse_args()

    paths = [Path(f) for f in args.files] if args.files else changed_files(args.base)
    paths = [p for p in paths if p.exists() and p.is_file()]
    path_set = set(paths)

    findings: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        check_view_syntax(path, text, findings)
        check_menu_license_gate(path, text, path_set, findings)
        check_two_layer_separation(path, text, findings)
        check_secrets(path, text, findings)
        check_test_collectability(path, text, findings)

    if findings:
        print("architecture-guard: violations found\n")
        for f in findings:
            print(f"  ✗ {f}")
        print(f"\n{len(findings)} violation(s). Fix or justify in the PR description, then re-run.")
        return 1

    # Be explicit about the empty case. A local pre-commit run legitimately sees
    # 0 files (untracked files are not in `git diff`), and a bare "clean" there
    # reads like a passing review when nothing was actually inspected.
    if not paths:
        print(
            f"architecture-guard: nothing to check — 0 changed files vs {args.base}.\n"
            "  NOTE: untracked files are NOT diffed. If you expected coverage here, "
            "commit them first."
        )
        return 0

    print(f"architecture-guard: clean ({len(paths)} file(s) checked).")
    print(
        "  scope: secrets + XML syntax on every changed file; menu/license gate + "
        "two-layer separation on custom_addons/ only.\n"
        "  infra surfaces (shell, compose, workflows) are covered by "
        "scripts/ci/invariants.py."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
