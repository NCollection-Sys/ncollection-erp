#!/usr/bin/env bash
# ============================================================================
#  pylint_gate.sh — the pylint-odoo gate, in ONE place (#267)
# ============================================================================
#  Called by BOTH:
#    .githooks/pre-push        (with --allow-missing)
#    .github/workflows/ci.yml  (lint job, no flag)
#
#  It used to live inline in ci.yml only, which made pylint-odoo the one gate a
#  developer could not run before pushing. Every finding it added was discovered
#  ~30s into CI, after a push and a wait — three tickets in one day paid that
#  toll (#219, #55, #264).
#
#  Copying the check into the hook would have duplicated FOUR things that can
#  drift independently: the baseline number, the pylint invocation, the
#  finding-count regex, and the comparison. So the whole gate moved here and
#  both callers invoke it. One definition — same reasoning as #243 and #221.
#
#  Usage:
#    scripts/ci/pylint_gate.sh                  # CI: missing pylint is an ERROR
#    scripts/ci/pylint_gate.sh --allow-missing  # hook: missing pylint SKIPS
#
#  Exit codes:
#    0  pass (count <= baseline)
#    1  findings exceed baseline
#    2  tool or plugin missing and --allow-missing was NOT given (CI: a broken
#       image), or the run could not be trusted (see the guards below)
#    3  SKIPPED — tool or plugin missing under --allow-missing
#
#  3 is distinct from 0 on purpose. The hook's run_gate helper swallows output
#  on success, so a skip that exited 0 would be reported as "ok" — a gate that
#  never ran, displayed as a gate that passed. That is the precise false-green
#  this repo keeps getting bitten by, so a skip gets its own code and the hook
#  renders it as "skipped".
# ============================================================================
set -uo pipefail

# ---------------------------------------------------------------------------
#  THE BASELINE
# ---------------------------------------------------------------------------
#  pylint-odoo reports pre-existing findings across the addons (missing
#  README.rst, superfluous/deprecated manifest keys, redundant `string=` attrs).
#  Rather than a blanket continue-on-error — which would hide a PR introducing
#  BRAND-NEW findings just as easily as it hides the old ones — the gate fails
#  only when the count goes ABOVE this number. New violations still fail; old
#  debt does not. Once the backlog is paid down, delete the baseline and the
#  comparison so any finding fails.
#
#  History (moved here from ci.yml by #267, where it had already split across
#  two comment sites — the value and its provenance now live together):
#    42 → 60 (2026-07-19): PR #132 merged main into develop, bringing the
#      demo-sprint addons (tenant_wizard, module, dashboard, provisioning_job,
#      mis_templates, demo_freshorigin) whose W8113/C8113 debt was never
#      counted — lint only runs on PRs, so the first PR after that merge
#      inherited the bill (PR #133). Paying it down is backlog, not per-PR work.
#    60 → 61 (2026-07-27): F1-T01 (#109) adds ncollection_account_core. Its
#      manifest trips the one unavoidable manifest-required-author (C8101) that
#      every ncollection_* manifest has — we are proprietary, not OCA. The 3
#      superfluous manifest-key findings were AVOIDED by omitting
#      installable/application/auto_install (all Odoo defaults), so +1 exactly.
#    61 → 62 (2026-07-27): F5-T01 (#126) adds
#      ncollection_account_localization_uae — same single C8101; +1.
#    62 → 55 (2026-07-28): #226 removes the dead, never-loaded
#      ncollection.tenant.wizard, paying down the 7 W8113/C8113 it carried.
#    55 → 56 (2026-07-30): P3-T07 (#47) adds ncollection_approvals — C8101; +1.
#    56 → 57 (2026-08-01): P3-T11 (#51) adds ncollection_data_import — C8101;
#      the 3 superfluous manifest-key findings were AVOIDED; +1.
#    57 → 54 (2026-08-02, #221): the 3 print-used findings in seed_tenant.py
#      were never debt. That script's STDOUT is the platform's only channel for
#      reading a subprocess result, so print IS the interface; it now carries a
#      file-level disable with that reason, as does the #221 re-key script.
#      Lowered rather than raised, so 3 units of unused slack cannot silently
#      absorb a future REAL finding.
#    54 → 55 (2026-08-04, #101/P10-T09): adds ncollection_reseller — the one
#      unavoidable manifest-required-author (C8101); the superfluous manifest
#      keys, missing README, redundant field strings and wizard-in-models
#      findings were all AVOIDED/cleaned up, so +1 exactly.
#    55 → 56 (2026-08-04, #119/F3-T01): adds ncollection_account_dashboard —
#      same single unavoidable C8101; README present, no superfluous manifest
#      keys, no redundant field strings, so +1 exactly.
# ---------------------------------------------------------------------------
PYLINT_BASELINE=56

# The scanned tree and the finding-count pattern. Both callers MUST use the
# same ones or the two environments would count differently — the subtlest way
# this gate could drift while still looking green.
PYLINT_TARGET="custom_addons/"
FINDING_RE=": [CWEF][0-9]{4}: "

allow_missing=0
[ "${1:-}" = "--allow-missing" ] && allow_missing=1

# `cd "" || exit 0` does NOT work: when git rev-parse fails the substitution is
# empty, and `cd ""` returns 0 in bash while leaving the cwd unchanged — so the
# fallback never fires and the script lints whatever happens to be here. Outside
# a repo that produced "1 findings (baseline 54)" and EXIT 0: a PASS having
# linted nothing. Resolve first, then check.
if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || [ -z "$repo_root" ]; then
  echo "pylint-odoo: not inside a git repository — refusing to guess a target." >&2
  exit 2
