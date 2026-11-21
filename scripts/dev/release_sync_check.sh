#!/usr/bin/env bash
# release_sync_check.sh — READ-ONLY release drift check: local runtime, GitHub develop and EVERY production platform.
#
# Reports PASS / WARN / FAIL per check, then a one-line verdict. Never writes: no git checkout/fetch/reset, no
# module upgrade, no deployment, no database write (every SQL statement is a SELECT, run with
# default_transaction_read_only=on). Never prints secrets: it reads module states and git metadata only.
#
# Usage:
#   scripts/dev/release_sync_check.sh                 # local + GitHub
#   NC_PROD_SSH=root@<vps> NC_PROD_SSH_OPTS="-i ~/.ssh/<key> -o UserKnownHostsFile=~/.ssh/<known_hosts>" \
#     scripts/dev/release_sync_check.sh               # + production
#   scripts/dev/release_sync_check.sh --json          # machine-readable summary on the last line
#
# Environment:
#   NC_RELEASE        expected release commit (default: GitHub develop HEAD, read with `git ls-remote`)
#   NC_LOCAL_DBS      local databases to check (default: "ncollection awtar ncollection_demo")
#   NC_PROD_SSH       user@host of production; unset = production checks are skipped (reported as WARN)
#   NC_PROD_SSH_OPTS  extra ssh options (key, known_hosts)
#   NC_PROD_PLATFORMS space-separated <name>:<checkout>:<runner conf>:<compose project>:<platform db> (default: balancecloud + atlascore)
#   NC_LOCAL_PLATFORM_DB local platform database for the functional-parity check (default ncollection; "" = skip)
#
# Exit status: 0 = all PASS, 1 = at least one WARN (no FAIL), 2 = at least one FAIL.
# Docs: docs/markdown/RELEASE_SYNC_WORKFLOW.md
set -u

JSON=0
[ "${1:-}" = "--json" ] && JSON=1

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "FAIL not inside the git repository"; exit 2; }
cd "$ROOT" || exit 2
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: keep container paths as written

NPASS=0; NWARN=0; NFAIL=0; RESULTS=""
report() {  # report <PASS|WARN|FAIL> <check> <detail>
    printf '%-4s %-34s %s\n' "$1" "$2" "$3"
    case "$1" in PASS) NPASS=$((NPASS + 1));; WARN) NWARN=$((NWARN + 1));; FAIL) NFAIL=$((NFAIL + 1));; esac
    RESULTS="${RESULTS}{\"status\":\"$1\",\"check\":\"$2\",\"detail\":\"$(printf '%s' "$3" | tr -d '"\134')\"},"
}

# The SQL both sides run: our installed modules, their recorded version, and anything pending.
MODULE_SQL="SET default_transaction_read_only = on;
SELECT name || '|' || state || '|' || coalesce(latest_version, '') FROM ir_module_module
 WHERE name LIKE 'ncollection%' AND state <> 'uninstalled'
 UNION ALL
SELECT '__pending__|' || count(*) || '|' FROM ir_module_module WHERE state IN ('to install', 'to upgrade', 'to remove');"

manifest_version() {  # manifest_version <module>  (from the RELEASE commit, not the local checkout)
    # Comparing against the local files would let a local runtime that is behind hide a production database that is
    # behind too; the release is the reference for every environment.
    git show "$EXPECTED:custom_addons/$1/__manifest__.py" 2>/dev/null \
        | sed -n "s/^[[:space:]]*['\"]version['\"][[:space:]]*:[[:space:]]*['\"]\([^'\"]*\)['\"].*/\1/p" | head -1
}
in_release() { git cat-file -e "$EXPECTED:custom_addons/$1/__manifest__.py" 2>/dev/null; }

