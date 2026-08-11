#!/usr/bin/env bash
# =============================================================================
# assert_modules_settled.sh <db> <what>
#
# Refuse to install/upgrade into a database that is ALREADY in a broken state.
#
# WHY THIS EXISTS (#388). assert_odoo_setup.sh reads odoo's setup log for
# "Some modules are not loaded" / "inconsistent states" / "not installable,
# skipped". Those three markers are DATABASE-WIDE, not per-module. When odoo
# runs with -i/-u it builds its graph from EVERY installed module on that
# database, with no filter on the names you passed (odoo/modules/loading.py):
#
#     env.cr.execute("SELECT name from ir_module_module WHERE state IN %s",
#                    [('installed', 'to upgrade', 'to remove', 'to install')])
#
# So a module this command never mentioned can trip the guard. The cost lands
# on the PERSISTENT platform databases — CLAUDE.md lists saastest and
# ncplatform as "do not drop", so they are the ones that accumulate history.
# One module left stuck at 'to install' there would make every later
# `-u ncollection_saas` refuse, forever, on a perfectly healthy upgrade, with a
# message pointing at the wrong module. That is the most expensive thing a
# guard can do: abort work that is fine.
#
# This runs BEFORE the upgrade and names the real problem. It does not weaken
# assert_odoo_setup.sh — nothing is suppressed. It changes an accurate refusal
# with a misleading CAUSE into an accurate refusal with the right one.
#
# WHAT IT DOES NOT DO.
#  * It does not make the markers per-module. A module that goes bad DURING
#    this run for unrelated reasons still reports through assert_odoo_setup.sh
#    with the same ambiguity. This covers the pre-existing case, which is the
#    one that actually accumulates on a long-lived database.
#  * It does not refuse on a transitional row that is merely PRESENT — see the
#    age window below. A healthy concurrent upgrade produces exactly those rows,
#    so refusing on presence would false-positive on the shared stack.
#  * Consequently a genuinely broken row is invisible for its first 30 minutes.
#    That is the deliberate trade: a missed refusal costs one confusing run of
#    assert_odoo_setup.sh, and the next invocation catches it; a false refusal
#    blocks healthy work and sends someone to repair rows that are fine.
#  * It does not serialise access to PLATFORM_DB. Two concurrent runs against
#    saastest remain possible and are still a bad idea — this only ensures they
#    are not misreported as corruption.
#
# Exit 0 = nothing is stuck (including "database does not exist yet", "no module
# table yet", and "transitional rows exist but are fresh").
# Exit 1 = something has been stuck past the window, or the state could not be
# determined.
# =============================================================================
set -euo pipefail

db="${1:?usage: assert_modules_settled.sh <db> <what>}"
what="${2:?usage: assert_modules_settled.sh <db> <what>}"

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# Rule 11: derive the container, never hardcode `ncollection-db` — that breaks
# under a non-default COMPOSE_PROJECT_NAME (R-006).
if ! cid="$(docker compose ps -q db 2>/dev/null)" || [ -z "$cid" ]; then
  echo "REFUSING: no running 'db' service, so '$what' could not be verified." >&2
  echo "  Start the stack first (make up), or check COMPOSE_FILE." >&2
  exit 1
fi

psql_q() { docker compose exec -T db psql -U odoo -d "$1" -tAc "$2" 2>/dev/null; }

# A database that does not exist yet cannot hold a stuck module. This is the
# normal path for every throwaway fixture (prov*, fintest, upgr*), which are
# dropped and recreated per run.
if ! exists="$(psql_q postgres "SELECT 1 FROM pg_database WHERE datname='$db'")"; then
  echo "REFUSING: could not query postgres to see whether '$db' exists." >&2
  echo "  Not proceeding — an unverifiable precondition is not a satisfied one." >&2
  exit 1
fi
[ "$exists" = "1" ] || exit 0

# Present but not yet an Odoo database (createdb ran, odoo has not). Also
# settled: there are no modules to be stuck.
if ! tbl="$(psql_q "$db" "SELECT to_regclass('public.ir_module_module')")"; then
  echo "REFUSING: could not query '$db' for its module table." >&2
  exit 1
