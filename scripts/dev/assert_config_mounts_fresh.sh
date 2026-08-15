#!/usr/bin/env bash
# ============================================================================
#  assert_config_mounts_fresh.sh — is the container reading the config file
#  that is actually on disk?
# ============================================================================
#  WHY THIS EXISTS (#427).
#
#  Config files are bind-mounted as SINGLE FILES:
#
#      - ./config/odoo.conf:/etc/odoo/odoo.conf:ro
#
#  A single-file bind mount resolves to one inode. `git merge`, `git checkout`,
#  `git pull` and most editors do not write in place — they write a temp file
#  and rename() it over the target, which creates a NEW inode. The container
#  keeps reading the old one, which is now unlinked.
#
#  Measured on this repo, with one `mv`:
#
#      host now contains the marker: 1
#      container sees the marker   : UNREADABLE
#
#  `ls -l /etc/odoo/` still lists the file, with a plausible size and date.
#  Only reading it fails.
#
#  NEITHER `make restart` NOR `up -d` RE-BINDS. Both were measured: restart
#  reuses the mount namespace, and `up -d` reports "Healthy" and does nothing
#  because no compose setting changed. Only `up -d --force-recreate` (or
#  down/up) re-resolves it — and `make oca` currently tells you to use
#  `make restart`, which is the one that does not work.
#
#  WHY THIS IS WORSE THAN A STALE CONFIG. Odoo does not fail when ODOO_RC
#  points at an unreadable file. It falls back to DEFAULTS, silently. Two of
#  the lost settings are load-bearing here:
#
#      addons_path   every OCA module becomes an "invalid module name" — a
#                    WARNING, then "Modules loaded.", then exit 0. That is the
#                    failure mode CLAUDE.md documents under "what a green check
#                    does NOT mean" and that assert_odoo_setup.sh (#385) exists
#                    for — but that guard reads a SETUP LOG. It cannot see a
#                    container that has been running for hours with a config it
#                    can no longer read.
#      db_filter     subdomain->database routing stops being enforced. A suite
#                    measuring routing or isolation against that container is
#                    measuring something else entirely, which is exactly the
#                    R-018 class of phantom finding.
#
#  WHY CONTENT AND NOT INODES. The obvious check — compare the host's inode to
#  the container's — does not work on this platform and would fail on every
#  healthy run. Measured on a HEALTHY mount: host 39390476, container 9. Docker
#  Desktop on macOS virtualises the filesystem (VirtioFS), so inode numbers are
#  not shared across the boundary. Content is, and a hash catches BOTH failure
#  modes: unreadable (stale inode) and readable-but-stale.
#
#  WHY docker inspect AND NOT THE COMPOSE FILES. Six compose files declare a
#  single-file config mount, and which of them are layered depends on how the
#  stack was started (dev, routing, saas, pooling, cronstall, cronscope). The
#  running container knows the truth; a compose file only knows an intention.
#  Same reasoning assert_oca_present.sh applies when it parses repos.yml rather
#  than hardcoding a directory name.
#
#  Read-only. Never restarts or recreates anything.
#
#  Usage: scripts/dev/assert_config_mounts_fresh.sh [service ...]
#         (default: every running compose service)
#  Exit 0 — every bind-mounted config file matches the file on disk.
#  Exit 1 — at least one does not, with the service, the path and the fix.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
repo_root="$PWD"

# sha256 of a file on the host. macOS ships `shasum`, Linux `sha256sum`; CI
# runs on Linux and developers here run macOS, so support both rather than
# discovering the difference in a failing pipeline.
host_sha() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" 2>/dev/null | cut -d' ' -f1
  else
    shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1
  fi
}

# --list prints `service|host-path|container-path` for every SINGLE-FILE config
# mount and exits. prove_config_mount_guard.sh consumes it so the proof always
# targets something that is actually vulnerable — its first version hardcoded
# config/odoo.conf and kept "passing" after that file moved to a directory
# mount and stopped being breakable.
list_only=0
if [ "${1:-}" = "--list" ]; then
  list_only=1
  shift
fi

services="$*"
if [ -z "$services" ]; then
  services="$(docker compose ps --services --filter status=running 2>/dev/null || true)"
fi

if [ -z "$services" ]; then
  # STDERR, not stdout. `--list` is a machine contract consumed by
  # prove_config_mount_guard.sh, and this sentence on stdout was parsed as a
  # record: `service` became "config-mounts: no running compose service…",
  # which then flowed into `docker compose up --force-recreate "$service"`.
  echo "config-mounts: no running compose service — nothing to check." >&2
  exit 0
fi

stale=0
checked=0

