#!/usr/bin/env bash
# ============================================================================
#  lib_alert.sh — shared Discord alerting for the monitors  (ticket P2-T10)
# ============================================================================
#  Source this, then call:  nc_alert "message"
#  It ALWAYS logs "[ALERT] …" to stderr (so cron mail / journald captures it and
#  the acceptance test can assert on it) and, when DISCORD_WEBHOOK is set, posts
#  the same message to Discord. A Discord hiccup never fails the caller.
# ============================================================================

# JSON-escape a string safely (message may contain quotes / emoji).
nc_json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

nc_alert() {
  local msg="$1"
  echo "[ALERT] $msg" >&2
  if [ -n "${DISCORD_WEBHOOK:-}" ]; then
    if ! curl -fsS -X POST "$DISCORD_WEBHOOK" \
         -H 'Content-Type: application/json' \
         -d "{\"content\": $(nc_json_escape "$msg")}" >/dev/null 2>&1; then
      echo "[WARN] Discord post failed (alert still logged above)" >&2
    fi
  fi
}
