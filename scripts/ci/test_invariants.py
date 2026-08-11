#!/usr/bin/env python3
"""Tests for the invariants guard (#330).

`scripts/ci/` had no automated tests at all. Every proof that these rules work
had been a manual RED pass, re-done from scratch by whoever last touched them —
and that is not a hypothetical cost. While the rules were being written, the
logic broke in ways reading did not catch:

  * #328's R4 reported four fully-covered modules as MISSING, because the list
    was captured with a line-anchored regex and a reflowed `ci.yml` line is
    byte-identical to odoo once bash joins the continuation.
  * #336's R3 fired on a GitHub org and two config filenames.
  * #344's R5 passed `--max-cron-threads=64` — which re-enables the very bug it
    guards — and passed a flag that had been commented out.

Every one was found by RUNNING a scenario. This file makes those scenarios
permanent.

**How this differs from the SELF_TEST inside invariants.py.** That one is
regex-level and runs on every invocation, so a pattern that silently stops
matching cannot print "clean". This one is rule-level: it writes real files into
a throwaway repo and asserts on the findings the rule functions produce. The two
catch different things — a regex can be individually correct while the rule that
uses it looks at the wrong lines.

**Nothing here touches the working tree.** Each test builds a temporary
directory and points `invariants.REPO_ROOT` at it, because the repo is
bind-mounted live into running containers and editing it to prove a point is how
#310 fabricated a CRITICAL that had not happened (CLAUDE.md Rule 14 / R-018).

Run:  python3 scripts/ci/test_invariants.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import invariants  # noqa: E402


class GuardTestCase(unittest.TestCase):
    """Base: a disposable repo root that `invariants` is pointed at."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._real_root = invariants.REPO_ROOT
        invariants.REPO_ROOT = self.root
        self.addCleanup(self._restore)

    def _restore(self):
        invariants.REPO_ROOT = self._real_root
        self._tmp.cleanup()

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def scan(self, rel, content):
        """Findings produced by scanning one file. Empty list means clean."""
        findings = []
        invariants.scan(self.write(rel, content), findings)
        return findings


# ---------------------------------------------------------------------------
# R1 — psql / pg_isready without an explicit -d
# ---------------------------------------------------------------------------
class TestR1PostgresExplicitDb(GuardTestCase):

    def test_psql_without_d_is_flagged(self):
        found = self.scan("s.sh", "psql -U odoo -c 'SELECT 1'\n")
        self.assertEqual(len(found), 1)
        self.assertIn("-d", found[0])

    def test_psql_with_d_is_clean(self):
        self.assertEqual(self.scan("s.sh", "psql -U odoo -d postgres -c 'x'\n"), [])

    def test_dropdb_is_deliberately_not_matched(self):
        """dropdb/createdb default their MAINTENANCE db to postgres and are
        correct without -d — verified live when R1 was written. Flagging them
        would be the cry-wolf failure the module docstring forbids."""
        self.assertEqual(self.scan("s.sh", "dropdb -U odoo --if-exists scratch\n"), [])

    def test_makefile_recipe_lines_are_scanned(self):
        found = self.scan("Makefile", "target:\n\tpsql -U odoo -c 'x'\n")
        self.assertEqual(len(found), 1)

    def test_makefile_phony_list_is_not_scanned(self):
        """`.PHONY: … psql …` names a target; it invokes nothing."""
        self.assertEqual(self.scan("Makefile", ".PHONY: help psql shell\n"), [])


# ---------------------------------------------------------------------------
# R2 — `|| true` on a state-changing docker command
# ---------------------------------------------------------------------------
class TestR2SilentDockerFailure(GuardTestCase):

    def test_literal_docker_compose_up_is_flagged(self):
        found = self.scan("s.sh", "docker compose up -d || true\n")
        self.assertEqual(len(found), 1)

    def test_compose_variable_form_is_flagged(self):
        """#336: R2 matched a LITERAL `docker compose`, and neither place this
        repo actually issues those commands spells it out."""
        found = self.scan("Makefile", "up:\n\t$(COMPOSE) up -d || true\n")
        self.assertEqual(len(found), 1)

    def test_array_variable_form_is_flagged(self):
        found = self.scan("s.sh", '"${DC[@]}" up -d --wait db || true\n')
        self.assertEqual(len(found), 1)

    def test_define_endef_macro_body_is_scanned(self):
        """#336's HIGH: macro bodies carry no leading tab, and `drop_database`
        is such a macro backing every destructive *-clean target."""
        found = self.scan(
            "Makefile",
            "define drop_database\n"
            "docker compose restart odoo || true\n"
            "endef\n")
        self.assertEqual(len(found), 1)

    def test_bare_docker_rm_is_deliberately_allowed(self):
        """Removing a throwaway container in a trap is legitimate best-effort
        cleanup; this repo has six such lines."""
        self.assertEqual(self.scan("s.sh", "docker rm -f scratch || true\n"), [])

    def test_state_change_without_swallowing_is_clean(self):
        self.assertEqual(self.scan("s.sh", "docker compose up -d\n"), [])


