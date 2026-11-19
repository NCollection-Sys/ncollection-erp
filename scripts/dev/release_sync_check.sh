#!/usr/bin/env bash
# release_sync_check.sh — READ-ONLY three-way release drift check (local runtime / GitHub develop / production).
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
#   NC_PROD_DIR       production checkout (default /opt/balancecloud)
#   NC_PROD_DBS       production databases (default: every non-template database on the production server)
#   NC_PROD_RUNNER_CONF  runner config whose addons_path must equal config/odoo.prod.conf (default
#                     /etc/balancecloud/odoo.runner.conf)
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

manifest_version() {  # manifest_version <module>  (from the LOCAL checkout)
    sed -n "s/^[[:space:]]*['\"]version['\"][[:space:]]*:[[:space:]]*['\"]\([^'\"]*\)['\"].*/\1/p" \
        "custom_addons/$1/__manifest__.py" 2>/dev/null | head -1
}

check_db_modules() {  # check_db_modules <label> <db> <query output>
    local label="$1" db="$2" out="$3" pending stale="" missing="" name state ver want
    if [ -z "$out" ]; then report FAIL "$label db $db" "unreadable (does it exist?)"; return; fi
    pending="$(printf '%s\n' "$out" | sed -n 's/^__pending__|\([0-9]*\)|.*/\1/p')"
    while IFS='|' read -r name state ver; do
        [ -z "$name" ] || [ "$name" = "__pending__" ] && continue
        [ "$state" = "installed" ] || continue
        if [ ! -f "custom_addons/$name/__manifest__.py" ]; then missing="$missing $name"; continue; fi
        want="$(manifest_version "$name")"
        [ "$ver" = "$want" ] || stale="$stale $name($ver->$want)"
    done <<EOF
$out
EOF
    if [ "${pending:-0}" != "0" ]; then report FAIL "$label db $db" "$pending module(s) pending install/upgrade/remove"
    elif [ -n "$missing" ]; then report FAIL "$label db $db" "installed but absent from the checkout:$missing"
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

DBC="$(docker compose ps -q db 2>/dev/null)"
if [ -z "$DBC" ]; then report WARN "local databases" "db service not running: database checks skipped"
else
    for db in ${NC_LOCAL_DBS:-ncollection awtar ncollection_demo}; do
        out="$(printf '%s\n' "$MODULE_SQL" | docker exec -i "$DBC" psql -U odoo -d "$db" -At -q 2>/dev/null)"
        check_db_modules local "$db" "$out"
    done
fi

# ---- Production --------------------------------------------------------------------------------------------------
if [ -z "${NC_PROD_SSH:-}" ]; then
    report WARN "production" "NC_PROD_SSH not set: production checks skipped"
else
    PDIR="${NC_PROD_DIR:-/opt/balancecloud}"; PCONF="${NC_PROD_RUNNER_CONF:-/etc/balancecloud/odoo.runner.conf}"
    # shellcheck disable=SC2086  # NC_PROD_SSH_OPTS is a word list on purpose
    prod() { ssh -o BatchMode=yes ${NC_PROD_SSH_OPTS:-} "$NC_PROD_SSH" "$@"; }
    P_HEAD="$(prod "git -C $PDIR rev-parse HEAD" 2>/dev/null)"
    P_TREE="$(prod "git -C $PDIR rev-parse 'HEAD^{tree}'" 2>/dev/null)"
    if [ -z "$P_HEAD" ]; then report FAIL "production" "cannot reach $NC_PROD_SSH or $PDIR is not a git checkout"
    else
        if [ "$P_HEAD" = "$EXPECTED" ]; then report PASS "production commit" "$P_HEAD"
        else report FAIL "production commit" "$P_HEAD (release $EXPECTED)"; fi
        if [ "$P_TREE" = "$EXPECTED_TREE" ]; then report PASS "production tree" "$P_TREE"
        else report FAIL "production tree" "$P_TREE (release ${EXPECTED_TREE:-unknown})"; fi
        P_DIRTY="$(prod "git -C $PDIR status --porcelain | wc -l" 2>/dev/null | tr -d ' ')"
        if [ "$P_DIRTY" = "0" ]; then report PASS "production local changes" "none (no production-only code)"
        else report FAIL "production local changes" "${P_DIRTY:-?} modified/untracked path(s) in $PDIR"; fi
        P_ADDONS="$(prod "grep -E '^addons_path' $PCONF" 2>/dev/null)"
        R_ADDONS="$(git show "$EXPECTED:config/odoo.prod.conf" 2>/dev/null | grep -E '^addons_path')"
        if [ -z "$P_ADDONS" ]; then report WARN "production runner conf" "could not read addons_path from $PCONF"
        elif [ "$P_ADDONS" = "$R_ADDONS" ]; then report PASS "production runner conf" "addons_path = config/odoo.prod.conf"
        else report FAIL "production runner conf" "addons_path differs from config/odoo.prod.conf"; fi
        P_DBC="$(prod "docker ps -q --filter label=com.docker.compose.service=db | head -1" 2>/dev/null)"
        P_DBS="${NC_PROD_DBS:-$(prod "docker exec $P_DBC psql -U odoo -d postgres -At -c \"SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1\"" 2>/dev/null)}"
        for db in $P_DBS; do
            out="$(printf '%s\n' "$MODULE_SQL" | prod "docker exec -i $P_DBC psql -U odoo -d $db -At -q" 2>/dev/null)"
            check_db_modules production "$db" "$out"
        done
    fi
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
