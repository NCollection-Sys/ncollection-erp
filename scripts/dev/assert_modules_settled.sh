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
# WHAT IT DOES NOT DO. It does not make the markers per-module. A module that
# goes bad DURING this run for unrelated reasons still reports through
# assert_odoo_setup.sh with the same ambiguity. This covers the pre-existing
# case, which is the one that actually accumulates on a long-lived database.
#
# Exit 0 = no module is mid-transition (including "database does not exist yet"
# and "database has no modules yet" — both are legitimately settled).
# Exit 1 = something is stuck, or the state could not be determined.
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
if ! stuck="$(psql_q "$db" \
      "SELECT name || ' (' || state || ')' FROM ir_module_module \
        WHERE state IN ('to install','to upgrade','to remove') ORDER BY name")"; then
  echo "REFUSING: could not read module states from '$db'." >&2
  exit 1
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
  echo "  If '$db' is a throwaway fixture, drop and rebuild it. If it is a" >&2
  echo "  PERSISTENT platform DB (saastest, ncplatform — CLAUDE.md says do not" >&2
  echo "  drop), repair the rows above instead: finish or roll back whatever" >&2
  echo "  left them mid-transition." >&2
  exit 1
fi