fi
cd "$repo_root" || { echo "pylint-odoo: cannot enter $repo_root" >&2; exit 2; }

# pylint is frequently a user-site install that is not on PATH (the same reason
# .githooks/pre-push probes for flake8), so try the plausible entry points
# before concluding it is absent.
find_pylint() {
  if python3 -m pylint --version >/dev/null 2>&1; then
    echo "python3 -m pylint"; return 0
  fi
  if command -v pylint >/dev/null 2>&1; then
    echo "pylint"; return 0
  fi
  local candidate
  for candidate in "$HOME"/Library/Python/*/bin/pylint; do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

if ! pylint_cmd="$(find_pylint)"; then
  if [ "$allow_missing" -eq 1 ]; then
    echo "not installed"
    exit 3
  fi
  echo "pylint-odoo: NOT INSTALLED — required here (CI installs it in the lint job)." >&2
  exit 2
fi

if ! log="$(mktemp)" || [ -z "$log" ]; then
  echo "pylint-odoo: mktemp failed — cannot capture output." >&2
  exit 2
fi
# shellcheck disable=SC2064  # expand $log now, at trap-set time, on purpose
trap "rm -f '$log'" EXIT

# `-d all -e odoolint` = disable everything, enable only the Odoo checks.
# shellcheck disable=SC2086  # intentional word-split: may be "python3 -m pylint"
$pylint_cmd --load-plugins=pylint_odoo -d all -e odoolint "$PYLINT_TARGET" > "$log" 2>&1
status=$?

# Did the plugin actually load? pylint_odoo is a separate package from pylint,
# and without it `-e odoolint` matches nothing: the run SUCCEEDS, reports ZERO
# odoolint findings, and would score as a clean pass — silently disabling this
# gate entirely.
#
# This reads the REAL run's own output rather than pre-flighting a separate
# probe. An earlier version asked `--list-msgs-enabled | grep C8101`; it worked
# under every local condition tried (same pylint 4.0.6 / pylint-odoo 10.0.8 as
# CI, with and without pipefail, repeated runs) yet false-negatived in CI and
# blocked the lint job. The cause was never reproduced — so the probe was
# replaced with something that cannot have that class of bug: it checks the
# thing we actually care about, in the run we actually performed, and needs no
# second invocation.
if grep -qE "bad-plugin-value|unknown-option-value" "$log"; then
  if [ "$allow_missing" -eq 1 ]; then
    echo "plugin pylint_odoo did not load"
    exit 3
  fi
  echo "pylint-odoo: the pylint_odoo plugin did not load, so '-e odoolint' matched" >&2
  echo "             nothing. Refusing to report a pass on a run that checked" >&2
  echo "             nothing. Install it:  pip install pylint-odoo" >&2
  grep -E "bad-plugin-value|unknown-option-value" "$log" >&2
  exit 2
fi

# Ordered AFTER the plugin check on purpose: a failed plugin load also exits 32,
# so checking status first would report it as a hard 'not a clean run' even in
# hook mode, where a missing plugin must degrade to a skip (#267 item 1).
# pylint's status is bit-encoded: 1 fatal, 2 error, 4 warning, 8 refactor,
# 16 convention, 32 usage error. A normal findings run here returns 28
# (4|8|16), so a non-zero status does NOT mean failure — but bit 1 (pylint
# itself died) and 32 (we invoked it wrongly) do, and BOTH can emit zero
# lines matching FINDING_RE, which would otherwise score as "0 findings" and
# PASS. Verified: a bad flag exits 32 with no matching output.
if [ $((status & 1)) -ne 0 ] || [ "$status" -eq 32 ]; then
  echo "pylint-odoo: pylint exited $status (fatal/usage) — NOT a clean run:" >&2
  cat "$log" >&2
  exit 2
fi

# A completely empty log means pylint never produced anything — a redirect that
# failed, a killed process. Trusting the grep then reports a confident zero.
if [ ! -s "$log" ]; then
  echo "pylint-odoo: no output captured — refusing to report a pass." >&2
  exit 2
fi

count="$(grep -cE "$FINDING_RE" "$log" || true)"

# `grep -c` on an unreadable file prints nothing, leaving count EMPTY rather
# than 0; `[ "" -gt N ]` then errors and, with -e off, execution falls through
# to the success path. That is a PASS with no lint behind it (R-005's shape).
case "$count" in
  ''|*[!0-9]*)
    echo "pylint-odoo: could not parse a finding count — refusing to pass." >&2
    exit 2 ;;
esac

if [ "$count" -gt "$PYLINT_BASELINE" ]; then
  echo "pylint-odoo: $count findings (baseline $PYLINT_BASELINE) — NEW findings introduced:" >&2
  # Show only the findings, not pylint's score banner — the developer needs the
  # lines they have to fix, not the whole report.
  grep -E "$FINDING_RE" "$log" >&2 || true
  exit 1
fi

# CI parity: the block this replaced piped through `tee`, so the full report
# streamed into the job log on every run and the standing debt could be read
# straight from CI. Keep that in CI mode; the hook stays terse (one line) since
# a developer can run this script directly.
if [ "$allow_missing" -eq 0 ]; then
  grep -E "$FINDING_RE" "$log" || true
fi
echo "pylint-odoo: $count findings (baseline $PYLINT_BASELINE)"
exit 0
