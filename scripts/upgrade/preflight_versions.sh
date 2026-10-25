#!/usr/bin/env bash
# ============================================================================
#  Release preflight (R-037): is any installed module of <db> behind the code that is about to serve it?
# ============================================================================
#  READ-ONLY and fail-closed: it opens no Odoo registry, writes nothing, upgrades nothing. Run it against a REAL
#  database after the new code is deployed and BEFORE traffic is pointed at it:
#
#      backup -> deploy code -> preflight (this) -> rehearse on a copy -> targeted upgrade -> preflight again -> traffic
#
#  Exit 0 = nothing behind. Exit 1 = at least one module is behind (it names them: upgrade exactly those, together,
#  never `-u all`). Exit 2 = it could not check (never a silent pass).
#
#  usage: scripts/upgrade/preflight_versions.sh <db> [<db> ...]
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."
[ "$#" -ge 1 ] || { echo "usage: $0 <db> [<db> ...]" >&2; exit 2; }

DC=(docker compose)
rc=0
for db in "$@"; do
  case "$db" in *[!a-zA-Z0-9_]*|"") echo "REFUSING: '$db' is not a plain database name" >&2; exit 2 ;; esac
  out=""
  # The connection comes from the container's own environment (HOST / USER / PASSWORD), so this works unchanged against a
  # production stack and never carries a credential in this file.
  # shellcheck disable=SC2016  # $1/$HOST/... expand in the CONTAINER's shell, not here
  out="$("${DC[@]}" exec -T odoo sh -c 'python3 - "$1" --env' _ "$db" < scripts/upgrade/preflight_versions.py 2>&1)" || true
  printf '%s\n' "$out" | grep -aE '^PREFLIGHT' | sed "s/^/  /" || true
  verdict="$(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT=//p' | head -1 | tr -d '\r')"
  case "$verdict" in
    PASS) echo "✅ $db: no installed module is behind the code" ;;
    FAIL) echo "❌ $db: behind the code. Upgrade exactly the modules named above, together, then run this again"; [ "$rc" -ge 1 ] || rc=1 ;;
    *)    echo "❌ $db: could not be checked ($verdict)"; rc=2 ;;
  esac
done
exit "$rc"