check_db_modules() {  # check_db_modules <label> <db> <query output>
    local label="$1" db="$2" out="$3" pending stale="" missing="" name state ver want
    if [ -z "$out" ]; then report FAIL "$label db $db" "unreadable (does it exist?)"; return; fi
    pending="$(printf '%s\n' "$out" | sed -n 's/^__pending__|\([0-9]*\)|.*/\1/p')"
    while IFS='|' read -r name state ver; do
        [ -z "$name" ] || [ "$name" = "__pending__" ] && continue
        [ "$state" = "installed" ] || continue
        if ! in_release "$name"; then missing="$missing $name"; continue; fi
        want="$(manifest_version "$name")"
        [ "$ver" = "$want" ] || stale="$stale $name($ver->$want)"
    done <<EOF
$out
EOF
    if [ "${pending:-0}" != "0" ]; then report FAIL "$label db $db" "$pending module(s) pending install/upgrade/remove"
    elif [ -n "$missing" ]; then report FAIL "$label db $db" "installed but absent from the release:$missing"
    elif [ -n "$stale" ]; then report FAIL "$label db $db" "upgrade pending (db->code):$stale"
    else report PASS "$label db $db" "all ncollection modules at manifest version, 0 pending"; fi
}

echo "== Release sync check ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

# ---- GitHub ------------------------------------------------------------------------------------------------------
GH_DEVELOP="$(git ls-remote origin refs/heads/develop 2>/dev/null | cut -f1)"
if [ -n "$GH_DEVELOP" ]; then report PASS "github develop" "$GH_DEVELOP"
else report FAIL "github develop" "could not read origin develop (network or credentials)"; fi
EXPECTED="${NC_RELEASE:-$GH_DEVELOP}"
if [ -z "$EXPECTED" ]; then echo "FAIL no expected release (set NC_RELEASE)"; exit 2; fi
if git cat-file -e "$EXPECTED^{commit}" 2>/dev/null; then
    EXPECTED="$(git rev-parse "$EXPECTED")"
    EXPECTED_TREE="$(git rev-parse "$EXPECTED^{tree}")"
    report PASS "expected release" "$EXPECTED tree $EXPECTED_TREE"
else
    EXPECTED_TREE=""
    report FAIL "expected release" "$EXPECTED is not in the local object store (git fetch origin develop first)"
fi
if [ -n "$GH_DEVELOP" ] && [ "$GH_DEVELOP" != "$EXPECTED" ]; then
    report WARN "github develop vs release" "develop is $GH_DEVELOP, checking against $EXPECTED"
fi
# A release commit can never name its own hash, so its own copy of the record cannot hold its row: the row is written
# by a LATER commit (or the release gets a tag at deploy time). Read the record from every place that may carry it, and
# accept only a table row whose first cell is the release, never a hash that merely appears in prose.
REC_DOC=docs/markdown/RELEASE_SYNC_WORKFLOW.md
SHORT="$(printf '%.7s' "$EXPECTED")"
record_has_row() {
    { cat "$REC_DOC" 2>/dev/null
      for ref in "$EXPECTED" "$GH_DEVELOP"; do [ -n "$ref" ] && git show "$ref:$REC_DOC" 2>/dev/null; done
    } | grep -Eq "^\| \`${SHORT}[0-9a-f]*\` \|"
}
RELEASE_TAG="$(git tag --points-at "$EXPECTED" 2>/dev/null | head -1)"
if [ -n "$RELEASE_TAG" ]; then
    report PASS "release metadata" "tag $RELEASE_TAG names $SHORT"
elif record_has_row; then
    report PASS "release metadata" "release-record row names $SHORT"
else
    report WARN "release metadata" "no tag and no release-record row for $SHORT"
fi

# ---- Local -------------------------------------------------------------------------------------------------------
LOCAL_HEAD="$(git rev-parse HEAD)"; LOCAL_TREE="$(git rev-parse 'HEAD^{tree}')"
if [ "$LOCAL_HEAD" = "$EXPECTED" ]; then report PASS "local commit" "$LOCAL_HEAD ($(git branch --show-current))"
elif git merge-base --is-ancestor "$LOCAL_HEAD" "$EXPECTED" 2>/dev/null; then
    report FAIL "local commit" "$LOCAL_HEAD is BEHIND the release ($(git rev-list --count "$LOCAL_HEAD..$EXPECTED") commits)"
