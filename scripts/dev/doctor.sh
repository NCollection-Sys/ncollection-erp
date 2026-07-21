#!/usr/bin/env bash
# ============================================================================
#  make doctor — answer "why doesn't this work on my machine?" in one command
# ============================================================================
#  Read-only. Checks the things that actually bite during setup, and prints the
#  exact command to fix each one. Exits non-zero only on hard blockers, so it is
#  safe to run any time.
# ============================================================================
set -uo pipefail
# No `set -e` here (a failing check must not abort the report), so guard the cd
# explicitly — every later path is relative to the repo root.
cd "$(dirname "$0")/../.." || exit 1

blockers=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n     fix: %s\n' "$1" "$2"; }
bad()  { printf '  \033[31m✗\033[0m %s\n     fix: %s\n' "$1" "$2"; blockers=$((blockers + 1)); }

echo "NCollection ERP — environment check"
echo

# --- 1. Docker ---------------------------------------------------------------
if docker info >/dev/null 2>&1; then
  ok "docker daemon is running"
else
  bad "docker daemon is NOT running" "start Docker Desktop"
fi

# --- 2. OCA tree (generated; the stack will not boot without it) -------------
if [ -d oca/mis-builder ]; then
  ok "./oca aggregated"
else
  bad "./oca is missing or empty (base compose mounts it)" "make oca"
fi

# --- 3. Stack containers -----------------------------------------------------
running="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c '^ncollection-' || true)"
if [ "${running:-0}" -ge 3 ]; then
  ok "stack containers up ($running running)"
else
  warn "stack looks down (only ${running:-0} ncollection-* containers)" "make routing-up"
fi

# --- 4. Git hooks ------------------------------------------------------------
if [ "$(git config core.hooksPath 2>/dev/null)" = ".githooks" ]; then
  ok "git hooks enabled (pre-push gates active)"
else
  warn "git hooks NOT enabled — pushes skip the fast local gates" "make hooks-install"
fi

# --- 5. E2E deps -------------------------------------------------------------
if [ -d e2e/node_modules ]; then
  ok "e2e node dependencies installed"
else
  warn "e2e/node_modules missing" "cd e2e && npm ci && npx playwright install chromium"
fi

# --- 6. Fixture hostname resolution -----------------------------------------
# Both suites address tenants by subdomain; without resolution every browser
# journey fails in a confusing way.
unresolved=""
for host in clienta.localhost e2eclienta.localhost; do
  getent hosts "$host" >/dev/null 2>&1 || ping -c1 -W1 "$host" >/dev/null 2>&1 || unresolved="$unresolved $host"
done
if [ -z "$unresolved" ]; then
  ok "*.localhost fixture hostnames resolve"
else
  warn "these do not resolve:$unresolved" \
       "add to /etc/hosts: 127.0.0.1 clienta.localhost clientb.localhost admin.localhost e2eclienta.localhost e2eclientb.localhost e2eadmin.localhost"
fi

# --- 7. Lint tooling (used by the pre-push hook and CI) ----------------------
if python3 -m flake8 --version >/dev/null 2>&1 || command -v flake8 >/dev/null 2>&1 \
   || compgen -G "$HOME/Library/Python/*/bin/flake8" >/dev/null; then
  ok "flake8 available"
else
  warn "flake8 not found (pre-push will skip it; CI still enforces)" "pip install flake8"
fi

if command -v shellcheck >/dev/null 2>&1; then
  ok "shellcheck available"
else
  warn "shellcheck not found (pre-push will skip it; CI still enforces)" "brew install shellcheck"
fi

# --- 8. Stale module dependencies (REGRESSIONS.md R-012) --------------------
# Odoo does NOT retroactively install newly-declared dependencies into existing
# databases — only an upgrade does. So when a module's `depends` grows, every
# database created before that change silently keeps the old dependency set and
# the module stops loading. The only signal is one line at startup:
#
#   ERROR odoo.modules.loading: Some modules are not loaded ... ['x']
#
# which is trivially missed. This asks Postgres directly, per database, and
# prints the exact command that repairs each hit.
# Container id comes from compose, never a hardcoded name — a non-default
# COMPOSE_PROJECT_NAME would otherwise silently skip this whole check.
db_cid="$(docker compose -f docker-compose.yml -f docker-compose.dev.yml ps -q db 2>/dev/null)"
if [ -n "$db_cid" ]; then
  stale_total=0
  dbs="$(docker exec "$db_cid" psql -U odoo -d postgres -tAc \
        "SELECT datname FROM pg_database WHERE datistemplate=false AND datname <> 'postgres' ORDER BY 1" 2>/dev/null)"
  for db in $dbs; do
    # An installed module whose declared dependency is not itself installed.
    rows="$(docker exec "$db_cid" psql -U odoo -d "$db" -tAc "
      SELECT m.name || ' -> ' || d.name
      FROM ir_module_module m
      JOIN ir_module_module_dependency d ON d.module_id = m.id
      JOIN ir_module_module dep ON dep.name = d.name
      WHERE m.state = 'installed' AND dep.state <> 'installed'
      ORDER BY 1" 2>/dev/null)"
    for row in $(printf '%s\n' "$rows" | tr -d ' ' | grep -v '^$'); do
      mod="${row%%->*}"
      missing="${row##*->}"
      bad "db '$db': '$mod' is installed but its dependency '$missing' is NOT" \
          "make upgrade m=$mod db=$db"
      stale_total=$((stale_total + 1))
    done
  done
  if [ "$stale_total" -eq 0 ]; then
    ok "no stale module dependencies in any database"
  fi
else
  warn "database container not running — skipped the stale-dependency scan" "make routing-up"
fi

echo
if [ "$blockers" -gt 0 ]; then
  echo "✗ $blockers blocker(s) — fix those first."
  exit 1
fi
echo "✓ no blockers. Run 'make verify-all' to prove every suite still passes."