# ---------------------------------------------------------------------------
# R3 — hardcoded container / volume names
# ---------------------------------------------------------------------------
class TestR3HardcodedNames(GuardTestCase):

    def setUp(self):
        super().setUp()
        # R3 derives container names from compose files in THIS fixture root.
        #
        # The names are deliberately FICTIONAL. An earlier version used the
        # repo's real ones, which made every case here vacuous twice over:
        # the derived pattern was frozen at import from the real repo (so the
        # fixture was inert), and `ncollection-odoo-bus` contains
        # `ncollection-odoo` as a \b-bounded prefix, so even the pre-#336
        # four-name regex matched it. The test passed under the very
        # regression it was named for. Names nothing outside this tempdir
        # declares cannot fail that way.
        self.write("docker-compose.yml",
                   "services:\n"
                   "  alpha:\n"
                   "    container_name: fixturesvc-alpha\n"
                   "  beta:\n"
                   "    container_name: fixturesvc-beta-worker\n")

    def test_hardcoded_container_is_flagged(self):
        found = self.scan("s.sh", "docker logs fixturesvc-alpha\n")
        self.assertEqual(len(found), 1)
        self.assertIn("container name", found[0])

    def test_name_is_derived_from_the_compose_files(self):
        """#336: the old list named four services while compose declared eight.

        This can ONLY pass if the pattern was built from this fixture, since
        `fixturesvc-beta-worker` appears nowhere in the real repo and in no
        hardcoded list. That is the whole point — the previous version of this
        test passed even with the derivation reverted.
        """
        found = self.scan("s.sh", "docker logs fixturesvc-beta-worker\n")
        self.assertEqual(len(found), 1)

    def test_a_name_no_compose_file_declares_is_not_flagged(self):
        """The other half of derivation: it must not invent names either."""
        self.assertEqual(
            self.scan("s.sh", "docker logs fixturesvc-never-declared\n"), [])

    def test_the_real_repos_names_do_not_leak_into_a_fixture_run(self):
        """Pins the isolation bug itself.

        `ncollection-odoo` is a real container in this repo and is NOT in the
        fixture, so a finding here means the pattern was frozen from the real
        tree rather than derived from the fixture — which is exactly what was
        happening before #330's review caught it.
        """
        self.assertEqual(
            self.scan("s.sh", "docker logs ncollection-odoo\n"), [],
            "the derived pattern must come from the fixture, not the real repo")

    def test_project_prefixed_volume_gets_volume_advice(self):
        found = self.scan("s.sh", "docker volume rm ncollection-erp_pgdata\n")
        self.assertEqual(len(found), 1)
        self.assertIn("volume/network", found[0],
                      "`ps -q <service>` is wrong advice for a volume")

    def test_the_github_org_is_not_a_container(self):
        """#336: a generalised pattern fired on all three of these."""
        self.assertEqual(
            self.scan("s.sh", "docker build -t ghcr.io/ncollection-sys/x:local .\n"), [])

    def test_config_filenames_are_not_containers(self):
        self.assertEqual(
            self.scan("s.sh", "install -m 0644 /etc/fail2ban/ncollection-sshd.local\n"), [])
        self.assertEqual(
            self.scan("s.sh", "install -m 0644 /etc/apt/52ncollection-unattended\n"), [])

    def test_container_name_definition_is_not_a_usage(self):
        self.assertEqual(
            self.scan("docker-compose.dev.yml",
                      "services:\n  odoo:\n    container_name: ncollection-odoo\n"), [])


# ---------------------------------------------------------------------------
# R4 — CI module coverage. The six cases #330 names, verbatim.
# ---------------------------------------------------------------------------
_CI_TEMPLATE = """\
jobs:
  test:
    steps:
      - run: |
          odoo -d test %s \\
               --test-enable \\
               %s \\
               --stop-after-init
"""