else report FAIL "local commit" "$LOCAL_HEAD is not the release and not an ancestor of it (unreleased local work?)"; fi
if [ -n "$EXPECTED_TREE" ] && [ "$LOCAL_TREE" = "$EXPECTED_TREE" ]; then report PASS "local tree" "$LOCAL_TREE"
else report FAIL "local tree" "$LOCAL_TREE (release tree ${EXPECTED_TREE:-unknown})"; fi
DIRTY="$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"
if [ "$DIRTY" = "0" ]; then report PASS "local tracked changes" "none"
else report FAIL "local tracked changes" "$DIRTY modified tracked file(s): the runtime is not the release"; fi
UNTRACKED_CODE="$(git status --porcelain -- custom_addons config | grep -c '^??' || true)"
if [ "$UNTRACKED_CODE" = "0" ]; then report PASS "local untracked code/config" "none under custom_addons/ or config/"
else report WARN "local untracked code/config" "$UNTRACKED_CODE untracked path(s) under custom_addons/ or config/"; fi
for m in custom_addons/*/__manifest__.py; do
    mod="$(basename "$(dirname "$m")")"
    git cat-file -e "$EXPECTED:custom_addons/$mod/__manifest__.py" 2>/dev/null || { report WARN "local addon" "$mod is not in the release"; }
done

parity_report() {  # parity_report <label> <rc> <output> <severity on FAIL>
    local label="$1" rc="$2" out="$3" sev="$4" fails
    fails="$(printf '%s\n' "$out" | grep -E '^ +(FAIL|WARN) ' | sed -E 's/^ *(FAIL|WARN) *//' | head -5 | tr '\n' ';')"
    case "$rc" in
        0) report PASS "$label functional parity" "every tenant has its entitlement + same-plan peers' modules; views active" ;;
        1) report "$sev" "$label functional parity" "${fails%;}" ;;
        3) report WARN "$label functional parity" "${fails%;}" ;;
        *) report WARN "$label functional parity" "checker could not run (tenant_parity_check.sh rc=$rc)" ;;
    esac
}

DBC="$(docker compose ps -q db 2>/dev/null)"
if [ -z "$DBC" ]; then report WARN "local databases" "db service not running: database checks skipped"
else
    for db in ${NC_LOCAL_DBS:-ncollection awtar ncollection_demo}; do
        out="$(printf '%s\n' "$MODULE_SQL" | docker exec -i "$DBC" psql -U odoo -d "$db" -At -q 2>/dev/null)"
        check_db_modules local "$db" "$out"
    done
    LOCAL_PLATFORM_DB="${NC_LOCAL_PLATFORM_DB-ncollection}"
    LOCAL_ODOO="$(docker compose ps -q odoo 2>/dev/null)"
    if [ -n "$LOCAL_PLATFORM_DB" ] && [ -n "$LOCAL_ODOO" ]; then
        # WARN, not FAIL, locally: the dev platform's registry also names throwaway fixture tenants.
        rc=0
        out="$(bash "$ROOT/scripts/dev/tenant_parity_check.sh" "$LOCAL_ODOO" "$LOCAL_PLATFORM_DB" 2>&1)" || rc=$?
        parity_report local "$rc" "$out" WARN
    fi
fi

