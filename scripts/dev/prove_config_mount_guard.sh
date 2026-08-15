#!/usr/bin/env bash
# ============================================================================
#  prove_config_mount_guard.sh — RED-prove assert_config_mounts_fresh.sh
# ============================================================================
#  WHY THIS IS A MANUAL PROOF AND NOT A CI GATE.
#
#  Proving this guard means deliberately breaking a bind-mounted file and
#  recreating a container. CLAUDE.md Rule 14 / REGRESSIONS.md R-018 forbid
#  exactly that while other agents may be relying on the shared stack — it has
#  already produced two false CRITICALs in one session. So this cannot live in
#  `pre-push` or in CI, where it would fire concurrently with anything else.
#
#  It is a proof you RUN, before fanning out background work, when you want to
#  know the guard still discriminates. A guard nobody can re-prove decays into
#  decoration; one that races other agents is worse than decoration.
#
#  What it does, in order:
#    1. snapshot config/odoo.conf
#    2. replace it by RENAME (what git does) — the container keeps the old inode
#    3. assert the guard FAILS
#    4. restore the file and force-recreate the container
#    5. assert the guard PASSES
#
#  Usage: scripts/dev/prove_config_mount_guard.sh
#  Exit 0 — the guard failed when it should and passed when it should.
#  Exit 1 — it did not, which means it is not protecting anything.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

guard=./scripts/dev/assert_config_mounts_fresh.sh
snapshot="$(mktemp)"
compose_args="-f docker-compose.yml -f docker-compose.dev.yml"
[ -f docker-compose.routing.yml ] && \
  compose_args="$compose_args -f docker-compose.routing.yml"

# ASK THE GUARD what is vulnerable rather than assuming. The first version of
# this proof hardcoded config/odoo.conf and reported "the guard protects
# nothing" — correctly, because that file had just moved to a DIRECTORY mount
# and could no longer go stale. The proof was testing a fixed thing. One
# definition of "vulnerable" lives in the guard; this consumes it.
first="$($guard --list 2>/dev/null | head -1)"
if [ -z "$first" ]; then
  echo "No single-file config mount is left to break — every config is" >&2
  echo "  directory-mounted and immune by construction (#427). Nothing to" >&2
  echo "  prove, and nothing at risk." >&2
  exit 0
fi
service="${first%%|*}"
rest="${first#*|}"
target="${rest%%|*}"
target="${target#"$PWD"/}"
echo "proving against: service=$service file=$target"

# shellcheck disable=SC2329
# Invoked indirectly by the traps below; shellcheck cannot see that.
cleanup() {
  # A proof that leaves the stack broken has done more harm than the bug it was
  # checking for. Restore the file, then the container, and SAY SO if either
  # fails — the first version discarded the recreate's output and its exit
  # status, so a failed restore was indistinguishable from success and would
  # later surface as a STALE report nobody could attribute to an aborted proof.
  if [ -s "$snapshot" ]; then
    cp "$snapshot" "$target" || {
      echo "RESTORE FAILED: could not put $target back." >&2
      echo "  Recover with: git checkout -- $target" >&2
    }
  fi
  rm -f "$snapshot" "$snapshot.new"
  # shellcheck disable=SC2086
  if ! docker compose $compose_args up -d --force-recreate "$service" >/dev/null 2>&1; then
    echo "RESTORE FAILED: could not recreate '$service'." >&2
    echo "  The file is back but the container may still hold the stale mount." >&2
    echo "  Recover with: docker compose $compose_args up -d --force-recreate $service" >&2
  fi
  sleep 10
}
# EXIT alone is not enough. bash registers INT and TERM separately, and a
# supervisor that signals only this PID (rather than the foreground process
# group) leaves an EXIT-only trap waiting on whatever child is currently
# blocked — measured: `kill -INT <pid>` did not run the trap until the child
# finished. SIGKILL cannot be trapped by anything, which is why the recovery
# command is printed BEFORE the file is touched.
trap cleanup EXIT INT TERM

if [ -z "$(docker compose ps -q "$service" 2>/dev/null)" ]; then
  echo "REFUSING: $service is not running, so this proves nothing." >&2
  exit 1
fi

# The guard reports whatever the RUNNING stack mounts, which may come from an
# overlay this script cannot recreate with (saas, pooling, prod, cronstall,
# cronscope). Recreating with the wrong `-f` set either errors "no such
# service" or resolves a different definition — and the old cleanup discarded
# both outcomes. Refuse rather than pretend.
# shellcheck disable=SC2086
if ! docker compose $compose_args config --services 2>/dev/null | grep -qx "$service"; then
  echo "REFUSING: '$service' is not defined by the compose files this proof" >&2
  echo "  can recreate with ($compose_args)." >&2
  echo "  Run it against the plain dev stack, or extend compose_args." >&2
  exit 1
fi

# The file must be tracked and clean, so recovery is always one command — and
# that command is printed NOW, before anything is broken, because SIGKILL
# cannot be trapped and would otherwise leave no trail.
if ! git diff --quiet -- "$target" 2>/dev/null; then
  echo "REFUSING: $target already has uncommitted changes." >&2
  echo "  This proof rewrites it and restores from a snapshot; starting from a" >&2
  echo "  dirty file risks losing your edit." >&2
  exit 1
fi
echo "if this is interrupted with SIGKILL (which no trap can catch), recover with:"
echo "    git checkout -- $target"
echo "    docker compose $compose_args up -d --force-recreate $service"
echo

cp "$target" "$snapshot"

echo "== 1. baseline: the guard must PASS on a healthy stack =="
if ! "$guard"; then
  echo "FAIL: the guard already reports a problem — fix that first." >&2
  exit 1
fi

echo
echo "== 2. break it: replace $target by rename, as git does =="
{ cat "$target"; echo '# PROOF-MARKER'; } > "$snapshot.new"
mv "$snapshot.new" "$target"

echo
echo "== 3. the guard must FAIL =="
if "$guard"; then
  echo "FAIL: the guard PASSED against a stale mount — it protects nothing." >&2
  exit 1
fi
echo "  ok: guard refused, as it should"

echo
echo "== 4. restore + force-recreate =="
cp "$snapshot" "$target"
# shellcheck disable=SC2086
docker compose $compose_args up -d --force-recreate "$service" >/dev/null 2>&1
sleep 12

echo
echo "== 5. the guard must PASS again =="
if ! "$guard"; then
  echo "FAIL: the guard still refuses after a force-recreate — either the fix" >&2
  echo "  advice is wrong or the guard has a false positive." >&2
  exit 1
fi

echo
echo "PROVED: the guard fails on a stale mount and passes on a fresh one."
exit 0