class TestR9WorkflowActionsShaPinned(GuardTestCase):
    """R9 — a `uses:` on a mutable tag runs code someone else can change.

    Written after finding canary.yml on `actions/checkout@v4` while every other
    workflow SHA-pinned. Fixing that one line without a rule would leave the
    class open: the next hand-written step is just as likely to paste a tag.
    """

    SHA = "a" * 40

    def _findings(self):
        found = []
        invariants.rule_workflow_actions_sha_pinned(found)
        return found

    def _wf(self, body):
        self.write(invariants.WORKFLOW_DIR + "/w.yml", body)

    def test_a_sha_pin_is_clean(self):
        self._wf("jobs:\n  a:\n    steps:\n"
                 "      - uses: actions/checkout@%s # v7.0.1\n" % self.SHA)
        self.assertEqual(self._findings(), [])

    def test_a_mutable_tag_is_flagged(self):
        self._wf("jobs:\n  a:\n    steps:\n"
                 "      - uses: actions/checkout@v4\n")
        found = self._findings()
        self.assertEqual(len(found), 1, found)
        self.assertIn("not pinned", found[0])

    def test_a_branch_reference_is_flagged(self):
        self._wf("jobs:\n  a:\n    steps:\n"
                 "      - uses: evil/action@main\n")
        self.assertEqual(len(self._findings()), 1)

    def test_a_short_sha_is_flagged(self):
        """7 hex chars is still ambiguous and still resolvable to a tag move."""
        self._wf("jobs:\n  a:\n    steps:\n"
                 "      - uses: actions/checkout@3d3c42e\n")
        self.assertEqual(len(self._findings()), 1)

    def test_a_local_reusable_workflow_is_exempt(self):
        """`uses: ./.github/...` is our own file at our own commit."""
        self._wf("jobs:\n  a:\n    uses: ./.github/workflows/verify.yml\n")
        self.assertEqual(self._findings(), [])

    def test_no_workflow_files_reports_rather_than_passing(self):
        """A guard aimed at nothing must not be able to report clean."""
        (self.root / invariants.WORKFLOW_DIR).mkdir(parents=True, exist_ok=True)
        found = self._findings()
        self.assertEqual(len(found), 1, found)
        self.assertIn("verified", found[0])


class TestR4CiModuleCoverage(GuardTestCase):

    def _repo(self, modules=("mod_a", "mod_b"), install=None, tags=None):
        for module in modules:
            self.write("custom_addons/%s/__manifest__.py" % module, "{}\n")
        install = "-i " + ",".join(modules) if install is None else install
        tags = ("--test-tags " + ",".join("/" + m for m in modules)
                if tags is None else tags)
        self.write(invariants.CI_WORKFLOW, _CI_TEMPLATE % (install, tags))

    def _findings(self):
        found = []
        invariants.rule_ci_module_coverage(found)
        return found

    def test_unmodified_workflow_is_clean(self):
        self._repo()
        self.assertEqual(self._findings(), [])

    def test_list_reflowed_at_a_comma_unindented_is_clean(self):
        """THE false positive #328 shipped, and the exact shape that is safe.

        Bash removes `\\<newline>` and nothing else, so an UNINDENTED
        continuation yields one argument — verified: `-i mod_a,\\<nl>mod_b`
        gives bash `[-i] [mod_a,mod_b]`. odoo receives an identical string, so
        the guard must stay silent. A line-anchored regex saw only the first
        physical line and reported both modules missing.
        """
        self.write("custom_addons/mod_a/__manifest__.py", "{}\n")
        self.write("custom_addons/mod_b/__manifest__.py", "{}\n")
        self.write(invariants.CI_WORKFLOW, _CI_TEMPLATE % (
            "-i mod_a,\\\nmod_b",
            "--test-tags /mod_a,\\\n/mod_b"))
        self.assertEqual(
            self._findings(), [],
            "an unindented reflow is byte-identical once bash joins it")

    def test_list_reflowed_with_indentation_is_correctly_flagged(self):
        """The other half, which invariants.py's own comment glosses over.

        Indent the continuation and it is NOT byte-identical: bash keeps the
        leading spaces, so `-i mod_a,\\<nl>    mod_b` word-splits into
        `[-i] [mod_a,] [mod_b]` — verified against real bash. odoo never
        receives `mod_b` in the install list and its tests are genuinely
        inert, so flagging it is CORRECT, not a false positive.

        This case exists because the natural way to reflow a long line inside a
        YAML block scalar is to indent it, which is the dangerous form. The
        guard catching it is the feature.
        """
        self.write("custom_addons/mod_a/__manifest__.py", "{}\n")
        self.write("custom_addons/mod_b/__manifest__.py", "{}\n")
        self.write(invariants.CI_WORKFLOW, _CI_TEMPLATE % (
            "-i mod_a,\\\n               mod_b",
            "--test-tags /mod_a,\\\n               /mod_b"))
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("mod_b", found[0])

    def test_module_dropped_from_test_tags_only(self):
        self._repo(tags="--test-tags /mod_a")
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("--test-tags", found[0])
        self.assertNotIn("-i and", found[0])

    def test_module_dropped_from_both_lists(self):
        self._repo(install="-i mod_a", tags="--test-tags /mod_a")
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("-i", found[0])
        self.assertIn("--test-tags", found[0])

    def test_unfindable_lists_give_exactly_one_finding(self):
        """Not one per module. An avalanche buries the real problem, which is
        that the workflow changed shape."""
        self.write("custom_addons/mod_a/__manifest__.py", "{}\n")
        self.write("custom_addons/mod_b/__manifest__.py", "{}\n")
        self.write(invariants.CI_WORKFLOW, "jobs:\n  test:\n    steps: []\n")
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("could not locate", found[0])

    def test_a_covered_module_left_in_the_exemption_list_is_stale(self):
        self._repo()
        original = invariants.CI_EXEMPT_MODULES
        invariants.CI_EXEMPT_MODULES = {"mod_a": "reason"}
        self.addCleanup(setattr, invariants, "CI_EXEMPT_MODULES", original)
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("stale exemption", found[0])