for service in $services; do
  cid="$(docker compose ps -q "$service" 2>/dev/null || true)"
  [ -n "$cid" ] || continue

  # Every bind mount whose SOURCE is a file inside this repo's config/.
  # `docker inspect` reports what the container actually has, which is the
  # whole point — a compose file would only report what someone intended.
  mounts="$(docker inspect -f \
    '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}{{end}}' \
    "$cid" 2>/dev/null || true)"

  while IFS='|' read -r source dest; do
    [ -n "${source:-}" ] && [ -n "${dest:-}" ] || continue
    # Docker Desktop reports SOME bind sources with a `/host_mnt` prefix and
    # others without it — measured on one container, in one inspect call:
    #     /host_mnt/<repo>/config/odoo.conf   -> /etc/odoo/odoo.conf
    #     /<repo>/custom_addons               -> /mnt/extra-addons
    # Matching the raw string therefore silently skipped the very file this
    # guard exists for, and the first run reported "fresh (0 file(s))".
    host_path="${source#/host_mnt}"
    case "$host_path" in
      "$repo_root"/config/*) ;;
      *) continue ;;
    esac
    [ -f "$host_path" ] || continue

    checked=$((checked + 1))
    if [ "$list_only" -eq 1 ]; then
      echo "$service|$host_path|$dest"
      continue
    fi
    want="$(host_sha "$host_path")"
    # Distinguish "no sha256sum in this image" from "cannot read the file".
    # Both produce empty output, and reporting a minimal image as STALE would
    # be a false alarm on a perfectly healthy stack — the one outcome worse
    # than having no guard, because it gets ignored and then deleted.
    if ! docker compose exec -T "$service" \
           sh -c 'command -v sha256sum >/dev/null 2>&1'; then
      echo "SKIPPED: $service has no sha256sum, so $dest cannot be compared."
      echo "  This is NOT a pass — the mount is simply unchecked. Add coreutils"
      echo "  to that image or teach this guard another digest."
      continue
    fi
    got="$(docker compose exec -T "$service" \
             sha256sum "$dest" 2>/dev/null | cut -d' ' -f1 | tr -d '\r' || true)"

    if [ -z "$got" ]; then
      echo "STALE: $service cannot read $dest"
      echo "  host file : ${host_path#"$repo_root"/}"
      echo "  container : unreadable — the mount points at an inode that no"
      echo "              longer exists (the file was replaced by rename)."
      stale=1
    elif [ "$want" != "$got" ]; then
      echo "STALE: $service is reading an OLD copy of $dest"
      echo "  host file : ${host_path#"$repo_root"/}  ($want)"
      echo "  container : $got"
      stale=1
    fi
  done <<EOF
$mounts
EOF
done

# A check that verified nothing must not report success. The tree is the
# source of truth for whether config mounts are EXPECTED: if a compose file
# declares one and the containers show none, this guard is broken, not the
# stack. Its first run reported "fresh (0 file(s))" because of the /host_mnt
# prefix above — exactly the silent pass that assert_oca_present.sh refuses for
# the same reason ("no './oca/<repo>:' targets found ... this check would
# silently verify nothing").
if [ "$list_only" -eq 1 ]; then
  exit 0
fi

if [ "$checked" -eq 0 ]; then
  # ANY ./config/... mount, at any depth, any extension. The first version
  # required a .conf directly under config/, so it could not see 4 of the 9
  # real mounts in this repo (pgbackrest.conf, pgbouncer.ini, userlist.txt,
  # postgresql.conf — all in subdirectories). A vacuity check that cannot see
  # the thing it is checking for is the defect it exists to prevent.
  declared="$(grep -lE '^\s*-\s*\./config/[A-Za-z0-9_./-]+:' \
                docker-compose*.yml 2>/dev/null | head -1 || true)"
  if [ -n "$declared" ]; then
    echo "REFUSING: $declared declares a single-file config bind mount, but no" >&2
    echo "  running container reports one. This check would verify nothing." >&2
    echo "  Either the stack is started in a way this guard does not understand," >&2
    echo "  or docker inspect's mount shape changed. Fix the guard, do not" >&2
    echo "  ignore it — a silent pass here is worse than no check at all." >&2
    exit 1
  fi
fi

if [ "$stale" -ne 0 ]; then
  cat >&2 <<'MSG'

  WHY THIS MATTERS. Odoo does not fail on an unreadable ODOO_RC — it falls
  back to DEFAULTS. You lose `addons_path` (every OCA module becomes an
  "invalid module name": a WARNING, then "Modules loaded.", then exit 0) and
  `db_filter` (subdomain->database routing stops being enforced). Any suite you
  run right now may be measuring something other than what it claims.

  Fix:  docker compose up -d --force-recreate <service>

  `make restart` will NOT fix it — restarting reuses the mount namespace. Nor
  will `up -d`, which reports "Healthy" and changes nothing because no compose
  setting changed. Both were measured; see #427.
MSG
  exit 1
fi

# A DIRECTORY-mounted config is not counted here, and that is not a coverage
# gap — it is the fix. A directory bind mount is re-resolved per lookup, so a
# rename-replace inside it is picked up immediately (measured). Only SINGLE-FILE
# mounts can go stale, so only those need watching. The odoo configs moved to a
# directory mount in #427; what remains is postgres/pgbackrest, where a
# directory mount would shadow the image's own /etc/postgresql and is therefore
# the wrong trade.
echo "config-mounts: fresh ($checked single-file config mount(s) match the tree;"
echo "  directory-mounted configs are immune by construction and not counted)."
exit 0