# ---- Production --------------------------------------------------------------------------------------------------
# ---- Production platforms ----------------------------------------------------------------------------------------
# Every SaaS installation on the production host, each with its own checkout, runner config and Postgres server:
#   <name>:<checkout>:<runner conf>:<compose project>:<platform db>
# Each platform is checked on its own; two platforms on different releases is drift, reported per platform.
PLATFORMS="${NC_PROD_PLATFORMS:-balancecloud:/opt/balancecloud:/etc/balancecloud/odoo.runner.conf:balancecloud:balancecloud atlascore:/opt/atlascore:/etc/atlascore/odoo.runner.conf:atlascore:atlas-core}"
check_platform() {  # check_platform <name> <checkout> <runner conf> <compose project> [platform db]
    local name="$1" pdir="$2" pconf="$3" project="$4" pdb="${5:-}" head tree dirty addons raddons dbc dbs db out oc rc
    head="$(prod "git -C $pdir rev-parse HEAD" 2>/dev/null)"
    tree="$(prod "git -C $pdir rev-parse 'HEAD^{tree}'" 2>/dev/null)"
    if [ -z "$head" ]; then report FAIL "$name" "cannot reach $NC_PROD_SSH or $pdir is not a git checkout"; return; fi
    if [ "$head" = "$EXPECTED" ]; then report PASS "$name commit" "$head"
    else report FAIL "$name commit" "$head (release $EXPECTED)"; fi
    if [ "$tree" = "$EXPECTED_TREE" ]; then report PASS "$name tree" "$tree"
    else report FAIL "$name tree" "$tree (release ${EXPECTED_TREE:-unknown})"; fi
    dirty="$(prod "git -C $pdir status --porcelain | wc -l" 2>/dev/null | tr -d ' ')"
    if [ "$dirty" = "0" ]; then report PASS "$name local changes" "none (no production-only code)"
    else report FAIL "$name local changes" "${dirty:-?} modified/untracked path(s) in $pdir"; fi
    addons="$(prod "grep -E '^addons_path' $pconf" 2>/dev/null)"
    raddons="$(git show "$EXPECTED:config/odoo.prod.conf" 2>/dev/null | grep -E '^addons_path')"
    if [ -z "$addons" ]; then report WARN "$name runner conf" "could not read addons_path from $pconf"
    elif [ "$addons" = "$raddons" ]; then report PASS "$name runner conf" "addons_path = config/odoo.prod.conf"
    else report FAIL "$name runner conf" "addons_path differs from config/odoo.prod.conf"; fi
    # the platform's OWN Postgres: never "the first db container" (two platforms share this host)
    dbc="$(prod "docker ps -q --filter label=com.docker.compose.project=$project --filter label=com.docker.compose.service=db" 2>/dev/null)"
    if [ -z "$dbc" ] || [ "$(printf '%s\n' "$dbc" | wc -l)" != 1 ]; then
        report FAIL "$name databases" "no single running db container for compose project $project"; return
    fi
    dbs="$(prod "docker exec $dbc psql -U odoo -d postgres -At -c \"SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1\"" 2>/dev/null)"
    [ -n "$dbs" ] || report FAIL "$name databases" "no database listed on $project's server"
    for db in $dbs; do
        out="$(printf '%s\n' "$MODULE_SQL" | prod "docker exec -i $dbc psql -U odoo -d '$db' -At -q" 2>/dev/null)"
        check_db_modules "$name" "$db" "$out"
    done
    # FUNCTIONAL PARITY (MODULE SYNC above is not enough: equal commits and versions said nothing about a module that
    # was never installed). Run from the platform's OWN checkout, inside its OWN odoo container. Read-only.
    [ -n "$pdb" ] || return 0
    oc="$(prod "docker ps -q --filter label=com.docker.compose.project=$project --filter label=com.docker.compose.service=odoo" 2>/dev/null)"
    if [ -z "$oc" ] || [ "$(printf '%s\n' "$oc" | wc -l)" != 1 ]; then
        report WARN "$name functional parity" "no single running odoo container for compose project $project"; return 0
    fi
    rc=0
    out="$(prod "bash $pdir/scripts/dev/tenant_parity_check.sh $oc '$pdb' /etc/odoo/odoo.server.conf" 2>&1)" || rc=$?
    parity_report "$name" "$rc" "$out" FAIL
}

if [ -z "${NC_PROD_SSH:-}" ]; then
    report WARN "production" "NC_PROD_SSH not set: production checks skipped"
else
    # shellcheck disable=SC2086  # NC_PROD_SSH_OPTS is a word list on purpose
    prod() { ssh -o BatchMode=yes ${NC_PROD_SSH_OPTS:-} "$NC_PROD_SSH" "$@"; }
    for spec in $PLATFORMS; do
        IFS=: read -r p_name p_dir p_conf p_project p_db <<<"$spec"
        check_platform "$p_name" "$p_dir" "$p_conf" "$p_project" "$p_db"
    done
fi

# ---- Verdict -----------------------------------------------------------------------------------------------------
if [ "$NFAIL" -gt 0 ]; then VERDICT="OUT OF SYNC"; RC=2
elif [ "$NWARN" -gt 0 ]; then VERDICT="IN SYNC WITH WARNINGS"; RC=1
else VERDICT="IN SYNC"; RC=0; fi
echo "== $VERDICT: $NPASS pass, $NWARN warn, $NFAIL fail (release $(printf '%.7s' "$EXPECTED"))"
if [ "$JSON" = 1 ]; then
    printf '{"verdict":"%s","release":"%s","pass":%d,"warn":%d,"fail":%d,"results":[%s]}\n' \
        "$VERDICT" "$EXPECTED" "$NPASS" "$NWARN" "$NFAIL" "${RESULTS%,}"
fi
exit "$RC"
