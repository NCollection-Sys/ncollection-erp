#!/usr/bin/env bash
# =============================================================================
# einvoice_stack_guard.sh <check|billing|sb> -f <file> [-f <file> ...]
#
# Refuse to attach the UAE e-invoicing validator to a stack the given Compose
# files do not describe.
#
# WHY THIS EXISTS. docker-compose.einvoice.yml is an OVERLAY on a running stack:
# it adds the validator services and, on `odoo`, three environment variables and
# the internal `nc_einvoice` network. Attaching it RECREATES odoo, and Compose
# rebuilds odoo from whatever files it is given. `make einvoice-validator-up`
# used to give it base + dev only. On the SaaS development stack (`make saas-up`:
# base + dev + routing + saas) that would have replaced the routing command
#
#     --db-filter=^%d$ --no-database-list --proxy-mode --max-cron-threads=0
#
# with the dev one (--dev=reload, crons from NC_DEV_CRON_THREADS, the database
# selector back on), and it also rebuilt the image and started the Self-Billing
# worker. Nothing reported it.
#
# WHAT IT PROVES, with Compose config HASHES only, never a render: the overlay
# interpolates the validator token, so `docker compose config` would print it.
#   * the files start with docker-compose.yml and end with the overlay;
#   * odoo is running: this never creates a stack, it only attaches to one;
#   * every running service of the project is in the selected files, and every
#     one except odoo and the validators has exactly the hash those files give
#     it, i.e. the selected files ARE the running stack (a missing overlay or a
#     different order changes a hash or leaves a service unaccounted for);
#   * odoo's running hash is the base stack's (not attached yet: recreating it
#     adds the overlay and nothing else) or the full set's (attached already:
#     Compose leaves it alone);
#   * to start anything, the validator image exists: starting never builds.
# Recreating the shared odoo also needs EINVOICE_RECREATE_ODOO=1 (Rule 14).
# Read-only: it runs `config --hash`, `ps`, `inspect` and `image inspect`.
# =============================================================================
set -euo pipefail

OVERLAY=docker-compose.einvoice.yml
IMAGE=nc-einvoice-validator:dev
MANAGED=" einvoice-validator einvoice-validator-sb "

mode=${1:-}
case "$mode" in
  check|billing|sb) shift ;;
  *) echo "usage: $0 <check|billing|sb> -f <file> [-f <file> ...]" >&2; exit 2 ;;
esac

refuse() { echo "❌ REFUSING (einvoice $mode): $*" >&2; exit 1; }

args=("$@")
names=()
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  if [ "${args[$i]}" != "-f" ] || [ $((i + 1)) -ge "${#args[@]}" ]; then
    refuse "expected only '-f <file>' pairs, got '${args[$i]}'"
  fi
  names+=("${args[$((i + 1))]}")
  i=$((i + 2))
done
[ "${#names[@]}" -ge 2 ] || refuse "expected the stack's files followed by $OVERLAY"
[ "${names[0]}" = docker-compose.yml ] || refuse "the first file must be docker-compose.yml, got '${names[0]}'"
[ "${names[-1]}" = "$OVERLAY" ] || refuse "the last file must be $OVERLAY, got '${names[-1]}'"
for name in "${names[@]:0:${#names[@]}-1}"; do
  [ "$name" != "$OVERLAY" ] || refuse "$OVERLAY may appear only once, last"
  [ -f "$name" ] || refuse "no such Compose file: $name"
done
base=("${args[@]:0:${#args[@]}-2}")

# "<service> <sha256>" per service. stderr is dropped: on a missing token Compose names the variable, but nothing here
# should ever echo configuration.
full=$(docker compose "${args[@]}" config --hash '*' 2>/dev/null) \
  || refuse "Compose cannot resolve the selected files (is NC_EINVOICE_VALIDATOR_TOKEN set in .env?)"
stack=$(docker compose "${base[@]}" config --hash '*' 2>/dev/null) \
  || refuse "Compose cannot resolve the stack files without the overlay"
[ -n "$full" ] && [ -n "$stack" ] || refuse "Compose returned no service hashes"
hash_of() { awk -v s="$1" '$1 == s { print $2 }' <<<"$2"; }

odoo_id=$(docker compose "${args[@]}" ps -q odoo 2>/dev/null) || true
[ -n "$odoo_id" ] || refuse "odoo is not running. This target only ATTACHES the validator to a running stack; start the stack itself first (make up / routing-up / saas-up)"
[ "$(wc -w <<<"$odoo_id")" -eq 1 ] || refuse "more than one odoo container is running"
project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$odoo_id")
[ -n "$project" ] || refuse "the odoo container carries no Compose project label"
running=$(docker ps --filter "label=com.docker.compose.project=$project" \
  --format '{{.Label "com.docker.compose.service"}} {{.Label "com.docker.compose.config-hash"}}')

odoo_state=""
billing_running=no
while read -r service hash; do
  [ -n "$service" ] || continue
  want=$(hash_of "$service" "$full")
  [ -n "$want" ] || refuse "'$service' is running in project '$project' but is not in the selected files, so they are not the running stack (provisioning-runner comes from docker-compose.saas.yml). Select the stack with EINVOICE_STACK_FILES"
  case "$MANAGED" in
    *" $service "*)
      [ "$service" != einvoice-validator ] || billing_running=yes
      continue
      ;;
  esac
  if [ "$service" = odoo ]; then
    if [ "$hash" = "$want" ]; then
      odoo_state=attached
    elif [ "$hash" = "$(hash_of odoo "$stack")" ]; then
      odoo_state=detached
    else
      refuse "the running odoo matches neither the selected stack nor that stack plus $OVERLAY: recreating it would change more than the overlay. Select the stack odoo runs with in EINVOICE_STACK_FILES (in the order it was started)"
    fi
  elif [ "$hash" != "$want" ]; then
    refuse "the running '$service' differs from what the selected files define, so they are not the running stack. Select it with EINVOICE_STACK_FILES (in the order it was started)"
  fi
done <<<"$running"
[ -n "$odoo_state" ] || refuse "odoo was not found among the running services of project '$project'"

image=present
docker image inspect "$IMAGE" >/dev/null 2>&1 || image=missing

case "$mode" in
  billing)
    [ "$image" = present ] || refuse "image $IMAGE is missing; build it explicitly: make einvoice-validator-build"
    if [ "$odoo_state" = detached ] && [ "${EINVOICE_RECREATE_ODOO:-}" != 1 ]; then
      refuse "attaching the validator RECREATES the shared odoo (the overlay only: three variables and the nc_einvoice network). Re-run with EINVOICE_RECREATE_ODOO=1 when nothing else depends on the stack (Rule 14)"
    fi
    ;;
  sb)
    [ "$image" = present ] || refuse "image $IMAGE is missing; build it explicitly: make einvoice-validator-build"
    [ "$odoo_state" = attached ] || refuse "odoo is not attached to the validators yet: run make einvoice-validator-up first"
    [ "$billing_running" = yes ] || refuse "the Billing validator is not running: run make einvoice-validator-up first"
    ;;
esac

echo "✅ einvoice $mode: the selected files are the running stack (project $project: ${names[*]})"
echo "   odoo: $odoo_state$([ "$odoo_state" = detached ] && echo ' (attaching recreates it with the overlay only)')"
echo "   Billing validator: $billing_running · image $IMAGE: $image"
