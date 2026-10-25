#!/usr/bin/env bash
# shellcheck disable=SC2016  # single-quoted scripts expand in the CONTAINER's shell
# ============================================================================
#  Release plan (READ-ONLY): every database -> the modules it needs -> database version -> code version -> state
# ============================================================================
#  Deterministic and reproducible: the same code and the same databases print the same plan, sorted. It discovers the
#  platform (control-plane) database and every tenant registered in it (ncollection_tenant.database_name), plus any
#  database named on the command line, and for each one runs the read-only preflight (preflight_versions.py: no
#  registry, a read-only session) and reads the release-gate hold. It changes nothing and upgrades nothing.
#
#  STATE is what the release gate (ncollection_core/release_gate.py) will answer for that database under this code:
#    READY             nothing is behind and nothing holds it
#    UPGRADE_REQUIRED  the modules listed are behind: upgrade exactly those, together, in one run
#    HELD:<hold>       held by the release tooling / provisioning / an operator
#    ERROR             it could not be checked (unreachable, nothing checked, installed module not deployed)
#
#  usage: scripts/upgrade/release_plan.sh [extra_db ...]        PLATFORM_DB (default ncollection) is the control plane
#         RELEASE_PLAN_TENANTS=0 plans the platform (and named databases) only, not the tenants registered in it
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"
DC=(docker compose)
PLATFORM_DB="${PLATFORM_DB:-ncollection}"
in_db(){ "${DC[@]}" exec -T db sh -c 'export PGUSER="$POSTGRES_USER"; exec "$@"' _ "$@"; }
plain(){ case "$1" in *[!a-z0-9_]*|"") return 1 ;; esac; }
exists(){ [ "$(in_db psql -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname='$1'" | tr -d '[:space:]')" = "1" ]; }

plain "$PLATFORM_DB" || { echo "REFUSING: PLATFORM_DB '$PLATFORM_DB' is not a plain name" >&2; exit 2; }
targets="$PLATFORM_DB"$'\n'
if exists "$PLATFORM_DB" && [ "${RELEASE_PLAN_TENANTS:-1}" != "0" ]; then
  registered="$(in_db psql -d "$PLATFORM_DB" -Atc "SELECT database_name FROM ncollection_tenant WHERE coalesce(database_name, '') <> '' ORDER BY 1" 2>/dev/null || true)"
  targets+="$registered"$'\n'
fi
for extra in "$@"; do targets+="$extra"$'\n'; done
targets="$(printf '%s' "$targets" | tr -d '\r' | sed '/^$/d' | sort -u)"

printf '%-18s %-9s %-26s %-34s %-14s %s\n' DATABASE ROLE STATE MODULE DATABASE CODE
rc=0
for db in $targets; do
  role=named
  printf '%s\n' "${registered:-}" | grep -qx "$db" && role=tenant
  [ "$db" = "$PLATFORM_DB" ] && role=platform
  if ! plain "$db"; then printf '%-18s %-9s %-26s %s\n' "$db" "$role" ERROR "not a plain database name"; rc=2; continue; fi
  if ! exists "$db"; then printf '%-18s %-9s %-26s %s\n' "$db" "$role" ERROR "registered, but no such database"; rc=2; continue; fi
  out="$("${DC[@]}" exec -T odoo sh -c 'python3 - "$1" --env' _ "$db" < scripts/upgrade/preflight_versions.py 2>&1 || true)"
  verdict="$(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT=//p' | head -1 | tr -d '\r')"
  hold="$(in_db psql -d "$db" -Atc "SELECT split_part(value, ':', 1) FROM ir_config_parameter WHERE key = 'ncollection.release.hold'" 2>/dev/null | tr -d '[:space:]' || true)"
  case "$verdict" in
    PASS) state=READY ;;
    FAIL) state=UPGRADE_REQUIRED; [ "$rc" -ge 1 ] || rc=1 ;;
    *)    state=ERROR; rc=2 ;;
  esac
  [ -n "$hold" ] && state="$state/HELD:$hold"
  lines="$(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT_BEHIND=\([^ ]*\) database \([^ ]*\) < code \([^ ]*\)$/\1 \2 \3/p' | tr -d '\r' | sort)"
  missing="$(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT_MISSING=\([^ ]*\) .*/\1/p' | tr -d '\r' | sort)"
  if [ -z "$lines" ] && [ -z "$missing" ]; then
    printf '%-18s %-9s %-26s %-34s %-14s %s\n' "$db" "$role" "$state" "-" "-" "-"
  fi
  while read -r module dbv codev; do
    [ -n "$module" ] && printf '%-18s %-9s %-26s %-34s %-14s %s\n' "$db" "$role" "$state" "$module" "$dbv" "$codev"
  done <<<"$lines"
  while read -r module; do
    [ -n "$module" ] && printf '%-18s %-9s %-26s %-34s %-14s %s\n' "$db" "$role" "$state" "$module" "installed" "NOT DEPLOYED"
  done <<<"$missing"
  modules="$(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT_MODULES=//p' | head -1 | tr -d '\r')"
  [ -n "$modules" ] && printf '%-18s %-9s %-26s upgrade together, one run: %s\n' "$db" "$role" "$state" "$modules"
done
exit "$rc"
