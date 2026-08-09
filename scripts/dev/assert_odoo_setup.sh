#!/usr/bin/env bash
# =============================================================================
# assert_odoo_setup.sh <setup.log> <what> [hint]
#
# Refuse to continue when an Odoo install/upgrade FAILED WHILE EXITING 0.
#
# WHY THIS EXISTS. Odoo returns 0 having skipped a module whose dependency it
# could not find. Measured, with an empty /mnt/oca-addons:
#
#     odoo exit code: 0
#     ERROR ... Some modules are not loaded ... missing: ['queue_job']
#     ERROR ... Some modules have inconsistent states ...: ['ncollection_saas']
#
# So `if ! odoo ...; then refuse; fi` — which most harnesses here already do —
# is NOT sufficient. It was not sufficient in verify_cron_starvation.sh: setup
# "succeeded", the cron-arming step then also succeeded (env.ref reads cron rows
# that survive from an earlier good install), and the measurement waited out its
# full 120s for a cron that could never fire, reporting it as STARVATION. That
# cost an invalid cross-suite gate run and a regression very nearly filed
# against a healthy develop (#385).
#
# ci.yml already learned this shape for TEST runs — "Odoo can exit 0 on some
# test failures", hence its traceback gate. This is the same lesson applied to
# SETUP, where nothing was checking.
#
# ONE IMPLEMENTATION ON PURPOSE. Six harnesses share this failure mode; six
# copies of the check would drift, and the copy that drifts is the one nobody
# notices. Callers pass their own log and a description.
#
# Usage, after a setup step that already had its exit status checked:
#
#     odoo -d "$DB" -u some_module ... >"$WORK/setup.log" 2>&1 || { ...; exit 1; }
#     "$REPO/scripts/dev/assert_odoo_setup.sh" "$WORK/setup.log" \
#         "some_module on $DB" "run 'make oca' if ./oca is empty"
#
# SCOPE CAVEAT — the markers are DATABASE-WIDE, not per-module (#388). When
# odoo runs with -i/-u it builds its graph from EVERY installed module on that
# database, with no filter on the names you passed (loading.py):
#
#     env.cr.execute("SELECT name from ir_module_module WHERE state IN %s", ...)
#
# so `Some modules are not loaded`, `inconsistent states` and `not installable,
# skipped` can all fire because of a module this command never mentioned.
# (`invalid module names, ignored` is the exception — correctly scoped to the
# requested names.) The cost lands on PERSISTENT databases: if `saastest` ever
# picks up one module stuck at `to install`, every later `-u ncollection_saas`
# on it refuses on a healthy upgrade. Measured when this shipped: 0 such rows
# on saastest and ncplatform, so the precondition is real but unmet. #388
# tracks scoping it properly.
#
# Exit 0 = the setup really did load. Exit 1 = it did not; measuring now would
# produce a confident, precise, wrong answer about the layer under test.
# =============================================================================
set -euo pipefail

log="${1:?usage: assert_odoo_setup.sh <setup.log> <what> [hint]}"
what="${2:?usage: assert_odoo_setup.sh <setup.log> <what> [hint]}"
hint="${3:-}"

if [ ! -r "$log" ]; then
  # Fail closed. A missing log means nothing was verified, and reporting "fine"
  # over an absent file is how a guard becomes decoration.
  echo "REFUSING: $log is unreadable, so '$what' could not be verified." >&2
  exit 1
fi

# Odoo's own wording, from odoo/modules/loading.py and module_graph.py. There
# are TWO distinct silent-skip shapes and the first version of this guard only
# knew one of them:
#
#   * a DEPENDENCY of an installed module is missing -> ERROR "Some modules are
#     not loaded" + "inconsistent states". This is the queue_job/empty-oca case
#     that #385 was filed for.
#   * the module NAME itself is unknown, or its manifest says installable:False
#     -> WARNING ONLY. Reproduced live:
#
#         $ odoo -d db -i totally_nonexistent_module_xyz --stop-after-init
#         exit: 0
#         WARNING ... invalid module names, ignored: totally_nonexistent_module_xyz
#         INFO ... Modules loaded.
#
#     No ERROR, no CRITICAL, no traceback — odoo installs base's auto-deps and
#     reports success. That fires on any FIRST install of a module (no
#     ir_module_module row to short-circuit the name check), which is exactly
#     what verify_financial_bootstrap, verify_upgrade's build_fixture and the
#     e2e fresh-install path all do. A bad bind mount, a wrong addons_path or a
#     typo in a MODULES= variable all land here. Found by review after the first
#     version shipped with only the ERROR-level markers.
#
# CRITICAL is anchored to Odoo's log-level COLUMN, not matched as a bare word:
# this codebase uses `severity='critical'` as an enum value and has a test named
# test_completely_out_of_stock_is_critical, both of which appear verbatim in
# real logs. An unanchored match would refuse a healthy run the moment a suite
# added --test-enable.
if grep -qE "Some modules are not loaded|inconsistent states|invalid module names|not installable, skipped|Traceback \(most recent call last\)|^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:,]+ [0-9]+ CRITICAL " "$log"; then
  echo "REFUSING: $what did not actually load — odoo exited 0 but reported the" >&2
  echo "  module skipped, inconsistent, or a traceback. Continuing would measure" >&2
  echo "  something that cannot happen and report the SYMPTOM as a failure of" >&2
  echo "  whatever this suite tests." >&2
  [ -n "$hint" ] && echo "  Hint: $hint" >&2
  grep -E "not loaded|inconsistent states|invalid module names|not installable|Traceback|CRITICAL" "$log" | tail -6 >&2
  exit 1
fi
