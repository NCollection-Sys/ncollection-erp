#!/usr/bin/env bash
# ============================================================================
#  List databases on the shared dev server that no documented suite owns (#337)
# ============================================================================
#  READ-ONLY. It prints; it never drops anything, and it never will — every
#  name on that server is somebody's fixture until a human says otherwise, and
#  dropping another suite's fixture is REGRESSIONS.md R-004 (suites used to
#  share a namespace, so running one silently destroyed another's setup).
#
#  Why this exists: #337 found 89 databases on the shared server, and the dev
#  Odoo was running the crons of every one of them once a minute — real
#  fleet-migration, mail-queue and notification logic against months-old
#  fixtures. Turning cron off is the fix; this is how you find the fixtures
#  themselves, which are still there taking up space and confusing greps.
#
#  Ownership comes from the table in CLAUDE.md ("Test fixture ownership"). Keep
#  the two in step: a suite that gains a namespace and is not added here starts
#  reporting its own fixtures as orphans, which trains people to ignore output.
#
#  Usage:  make orphan-dbs
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

DB_USER="${DB_USER:-odoo}"
# Pin the file set explicitly, as every other script here does. It works without
# the flags today (compose auto-loads docker-compose.yml from cwd and the `db`
# service is identical either way), but an explicitly-set COMPOSE_PROJECT_NAME
# would make the bare form resolve somewhere else.
DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

# Databases with a documented owner. Anchored ERE, matched against the whole
# name, so `rtclienta` is owned but `rtclientaX` is not silently swallowed.
OWNED_RE='^(postgres|ncollection|ncplatform|albarari'\
'|rtclienta|rtclientb|rtadmin'\
'|e2eclienta|e2eclientb|e2eadmin'\
'|prov[a-z0-9]*'\
'|loadtesta|loadtestb|loadtestc'\
'|fintest|saastest|aggbench|cronstall|cronscopeplatform|cronscopetenant'\
'|nctest|upgrgreen|upgrred)$'

if [ -z "$("${DC[@]}" ps -q db 2>/dev/null)" ]; then
  echo "REFUSING: the 'db' service is not running. Start it first: make up" >&2
  exit 1
fi

# Names AND sizes in ONE query. Sizing them individually inside a `while read`
# loop looked tidier and was wrong: `docker compose exec -T` reads stdin, so the
# first iteration swallowed the rest of the loop's input and only one row ever
# printed. Asking the database once is both correct and cheaper.
#
# `-d postgres` is REQUIRED: psql defaults the target database to the USERNAME,
# and no database named "odoo" exists here (CLAUDE.md rule 9 / R-003).
rows="$("${DC[@]}" exec -T db psql -U "$DB_USER" -d postgres -tAc \
  "SELECT datname, pg_size_pretty(pg_database_size(datname))
     FROM pg_database WHERE datistemplate = false ORDER BY datname")"

total="$(printf '%s\n' "$rows" | grep -c . || true)"
# psql -tA separates columns with '|'; awk's regex flavour is ERE, same as the
# pattern above, so OWNED_RE is reused verbatim rather than maintained twice.
orphans="$(printf '%s\n' "$rows" | awk -F'|' -v re="$OWNED_RE" 'NF && $1 !~ re')"
count="$(printf '%s\n' "$orphans" | grep -c . || true)"

echo "======================================================================"
echo " Databases with no documented owner — ${count} of ${total}"
echo "======================================================================"

if [ "$count" -eq 0 ]; then
  echo "  none. Every database maps to a suite in CLAUDE.md's ownership table."
  exit 0
fi

# Size matters for the decision: a 9 MB skeleton and a 400 MB tenant are
# different calls.
printf '%s\n' "$orphans" | awk -F'|' '{ printf "  %-28s %s\n", $1, $2 }'

cat <<EOF

  These are LISTED, not judged. Some may be in active use by work in flight.
  Drop the ones you recognise, one at a time and deliberately:

      make dropdb db=<name>

  If any of these belongs to a suite, add its namespace to OWNED_RE in
  scripts/dev/orphan_dbs.sh AND to the fixture-ownership table in CLAUDE.md —
  a list that cries wolf is a list people stop reading.
EOF
