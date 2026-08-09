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

# `Some modules are not loaded` / `inconsistent states` are Odoo's own wording
# when a dependency is missing; the other two catch a setup that blew up without
# changing the exit status.
if grep -qiE "Some modules are not loaded|inconsistent states|Traceback \(most recent call last\)|CRITICAL" "$log"; then
  echo "REFUSING: $what did not actually load — odoo exited 0 but reported the" >&2
  echo "  module skipped, inconsistent, or a traceback. Continuing would measure" >&2
  echo "  something that cannot happen and report the SYMPTOM as a failure of" >&2
  echo "  whatever this suite tests." >&2
  [ -n "$hint" ] && echo "  Hint: $hint" >&2
  grep -iE "not loaded|inconsistent states|Traceback|CRITICAL" "$log" | tail -6 >&2
  exit 1
fi