fi
[ -n "$tbl" ] || exit 0

# The transitional states. 'installed' and 'uninstalled' are settled; so is
# 'uninstallable', which is NOT in the state list odoo selects for the graph
# (measured: saastest and ncplatform each carry 29 such rows and they have
# never tripped anything).
# AGE MATTERS — a transitional row is NOT on its own evidence of a problem.
#
# Odoo marks a module AND its whole dependency subtree 'to upgrade'/'to install'
# up front (button_upgrade/button_install in loading.py STEP 2), then loads the
# graph one package at a time and commits after EACH package
# (module.env.cr.commit(), loading.py:273). That commit also durably publishes
# the STEP-2 writes for every module still queued. So for the entire duration
# of a healthy `odoo -u ncollection_saas`, any other Postgres session sees rows
# in exactly these states. That is normal, not abandonment.
#
# The first version of this guard refused on presence alone. On the shared
# stack, PLATFORM_DB defaults to saastest for BOTH verify_provisioning.sh and
# verify_config_sync.sh, and nothing serialises them — so two agents running
# `make verify-all` concurrently would have produced a confident, wrong
# "modules stuck, repair the rows" against a perfectly healthy database. That
# is R-018's exact shape, which this repo has already been burned by twice.
# Caught in review before it shipped.
#
# So: refuse only on rows that have sat untouched longer than a full upgrade
# could plausibly take. A real `-u` on these databases takes 1-3 minutes; an
# abandoned row persists indefinitely. 30 minutes separates them with a wide
# margin. Override with NC_SETTLED_MAX_AGE_MIN when a legitimately longer
# migration is expected.
max_age_min="${NC_SETTLED_MAX_AGE_MIN:-30}"

if ! stuck="$(psql_q "$db" \
      "SELECT name || ' (' || state || ', untouched for ' \
              || date_trunc('second', now() - write_date) || ')' \
         FROM ir_module_module \
        WHERE state IN ('to install','to upgrade','to remove') \
          AND write_date < now() - interval '$max_age_min minutes' \
        ORDER BY name")"; then
  echo "REFUSING: could not read module states from '$db'." >&2
  exit 1
fi

# Fresh transitional rows are reported, never refused on: they are what a
# concurrent healthy upgrade looks like. Saying so out loud beats leaving the
# next person to wonder why the guard stayed quiet.
if ! inflight="$(psql_q "$db" \
      "SELECT count(*) FROM ir_module_module \
        WHERE state IN ('to install','to upgrade','to remove') \
          AND write_date >= now() - interval '$max_age_min minutes'")"; then
  echo "REFUSING: could not read module states from '$db'." >&2
  exit 1
fi
if [ -n "$inflight" ] && [ "$inflight" != "0" ]; then
  echo "  note: $inflight module(s) on '$db' are mid-transition but were touched" \
       "within ${max_age_min}m — treating as an upgrade in flight, not as stuck."
fi

if [ -n "$stuck" ]; then
  echo "REFUSING: '$db' has modules stuck mid-transition BEFORE $what started." >&2
  while IFS= read -r line; do echo "    $line" >&2; done <<<"$stuck"
  echo "" >&2
  echo "  This is a state problem in the database, NOT a failure of the module" >&2
  echo "  being installed. Odoo builds its graph from every installed module on" >&2
  echo "  the database, so these rows would make the setup check refuse and" >&2
  echo "  blame the wrong module (#388)." >&2
  echo "" >&2
  echo "  These have been untouched for over ${max_age_min}m, so this is not a" >&2
  echo "  concurrent upgrade in flight. If '$db' is a throwaway fixture, drop" >&2
  echo "  and rebuild it. If it is a PERSISTENT platform DB (saastest," >&2
  echo "  ncplatform — CLAUDE.md says do not" >&2
  echo "  drop), repair the rows above instead: finish or roll back whatever" >&2
  echo "  left them mid-transition." >&2
  exit 1
fi