# ---------------------------------------------------------------------------
# R5 — the dev/routing cron-threads flag (#344)
# ---------------------------------------------------------------------------
class TestR5CronThreadsScoped(GuardTestCase):

    def _compose(self, dev_cmd, routing_cmd):
        self.write("docker-compose.dev.yml",
                   "services:\n  odoo:\n    command: %s\n" % dev_cmd)
        self.write("docker-compose.routing.yml",
                   "services:\n  odoo:\n    command: %s\n" % routing_cmd)

    def _findings(self):
        found = []
        invariants.rule_cron_threads_scoped(found)
        return found

    def test_both_flags_present_is_clean(self):
        self._compose("odoo --max-cron-threads=0",
                      '["odoo", "--max-cron-threads=0"]')
        self.assertEqual(self._findings(), [])

    def test_the_documented_escape_hatch_is_accepted(self):
        self._compose("odoo --max-cron-threads=${NC_DEV_CRON_THREADS:-0}",
                      '["odoo", "--max-cron-threads=0"]')
        self.assertEqual(self._findings(), [],
                         "the hatch defaults to 0 and must keep passing")

    def test_missing_flag_is_flagged(self):
        self._compose("odoo --log-level=debug",
                      '["odoo", "--max-cron-threads=0"]')
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("no longer passes", found[0])

    def test_a_nonzero_count_does_not_scope_cron(self):
        """#344's review: `=64` caps concurrency, not which databases tick."""
        self._compose("odoo --max-cron-threads=0",
                      '["odoo", "--max-cron-threads=64"]')
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("does not scope cron", found[0])

    def test_a_commented_out_flag_does_not_count(self):
        """The substring survives in the comment; the command does not have it."""
        self._compose("odoo --log-level=debug  # was --max-cron-threads=0",
                      '["odoo", "--max-cron-threads=0"]')
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("no longer passes", found[0])

    def test_an_unreadable_command_reports_going_blind(self):
        """A guard that quietly stops seeing what it guards is worse than one
        that is merely incomplete — the same choice R4 makes."""
        self.write("docker-compose.dev.yml",
                   "services:\n  odoo:\n    command:\n      - odoo\n")
        self.write("docker-compose.routing.yml",
                   'services:\n  odoo:\n    command: ["odoo", "--max-cron-threads=0"]\n')
        found = self._findings()
        self.assertEqual(len(found), 1)
        self.assertIn("could not find", found[0])


# ---------------------------------------------------------------------------
# The guard's own self-test must stay honest
# ---------------------------------------------------------------------------
class TestSelfTest(GuardTestCase):

    def test_the_embedded_self_test_passes(self):
        """invariants.py aborts before scanning if its own patterns regress.
        If that ever fails, every other result in this file is meaningless."""
        self.assertEqual(invariants.run_self_test(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
